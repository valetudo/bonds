# PROMPT — Bond Ladder Tool (modalità Plan per Claude Code)

## Contesto

Ho un repo esistente chiamato `quant_lab` che contiene, fra molte altre cose,
uno scraper per Borsa Italiana (obbligazioni) e una bozza di bond ladder.
Voglio estrarre e riscrivere da zero solo quella parte in un progetto autonomo
chiamato `bond_ladder`, pulito e focalizzato.

---

## Obiettivo

Costruire un'applicazione Python/Streamlit standalone che faccia **solo** tre cose:

1. **Costruzione dell'universo** di obbligazioni plain vanilla usando i filtri
   della pagina di ricerca avanzata di Borsa Italiana. I filtri stessi
   determinano sia l'eligibilità (plain vanilla, cedola fissa, no callable) sia
   la categoria (gov/corp, ita/estero). Niente classificazione per singola scheda.
2. **Aggiornamento prezzi** on-demand per i bond già nell'universo.
3. **Costruzione di un bond ladder** con parametri configurabili.

Il **rendimento (YTM) NON viene scaricato**: viene **calcolato** dai dati
(prezzo, data odierna, valore di rimborso, data di scadenza, cedola).
Viene calcolato sia il **rendimento lordo** sia il **rendimento netto** secondo
il regime fiscale italiano vigente.

---

## Principio chiave: i filtri della pagina di ricerca fanno il lavoro

La pagina "Obbligazioni - Cerca Strumento" di Borsa Italiana ha menu a tendina
che permettono di pre-filtrare TUTTO ciò che serve, senza dover visitare la
scheda di ogni ISIN né inferire nulla dal nome del bond:

| Menu a tendina | Opzioni rilevanti osservate | A cosa serve |
|---|---|---|
| **Struttura** | "Plain Vanilla" (esistono anche Index Linked, Inflation Linked, Strutturate Su Tassi) | Garantisce plain vanilla → no inflation/index linked, no strutturate |
| **Tipo Cedola** | "Fisso" (verificare label esatto) | Esclude cedola variabile e step-up |
| **Rimborso Anticipato** | "No" (verificare label esatto) | Esclude callable e putable |
| **Valuta** | "EUR", "USD" | Solo queste due valute |
| **Tipologia** | Banche, Corporate, Eurobonds Republic Of Italy, Secured, Titoli Di Stato Esteri, Titoli Di Stato Italiani | **Determina direttamente la categoria** gov/corp e parte di ita/estero |
| **Paese** | (lista paesi) | Distingue corporate italiane da estere |

La tabella dei risultati espone direttamente: **ISIN, Descrizione, Ultimo
(prezzo), Cedola (%), Scadenza** — con link alla scheda. I risultati sono
**paginati** (10+ pagine), quindi va iterata la paginazione.

**Conseguenza architetturale**: la stragrande maggioranza dei dati si ottiene
dai filtri + tabella risultati. La scheda individuale serve SOLO per i due dati
fattuali necessari al calcolo preciso del rendimento che non sono in tabella:
**frequenza cedolare** (annuale/semestrale) e **data ultimo godimento/stacco**
(per il rateo). E anche questo è opzionale (vedi sotto).

**Nessuna inferenza dal nome del bond, mai.** La categoria viene dal filtro
Tipologia + filtro Paese, non dal testo della descrizione.

---

## Fase 1 — Analisi del repo esistente

1. Naviga nel repo `quant_lab` (chiedi la path se non la conosci).
2. Localizza i file su: scraping Borsa Italiana / obbligazioni, bond ladder.
3. **Priorità**: cerca codice che già interagisce con il form di ricerca BI —
   in particolare come vengono passati i parametri dei filtri (GET/POST, nomi
   esatti dei parametri, gestione paginazione) e se esiste un endpoint
   JSON/AJAX sottostante.
4. Verifica se il repo usa requests/BeautifulSoup oppure Selenium/Playwright.
5. Produci un **report testuale**: file rilevanti, URL/endpoint funzionanti,
   struttura dati, cosa riusare e cosa riscrivere.

