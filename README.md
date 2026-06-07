# Bond Ladder

App Python/Streamlit standalone che fa tre cose, usando due fonti di **Borsa
Italiana** — **MOT** (ricerca avanzata, via Selenium) ed **EuroTLX** (endpoint
diretto, via requests; qui vivono gli Eurobond istituzionali "XS…"):

1. **Costruzione dell'universo** di obbligazioni *plain vanilla* a cedola fissa
   tramite i filtri del form (Struttura, Tipologia, Tipo Cedola, Subordinazione,
   Paese, Valuta). **La categoria viene dai filtri, mai dal nome del bond.**
2. **Aggiornamento prezzi** on-demand per gli ISIN già in universo.
3. **Costruzione di un bond ladder** parametrico (gradinata di scadenze).

Il rendimento (**YTM**) non viene mai scaricato: è **calcolato** dai dati
(prezzo, scadenza, cedola), sia **lordo** sia **netto** secondo il regime
fiscale italiano 2026. Modalità "Bilanciata": frequenza cedola configurabile e
rateo stimato dall'anniversario della scadenza, senza visitare le schede ISIN.

## Requisiti

- Python 3.10+
- Google Chrome (lo scraper usa Selenium headless; il driver è scaricato
  automaticamente da `webdriver-manager` al primo avvio)

## Setup (prima volta)

Il progetto è su Google Drive: crea il **venv fuori dal Drive** per evitare la
sincronizzazione di migliaia di file.

```powershell
python -m venv C:\Users\Beppe\venvs\bond_ladder
C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe -m pip install -r requirements.txt
```

## Avvio

**Doppio click su `start.bat`** — apre il browser sull'app e tiene aperta una
finestra col server (chiudila per fermare il programma).

In alternativa, da terminale:

```powershell
.\start.ps1
```

oppure manualmente:

```powershell
$env:PYTHONIOENCODING = "utf-8"
C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe -m streamlit run app.py
```

Si apre su `http://localhost:8501` (o porta indicata). I tre tab:

- **Overview** — scatter YTM vs anni alla scadenza (toggle lordo/netto, colori
  per categoria) + tabella filtrabile.
- **Aggiorna Dati** — selettore **Mercati** (MOT / EuroTLX), "Scarica universo
  da BI" (con progress e log live) e "Aggiorna prezzi". Idempotente: rieseguire
  non duplica gli ISIN; per un ISIN su entrambi i mercati vince MOT.
- **Bond Ladder** — form con validazione somma allocazioni = 100%, output per
  gradino, stacked bar e riepilogo (YTM medio ponderato, capitale, n. bond).

## Dati di esempio (inventati)

La cartella `data/` di questo repo contiene **dati FINTI** generati da
[`make_sample_data.py`](make_sample_data.py): pochi bond fittizi con descrizioni
tipo `ESEMPIO … (FAKE)`, giusto per far partire l'app con qualcosa di mostrabile.

- **Non sono dati reali** né prezzi/rendimenti veri.
- Vengono **sovrascritti** dal primo *Scarica universo da BI* nell'app.
- I file dati di runtime (`data/*.parquet`, `data/scrape_log.txt`) sono
  **git-ignored**; questi campioni sono committati a forza (`git add -f`) solo a
  scopo dimostrativo. **Non versionare i tuoi dati reali.**
- Per (ri)generarli: `python make_sample_data.py` (si rifiuta di sovrascrivere
  dati reali; usa `--force` per forzare).

## Test

```powershell
$env:PYTHONPATH = (Get-Location)
C:\Users\Beppe\venvs\bond_ladder\Scripts\python.exe -m unittest discover -s tests
```

## Struttura

```
app.py            entrypoint Streamlit (3 tab)
config.py         URL, id dei <select>, value filtri, paesi, default
scraper/          search.py (MOT), eurotlx.py (EuroTLX), price_updater.py, probe.py, detail.py (stub)
finance/          daycount.py, yield_calc.py (YTM lordo, brentq), tax.py (netto), enrich.py
ladder/           builder.py (LadderParams + build_ladder)
data/             store.py (parquet idempotente) + universe/prices.parquet (runtime)
ui/               charts.py (Plotly), sidebar.py (controlli)
tests/            test unitari (finance, store, parser/profili, builder)
```

## Note

- **Categoria dai filtri**: `gov_ita`/`gov_eur` dalla Tipologia; `corp_ita`/
  `corp_eur` e il paese dal **prefisso ISIN** (record marcato
  `paese_da_isin_fallback`). Si evita l'iterazione paese-per-paese: sarebbe ~20×
  più lenta per gli stessi titoli (per un ISIN specifico, guarda la scheda su BI).
- **Zero-coupon** (BOT, CTZ, …) **sempre inclusi**.
- **Salvataggio incrementale**: durante lo scraping i record sono scritti su
  parquet a blocchi (progresso visibile su disco; un crash non perde tutto, su
  errore salva il parziale).
- **Cedola**: la colonna CEDOLA di BI mostra spesso la cedola *periodica*; il
  numero annuo è estratto dal nome (estrazione fattuale), con la tabella come
  fallback.
- **Callable**: il filtro server-side "Rimborso Anticipato=No" è inaffidabile su
  BI (azzera i governativi); i callable sono scartati lato client dal nome.
- **EuroTLX**: mercato separato (la ricerca avanzata MOT copre solo MOT/Euronext
  Access). Classifica per *categoria* strumento (mappata nei 4 bucket) e **non**
  ha filtri Struttura/Cedola/Subordinazione: l'eligibilità (escludere floater
  `Tv`, index/inflation-linked, convertibili, callable) è fatta dal nome. Prezzo
  = Ultimo, con fallback al **mid(bid, ask)** per i (molti) titoli illiquidi.
- **Anti-ban**: pause random tra le pagine; non rimuoverle.
- **Aliquote 2026**: 12,5% titoli di Stato/white-list, 26% corporate; bollo
  0,2%/anno opzionale. Calcolo indicativo, **non è consulenza fiscale**.
```
