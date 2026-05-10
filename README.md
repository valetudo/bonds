# Bonds Screener — local single-file project

Tutto in `bonds/`: scraping da [Borsa Italiana — Ricerca Avanzata](https://www.borsaitaliana.it/borsa/obbligazioni/ricerca-avanzata.html), database SQLite locale (`bonds.db`), screener Flask con grafico interattivo, tabella filtrabile, anomalie BTP EUR ed export portabile in HTML self-contained.

## Avvio

```
start.bat
```

Apre `http://127.0.0.1:5070/` nel browser di default. Click **"⟳ Aggiorna"** per scaricare i prezzi da Borsa Italiana.

Per spegnere: chiudere la finestra "Bonds Screener Server".

## Setup iniziale (solo prima volta)

PowerShell o cmd, da dentro la cartella `bonds`:

```
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

(In PowerShell 5.1 evita `&&`: esegui i due comandi separatamente o uniscili con `;`.)

Selenium scarica automaticamente il driver Chrome al primo run via `webdriver-manager`.

## Cosa scarica

Lo scraper applica i filtri della pagina di ricerca avanzata in due passate:

| Profilo         | Struttura     | Tipo Cedola  | Rimborso Anticipato |
|-----------------|---------------|--------------|---------------------|
| `fixed_vanilla` | Plain Vanilla | Fisso        | No                  |
| `zero_coupon`   | Plain Vanilla | Zero Coupon  | No                  |

Per ogni profilo scorre **tutte le pagine** dei risultati con un delay random di 0.6–1.5 s tra una pagina e la successiva (anti-ban). Per ogni riga estrae ISIN, descrizione, ULTIMO (prezzo), CEDOLA, SCADENZA — niente visite a schede individuali.

Esclusi a monte (filtri lato sito): callable, floating, inflation-linked. Gli STRIP vengono filtrati comunque a runtime via nome.

## Struttura

```
bonds/
├── app.py              Flask + UI (route + template + portable export)
├── scraper.py          Selenium advanced search + parser puro + CLI dry-run
├── database.py         SQLite layer (bonds, bond_prices, scrape_runs)
├── calculations.py     net yield, duration, anomalie (pure functions)
├── requirements.txt
├── start.bat
├── bonds.db            (auto-creato al primo run)
└── tests/
    ├── test_database.py
    ├── test_calculations.py
    ├── test_scraper_parser.py
    └── test_app_integration.py
```

## Test

```
python -m unittest discover -v tests
```

Coverage attuale: **40 test**, tutti passanti, su database / calcoli / parser scraper / integrazione Flask. Lo scraper Selenium è isolato dai test (testiamo solo i parser puri); per debug del Selenium usare il CLI:

```
python scraper.py --show --dry-run --profile fixed_vanilla --verbose
```

## Note

- I bond senza prezzo restano in DB ma sono **esclusi dal calcolo della media yield** e dal grafico (niente `NaN`/errori).
- Database in `bonds.db` nella stessa cartella, formato SQLite, schema auto-creato.
- L'export portabile è un singolo `.html` con tutti i dati e i grafici interattivi inline (richiede solo connessione internet per caricare Plotly + jQuery + DataTables da CDN).