**Non creare ancora nessun file del nuovo progetto.**

---

## Fase 2 — Piano architetturale (da approvare)

### Struttura cartelle

```
bond_ladder/
├── app.py
├── scraper/
│   ├── __init__.py
│   ├── search.py          # universo via form ricerca BI (filtri + paginazione)
│   ├── detail.py          # OPZIONALE: frequenza cedola + data godimento per rateo
│   └── price_updater.py   # aggiorna solo il prezzo (YTM si ricalcola)
├── finance/
│   ├── __init__.py
│   ├── yield_calc.py      # YTM lordo calcolato (no download)
│   └── tax.py             # rendimento netto, regime fiscale italiano
├── data/
│   ├── universe.parquet
│   ├── prices.parquet
│   └── scrape_log.txt
├── ladder/
│   ├── __init__.py
│   └── builder.py
├── ui/
│   ├── charts.py
│   └── sidebar.py
└── requirements.txt
```

---

### Modulo scraper/search.py — universo via filtri

**Prima cosa**: ispeziona il form con DevTools/curl. Capisci GET vs POST, i nomi
dei parametri, i `value` HTML reali di ogni `<option>` (i label visibili possono
differire dai value, es. "Plain Vanilla" potrebbe avere value="PV" o un codice).
Se c'è un endpoint JSON/AJAX che alimenta la tabella, usalo: è molto più stabile.
Documenta in commenti URL, nomi parametri e value usati.

**Filtri fissi in ogni query**:
- Struttura = Plain Vanilla
- Tipo Cedola = Fisso
- Rimborso Anticipato = No
- Valuta ∈ {EUR, USD} (due passate, o multi-select se il form lo consente)

**Strategia per ottenere la categoria dai filtri** (no inferenza dal nome):

| Categoria target | Come ottenerla via filtri |
|---|---|
| `gov_ita` | Tipologia ∈ {Titoli Di Stato Italiani, Eurobonds Republic Of Italy} |
| `gov_eur` | Tipologia = Titoli Di Stato Esteri |
| `corp_ita` | Tipologia ∈ {Banche, Corporate, Secured} **AND** Paese = Italia |
| `corp_eur` | Tipologia ∈ {Banche, Corporate, Secured} **AND** Paese ≠ Italia |

Per `corp_eur` ("Paese ≠ Italia") il dropdown Paese non ha un valore unico
"tutti tranne Italia". Valuta in Fase 2 quale approccio è migliore dopo aver
ispezionato il form, e proponimelo:
- (a) iterare il dropdown Paese sui paesi disponibili (escludendo Italia), oppure
- (b) eseguire la query corporate senza filtro Paese e ricavare il paese di
  ciascun ISIN. **Solo come fallback** per il paese si può usare il prefisso
  dell'ISIN (ISO 6166: "IT" = Italia), che è un identificativo strutturale
  standardizzato, NON il nome del bond. Segnala l'eventuale uso di questo fallback.

Itera tutte le combinazioni necessarie di (Tipologia × Valuta [× Paese]),
gestendo la paginazione di ciascuna.

**Paginazione**: itera tutte le pagine attive fino a esaurimento risultati.
Pausa tra pagine: `time.sleep(random.uniform(1.5, 4.0))`.

**Output → universe.parquet** (un record per ISIN, deduplica tra query):
```
isin, descrizione, cedola_pct, scadenza, valuta,
categoria (gov_ita | gov_eur | corp_ita | corp_eur),
tipologia_bi (label originale del filtro), paese,
freq_cedola (null finché non recuperata da detail.py),
data_godimento (null finché non recuperata),
url_scheda, timestamp_aggiunta
```

Idempotente: se un ISIN è già in `universe.parquet`, non riscriverlo.

