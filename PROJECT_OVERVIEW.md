# Bonds Screener — Project Overview

**Scopo**: scaricare l'universo delle obbligazioni quotate su Borsa Italiana
(MOT/EuroMOT) tramite la pagina di ricerca avanzata, salvarle in un DB locale
SQLite, calcolare il **rendimento netto a scadenza (Net YTM)** post-tasse, e
mostrare il tutto in una webapp Flask con tabella filtrabile e grafici
interattivi.

**Stack**: Python 3, Selenium (Chrome headless) + BeautifulSoup per lo scrape,
SQLite per il persist, Flask + Plotly + DataTables per la UI. Tutto
self-contained nella cartella `bonds/`.

---

## Struttura dei file

```
bonds/
├── app.py            Flask + UI + export portabile in HTML
├── scraper.py        Selenium + AJAX hook + parser HTML + orchestratore
├── database.py       SQLite layer (schema, migrations, upsert/CRUD)
├── calculations.py   YTM bisection, tasse, classificazioni, anomalie
├── bonds.db          DB locale (auto-creato al primo run)
├── requirements.txt
├── start.bat         double-click → server avviato
├── debug/            script Selenium per diagnostica BI
└── tests/            unit test (DB + parser + calcoli + integrazione Flask)
```

---

## Flusso end-to-end

```
[Click "Aggiorna"]
  → POST /api/sync
  → thread che chiama run_scrape(DB)
  → SELENIUM apre BI ricerca-avanzata, applica filtri, paginazione,
    parsing HTML, upsert in SQLite
  → al termine: mark_stale_inactive() segna soft-purge bond non rivisti
  → SyncState aggiorna progress live (polling /api/sync/status)
  → /api/bonds restituisce JSON con bonds + chart + anomalie
  → Plotly renderizza in pagina + DataTables popola la tabella
```

---

## scraper.py — il cuore dello scrape

### Profili (76 totali)

Costruiti dal cartesiano di:

- **Tipologie BI** (6): `Titoli Di Stato Italiani`, `Titoli Di Stato Esteri`,
  `Eurobonds Republic Of Italy`, `Banche`, `Corporate`, `Secured`
- **Cedole** (2): `Titolo Con Cedole Tf` (fissa) e `Zero Coupon`
- **Paesi** (33, solo per "Esteri"): Italia, Francia, Germania... Stati Uniti,
  Cina, Filippine, Costa D Avorio, Jersey, ecc. — lista coerente con il
  dropdown Paese di Borsa Italiana.

Per ogni profilo lo scraper applica via JS sul `<select>` della pagina BI:

- **Struttura = Plain Vanilla** (esclude inflation-linked, index-linked,
  strutturate su tassi)
- **Tipologia = <iterata>**
- **Tipo Cedola = <iterata>**
- **Subordinazione = No** (esclude Tier1/Tier2/Sub bonds)
- **Paese = <iterato>** (solo Esteri)

`Rimborso Anticipato = No` viene **NOT applicato server-side** (BI ha un bug:
tratta NULL ≠ No, cancellando ogni sovereign). I callable vengono filtrati
client-side via `is_callable_from_name(name)` che cerca il token
"Call"/"Callable" nel nome.

### Page-size hook (workaround pagination)

Borsa Italiana cappa di default a 20 righe per pagina. La "Successiva" nativa
**droppa i filtri** (verificato col probe). La soluzione:

1. Monkey-patch su `jQuery.fn.load()` per sostituire `size=20` con `size=200`
   nell'URL prima del fire.
2. Cattura dell'URL completo (con tutti i filtri encoded) in
   `window.__lastSearchUrl`.
3. Loop di paginazione manuale via `fetch()`: ricostruisce
   `?...&size=200&page=2,3,4...` finché BI restituisce ISIN nuovi o meno di
   200 righe (= ultima pagina). Safety stop a 50 pagine.

Risultato: ogni profilo scarica **tutti** i bond che BI espone per quel
filter set, non più cap a 20.

### Parser HTML