Log in `scrape_log.txt`:
```
2026-06-06 14:32:07 | ADDED | IT0005454241 | BTP 1.35% Ap29 | gov_ita | EUR
2026-06-06 14:32:09 | ADDED | DE0001102614 | Bund 0% Au30    | gov_eur | EUR
2026-06-06 14:32:11 | SKIP  | IT0005454241 | già presente
```

---

### Modulo scraper/detail.py — OPZIONALE (precisione rendimento)

Serve solo a recuperare i due dati non presenti in tabella ma utili al calcolo
preciso del rendimento: **frequenza cedolare** e **data ultimo godimento/stacco**
(per il rateo). Da invocare solo sugli ISIN in universo che hanno questi campi
ancora null.

- Flusso idempotente (salta ISIN già completi).
- Visita `url_scheda`.
- **Pausa random obbligatoria**: `time.sleep(random.uniform(2.5, 6.0))`.
  Non negoziabile, mai sotto 2 secondi.
- Estrai: frequenza cedola, data godimento. Doppia verifica (opzionale) che
  Struttura/Cedola/Rimborso coincidano con quanto promesso dai filtri; logga
  eventuali discrepanze.
- Aggiorna i campi `freq_cedola` e `data_godimento` in `universe.parquet`.
- Su errore: logga e prosegui senza crashare.

**Alternativa senza schede** (più veloce, meno preciso): se preferisco evitare
del tutto le schede, il calcolo del rendimento usa convenzioni configurabili
di default (es. frequenza impostabile dall'UI, rateo trascurato o stimato).
Proponi in Fase 2 entrambe le opzioni e fammi scegliere.

---

### Modulo finance/yield_calc.py — YTM CALCOLATO (no download)

Il rendimento non viene mai preso da BI: si calcola.

**YTM lordo** — risolvi numericamente il tasso `y` che eguaglia il prezzo alla
somma dei flussi di cassa attualizzati:

```
dirty_price = Σ_t [ cedola_periodo / (1 + y/m)^(m·t) ] + rimborso / (1 + y/m)^(m·T)
```

dove:
- `m` = frequenza cedolare annua (1 = annuale, 2 = semestrale)
- `cedola_periodo` = cedola_annua / m
- `rimborso` = 100 (alla pari, plain vanilla)
- `dirty_price` = prezzo "tel quel" = corso secco (Ultimo da BI) + rateo
- `rateo` = quota di cedola maturata dall'ultimo godimento alla data odierna
  (richiede `data_godimento` e `freq_cedola`; se non disponibili, usa convenzione
  configurabile o poni rateo = 0 segnalandolo)
- `T` = anni alla scadenza dalla data odierna (usa una convenzione day-count
  esplicita, es. ACT/ACT o 30/360 — dichiarala in commento)

Implementazione: `scipy.optimize.brentq` (robusto, con bracketing) o Newton.
Gestisci il caso zero-coupon (cedola = 0) come limite.

Esponi funzioni pure e testabili:
```python
def accrued_interest(coupon_annual, freq, last_coupon_date, settlement_date) -> float
def ytm_gross(clean_price, coupon_annual, freq, maturity_date,
              settlement_date, redemption=100.0) -> float
```

---

### Modulo finance/tax.py — rendimento NETTO (fisco italiano 2026)

Regime fiscale vigente (rendere le aliquote **costanti configurabili** in cima
al file, non sparse nel codice — possono cambiare con le leggi di bilancio):

**Aliquota sostitutiva su cedole e plusvalenze in base alla categoria:**

| Categoria / Tipologia BI | Aliquota |
|---|---|
| Titoli Di Stato Italiani | 12,5% |
| Eurobonds Republic Of Italy | 12,5% |
| Titoli Di Stato Esteri (white list) | 12,5% |
| Banche | 26% |
| Corporate | 26% |
| Secured | 26% |

Note da implementare come commenti/parametri:
- I titoli di Stato esteri godono del 12,5% **solo se white list**. Per il
  perimetro EUR/USD la quasi totalità è white list; assumi 12,5% per "Titoli Di
  Stato Esteri" ma rendi possibile un override per-ISIN (flag `white_list`).
- Imposta di bollo: **0,2% annuo** sul controvalore di mercato. Rendila
  un'opzione attivabile dall'UI (incide sul netto come costo annuo ricorrente).

**Calcolo del rendimento netto** — stessa equazione del TIR ma su flussi netti:

- Cedola netta per periodo = `cedola_periodo · (1 − aliquota)`
- Plusvalenza a scadenza: `capital_gain = redemption − clean_price_acquisto`
  - se `capital_gain > 0`: imposta = `capital_gain · aliquota`;
    flusso finale netto = `redemption − imposta`
  - se `capital_gain ≤ 0` (acquisto sopra la pari): nessuna imposta sul capitale;
    la minusvalenza NON viene tassata. Modello base: ignora il beneficio della
    compensazione (dipende da fattori esterni allo strumento). Opzionale: flag
    per considerare la compensabilità delle minusvalenze.
- (Se bollo attivo) sottrai `0,2% · controvalore` per ciascun anno di detenzione.
- Risolvi `y_net` su `dirty_price = Σ flussi_netti_attualizzati`.

```python
ALIQUOTA_GOV = 0.125
ALIQUOTA_CORP = 0.26
BOLLO_ANNUO = 0.002

def aliquota_for(categoria: str, white_list: bool = True) -> float
def ytm_net(clean_price, coupon_annual, freq, maturity_date, settlement_date,
            categoria, redemption=100.0, apply_bollo=False,
            white_list=True) -> float
```

**Nota**: è un calcolo finanziario indicativo, non consulenza fiscale; le
aliquote sono quelle vigenti nel 2026 e vanno verificate nel tempo.

---

### Modulo ladder/builder.py

```python
@dataclass
class LadderParams:
    capital: float
    n_steps: int
    max_duration_years: int
    alloc_gov_ita: float
    alloc_corp_ita: float
    alloc_gov_eur: float
    alloc_corp_eur: float
    # somma allocazioni = 100%
    use_net_yield: bool = True   # ottimizza/ordina per YTM netto o lordo
    apply_bollo: bool = False
```

Logica:
1. Carica `universe.parquet`, fai join con `prices.parquet` (prezzo corrente).
2. Calcola per ogni bond YTM lordo e netto (finance/) alla data odierna.
3. Dividi l'orizzonte in `n_steps` fasce temporali uguali (0 → max_duration_years).
4. Per ogni fascia × categoria, seleziona il bond con YTM (netto se
   `use_net_yield`) più alto la cui scadenza cade nella fascia.
5. Alloca: `capitale / n_steps` per gradino, suddiviso per categoria secondo le %.
6. Output DataFrame:
   `[gradino, fascia_anni, isin, descrizione, categoria, paese, scadenza,
     cedola_pct, ytm_lordo, ytm_netto, prezzo, importo_eur, n_titoli_nominale]`
7. Se una fascia/categoria non ha bond disponibili, segnalalo esplicitamente
   (non crashare).

---

### UI Streamlit — 3 Tab

**Tab 1 — Overview**
- Scatter plot Plotly: X = anni alla scadenza, Y = YTM (%)
  - Toggle YTM lordo / YTM netto
  - Colori per categoria: gov_ita, corp_ita, gov_eur, corp_eur
  - Hover: ISIN, descrizione, cedola, prezzo, YTM lordo, YTM netto, paese
  - Legenda cliccabile per isolare/nascondere categorie
- Tabella bond eligible sotto il grafico (filtrabile), con colonne lordo e netto
- Badge "Prezzi aggiornati al: [timestamp]"

**Tab 2 — Aggiorna Dati**
- Sezione A — Aggiorna universo:
  - Contatori: ISIN trovati dai filtri / già in universo / nuovi
  - Pulsante "Scarica universo da BI" → esegue search.py (+ detail.py se attivo)
  - Progress bar con query/pagina corrente
  - Ultimi N log in tempo reale; riepilogo: N aggiunti, N errori
  - (Se opzione schede attiva) progress separato per il completamento
    frequenza/godimento via detail.py
- Sezione B — Aggiorna prezzi:
  - Timestamp ultimo aggiornamento
  - Pulsante "Aggiorna prezzi" → price_updater.py (aggiorna solo prezzo)
  - Progress bar; gli YTM si ricalcolano automaticamente dai nuovi prezzi

**Tab 3 — Bond Ladder**
- Form con parametri LadderParams (+ toggle lordo/netto, + toggle bollo)
- Validazione real-time: somma allocazioni = 100% (mostra totale corrente)
- Pulsante "Costruisci Ladder"
- Output:
  - Tabella per gradino con tutti i campi (incl. ytm_lordo e ytm_netto)
  - Stacked bar chart: X = fasce temporali, Y = importo EUR (colori per categoria)
  - Riepilogo: capitale allocato, YTM medio ponderato (lordo e netto), N bond usati
  - Warning esplicito per fasce/categorie senza bond disponibili

---

## Fase 3 — Implementazione

Solo dopo approvazione del piano, nell'ordine:

1. `requirements.txt`
2. `finance/yield_calc.py` + `finance/tax.py` — test con esempi noti a mano
   (es. bond a 100 con cedola X → YTM ≈ cedola; verifica netto vs lordo)
3. `scraper/search.py` — test: prime 20 righe per ogni categoria, verifica
   che la categoria derivi correttamente dai filtri
4. `scraper/detail.py` (se opzione attiva) — test su 5 ISIN: frequenza + godimento
5. `scraper/price_updater.py` — test sui 5 ISIN
6. `ladder/builder.py` — test con parametri fissi su dati reali
7. `ui/charts.py` + `ui/sidebar.py`
8. `app.py` — integrazione
9. Test end-to-end: `streamlit run app.py`

---

## Vincoli tecnici

- Python 3.10+, Streamlit, Plotly, Pandas + Parquet, scipy (per il TIR)
- requests + BeautifulSoup4 (o Selenium/Playwright se già nel repo)
- **Pausa random obbligatoria** verso Borsa Italiana:
  - schede singole: `time.sleep(random.uniform(2.5, 6.0))`
  - pagine della lista: `time.sleep(random.uniform(1.5, 4.0))`
  - mai rimuovere, mai sotto 1.5 secondi
- Il rendimento è SEMPRE calcolato, mai scaricato
- Aliquote fiscali come costanti configurabili in cima a `finance/tax.py`
- No API key, no abbonamenti — solo dati pubblici Borsa Italiana
- Progetto autonomo (nessuna dipendenza da `quant_lab`)
- Target: Windows 11, Intel i7-14700, 16GB RAM, no GPU
- Gestisci il "primo avvio" (nessun parquet presente)
- Errori scraper visibili nell'UI, non solo in console

---

## Note operative per Claude Code

- Modalità **Plan** prima di qualsiasi codice; per ogni modulo mostra il design
  e attendi conferma.
- **Priorità assoluta**: ispeziona il form di ricerca BI (DevTools/curl) prima
  di scrivere `search.py`. Determina GET/POST, nomi parametri, `value` reali
  delle option, presenza di endpoint JSON/AJAX. Documenta tutto in commenti.
- In Fase 2 proponimi: (a) la strategia di query per separare corp_ita/corp_eur,
  (b) se usare o no le schede per frequenza/rateo (preciso vs veloce).
- I moduli `finance/` devono essere funzioni pure e testabili in isolamento,
  con day-count convention dichiarata esplicitamente.
- Niente inferenze dal nome del bond: categoria dai filtri, caratteristiche dai
  filtri o dalla scheda. Il prefisso ISIN (ISO 6166) è ammesso solo come
  fallback esplicito per il paese, segnalato nei log.
- Idempotenza: rieseguire scraping su ISIN già presenti non modifica nulla.