`parse_results_html(html)` estrae da ogni `<tr>` della tabella risultati:

- ISIN, descrizione, ULTIMO prezzo, CEDOLA, SCADENZA
- Salta gli STRIP (zero-coupon non standard)
- Numeri parsati con format italiano (virgola decimale, punto migliaia)
  tramite `_normalise_number()`
- Date sia in `dd/mm/yyyy` che `yyyy-mm-dd`

### Orchestrazione (`_scrape_profile` + `run_scrape`)

- All'inizio di ogni run: `db.reset_categories()` setta `category=NULL,
  tipologia=NULL, nation=NULL` su tutti i record (così ogni scrape
  ri-attribuisce dalla fonte corretta).
- Per ogni profilo: nuova `driver.get(URL)`, dismiss cookie banner, install
  size-hook, applica filtri, click CERCA, walk all pages.
- `_on_record(rec)` per ogni ISIN: skip se callable; estrae `currency`,
  `coupon`, `geo_area`, `issuer_type` dal nome; deriva `tipologia` e `nation`
  dal profilo; `db.upsert_bond(...)`; `db.upsert_price(isin, today, price)`.
- A fine run: `db.mark_stale_inactive(days=14)` soft-purge degli ISIN non
  rivisti.

---

## database.py — SQLite

### Tabelle

**`bonds`** (1 riga per ISIN):

```
isin TEXT PK | name | coupon | maturity_date | currency
| category (= profile name di prima vista)
| tipologia (autoritativa BI)
| nation (autoritativa dal filtro Paese applicato)
| issuer_type | geo_area
| first_seen | last_seen
| is_active (soft-purge flag, default 1)
```

**`bond_prices`** `(isin, date) PK → price` con `ON DELETE CASCADE`. Schema
multi-data: ogni scrape aggiunge un nuovo prezzo per oggi, non sovrascrive lo
storico.

**`scrape_runs`**: audit log con `started_at, finished_at, profile,
rows_scraped, status, error_message`.

### Comportamenti chiave

- **Migration idempotente**: ogni ALTER TABLE è preceduto da check su
  `PRAGMA table_info`. Ho aggiunto storicamente: `tipologia`, `nation`,
  `is_active`.
- **`upsert_bond`**: usa `ON CONFLICT(isin) DO UPDATE` con `COALESCE` per
  preservare il valore *first-seen* su `category/tipologia/nation` (il primo
  profilo che vede l'ISIN vince), e `excluded` per aggiornare prezzo/maturity.
  **Re-sighting riattiva** un ISIN: `is_active=1`.
- **`mark_stale_inactive(days=14)`**: setta `is_active=0` su record con
  `last_seen < today - 14gg`. Niente DELETE: i prezzi storici restano.
- **`list_bonds_with_latest_price()`**: LEFT JOIN su sub-query `MAX(date)`
  per record di prezzo più recente. Filtra `is_active=1` di default; passare
  `include_inactive=True` per debug.

---

## calculations.py — la matematica e le classificazioni

### YTM via bisezione

`_ytm_bisection(price, coupon, years)` risolve numericamente `r` tale che il
PV dei cash flow = price:

```
PV(r) = coupon × [1-(1+r)^-n]/r  +  100 × (1+r)^-n
```

Composizione annuale, intervallo iniziale `[-20%, +200%]`, fino a 80
iterazioni o convergenza < 1e-10.

### Net YTM

`net_annual_yield()`:

1. Determina `issuer_type` (Government 12.5% / Corporate 26%) dal nome del
   bond.
2. `net_coupon = coupon × (1 - tax)` — solo le cedole vengono tassate.
3. Il valore di rimborso (100) **non è tassato**: la plusvalenza implicita
   su un bond comprato sotto la pari non subisce ritenuta sugli individui
   italiani.
4. Bisezione su `(price, net_coupon, years_to_maturity)` → tasso annuo netto.

### Classificazione issuer

`issuer_type_from_name(name)`: cerca token nel nome (BTP, OAT, BUND, BONOS,
GGB, REPUBLIC, GOVT, EIB, ESM, EFSF, EBRD, World Bank, IBRD, IFC, BEI,
Eurofima, Council of Europe, ecc.) → "Government". Tutto il resto →
"Corporate". Le sopranazionali vanno in "Government" perché sono in
white-list italiana al 12.5%.

### Nazione sovereign

`sovereign_nation_from_name(name)`: regex su token tipologici (BTP→Italia,
OAT→Francia, BUND→Germania, OBLIGACIONES→Spagna, GGB→Grecia, NEDERLAND→Olanda,
ecc.). **Usato solo come fallback** per record con `nation=NULL`. Il valore
primario viene dal filtro Paese applicato in scrape.

### Altri helper

- `is_callable_from_name(name)`: cerca "Call"/"Callable"/"C/A" come token
  isolati.
- `is_inflation_linked(name)`: BTPi, BTP€i, Bundei, OAT€i, TIPS, LINKER,
  INFLAZ, INDICIZZ. Inflation-linked ricevono `net_yield_pa = None` perché
  la cedola nominale ≠ reale.
- `currency_from_name(name)`: regex su codici 3-letter (USD, GBP, CHF...) e
  shorthand BI 2-letter (EU=EUR, US=USD).
- `years_to_maturity()`, `duration_bucket()` (Short <3y / Medium 3-7y /
  Long >7y).
- `geo_area_from_isin()`: prefisso ISIN → area geografica (IT=Italia,
  XS=Eurobond, ecc.).

### Aggregati

- **`enrich_bond(bond)`**: prende un dict da DB e gli aggiunge
  `years_to_maturity, duration_bucket, inflation_linked, is_callable,
  sovereign_nation, net_yield_pa, issuer_type, geo_area`.
- **`yield_by_nation(bonds, ...)`**: aggrega per nazione applicando filtri
  (currency, years_range, yield_range, tipologie, sovereign_only).
  Restituisce `{nation, count, avg, min, max, median}`. Il chart usa la
  **mediana** come statistica primaria (robusta a outlier di scadenza).
- **`find_anomalies(bonds)`**: top-N BTP italiani EUR il cui yield supera la
  media dei "peer" entro ±1 anno di duration. Identifica BTP "fuori curva"
  che potrebbero essere mispricing o eventi di credito.

---

## app.py — Flask + UI

### Endpoints

| Route              | Metodo | Cosa fa                                                                                                                  |
|--------------------|--------|--------------------------------------------------------------------------------------------------------------------------|
| `/`                | GET    | Pagina HTML con header, action bar, summary cards, chart YTM-vs-duration, chart yield-per-nazione, anomalie, tabella DataTables |
| `/api/bonds`       | GET    | JSON: tutti i bond enriched, average yield, anomalie, aggregato per nazione (sovereign EUR 7-12y proxy 10y)              |
| `/api/sync`        | POST   | Avvia thread scrape (uno alla volta). 409 se già in corso                                                                |
| `/api/sync/stop`   | POST   | Setta `cancel_requested=True`, lo scraper interrompe al prossimo check                                                   |
| `/api/sync/status` | GET    | Polling: status (idle/running/completed/failed/stopped) + per-profile stats live                                         |
| `/api/export`      | GET    | Download di un singolo `.html` self-contained con dati + Plotly + DataTables inline                                      |

### SyncState

Singleton in-memory, locked. Tiene il thread corrente e i `profile_stats`
aggiornati dal `page_callback` dello scraper. La UI fa polling ogni ~2s su
`/api/sync/status` per renderizzare la barra di avanzamento per profilo.

### Template

Inline (per portabilità), CSS+JS condivisi tra pagina live ed export. Il
render JS:

- **Cards top**: bonds totali / con prezzo / Government / Corporate / yield
  medio.
- **Chart Plotly principale**: scatter Net Yield % vs Years to Maturity,
  colorato per (currency, issuer_type), simbolo cerchio/diamante per
  Gov/Corp, hover con ISIN+name+price.
- **Chart per nazione**: bar chart orizzontale, sovereign EUR 7-12y. Mediana
  come x-axis, barra arancione per Italia. Etichette `X.XX% (N)` sopra ogni
  barra.
- **Tabella anomalie**: top 2 BTP fuori curva, con peer mean e spread.
- **Tabella principale DataTables**: tutti i bond, filtri colonna in cima
  (currency, issuer, duration), search globale, sort, paginazione lato
  client.

### Export portabile

`/api/export` genera un `.html` con `__PAYLOAD__` placeholder sostituito dal
JSON dei bond + i template CSS/JS. Apribile offline (richiede solo CDN per
Plotly/DataTables).

---

## Comportamento dei dati nel tempo

- **Bond nuovi**: appaiono al primo scrape che li vede.
- **Bond rivisti**: `last_seen` aggiornato, `is_active=1` confermato, prezzo
  nuovo aggiunto a `bond_prices`.
- **Bond non rivisti**: dopo 14gg `is_active=0` (soft-purge); nascosti dallo
  screener; ri-emergono se BI li riquota.
- **Bond scaduti**: `years_to_maturity ≤ 0` → `net_yield_pa = None` → fuori
  dal chart automaticamente. Restano in DB.
- **Storico prezzi**: monotonicamente crescente, mai cancellato. Permette di
  costruire serie temporali per ISIN se in futuro si vorrà.

---

## Test

`tests/` con 4 file:

- `test_database.py`: schema, upsert, CRUD, migrations.
- `test_calculations.py`: YTM bisection, edge cases (price=face, zero
  coupon, scadenze brevi/lunghe), classificazioni.
- `test_scraper_parser.py`: HTML parser su fixture, isolato da Selenium.
- `test_app_integration.py`: feed parsed records → DB → Flask test client →
  JSON shape + export.

Lo scrape Selenium **non è coperto da test automatici** (richiede browser
live). Per debug si usa il CLI: `python scraper.py --show --dry-run --profile
<name>`.

---

## Quello che il progetto NON fa (per progettazione)

- **Non visita le schede dei singoli ISIN**: ogni dato che ci serve è già
  nella tabella di ricerca avanzata.
- **Non scrive prezzi storici da fonti terze** (TradingView, ecc.): solo BI,
  una riga di prezzo per giorno.
- **Non gestisce ETF / fondi / azioni**: solo bond.
- **Non fa portfolio tracking**: è uno *screener*, non un *position manager*.
- **Non gestisce inflation-linked yield**: ne calcola il YTM nominale solo
  se richiesto, ma di default li esclude (cedola reale non tracciata).

---

## Storico delle decisioni architetturali principali

- **`nation` da regex → da filtro BI Paese (autoritativo)**: il dato di
  nazione viene direttamente dal filtro applicato durante lo scrape, non
  ricavato a posteriori dal nome del bond. Regex come fallback solo per
  legacy rows.
- **Pattern regex estesi**: aggiunti Obligaciones=Spagna, OATEI=Francia,
  NEDERLAND=Olanda per non perdere bond con denominazioni native non
  riconosciute prima.
- **18 → 33 paesi**: aggiunti USA, Canada, UK, Svizzera, Norvegia, Svezia,
  Lussemburgo, Estonia, Lettonia, Lituania, Filippine, Honduras, Costa D
  Avorio, Jersey, Cina; rimossa Slovacchia (non esiste nel dropdown BI).
- **Cap a 20 righe → paginazione vera senza limite**: jQuery.fn.load hook
  + walk via `fetch()` con `&size=200&page=N`.
- **Filtro callable client-side**: BI buggato server-side per i sovereign
  (NULL ≠ No → azzera tutto). Detection via token "Call" nel nome.
- **Plain Vanilla + Subordinazione=No riattivati server-side**: testati safe
  sui sovereign (loro NULL viene trattato come "non subordinato"/"plain").
- **Soft-purge `is_active`**: threshold 14gg, riattivazione automatica al
  re-sighting.
