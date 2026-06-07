# Bond Ladder — Project Overview

**Scopo**: scaricare l'universo delle obbligazioni *plain vanilla* a cedola fissa
quotate su **Borsa Italiana**, calcolarne il **rendimento netto a scadenza**
(YTM, fisco IT 2026) e costruire un **bond ladder** (gradinata di scadenze).

**Stack**: Python 3.10+, Streamlit + Plotly (UI), pandas + pyarrow (parquet),
scipy (TIR/brentq). Scraping: Selenium (MOT) e requests + BeautifulSoup (EuroTLX).

## Due fonti (entrambe Borsa Italiana)

- **MOT** (`scraper/search.py`) — pagina *ricerca avanzata* via Selenium. I filtri
  del form determinano l'eligibilità (Plain Vanilla, cedola fissa, no subordinati)
  e la categoria gov/corp (Tipologia). Una sola query per tipologia × valuta ×
  cedola: il paese (e corp_ita/estero) si ricava dal **prefisso ISIN** (niente
  iterazione paese-per-paese, sarebbe ~20× più lenta per gli stessi titoli).
- **EuroTLX** (`scraper/eurotlx.py`) — mercato separato (gli Eurobond "XS…"),
  endpoint diretto via requests (`…/eurotlx/ricerca-avanzata/risultati.html`).
  Classifica per *categoria* strumento (44 voci → 4 bucket); non avendo filtri
  Struttura/Cedola, l'eligibilità (no floater/index-linked/convertibili/callable)
  è dal nome. Prezzo = Ultimo o mid(bid, ask).

Per un ISIN presente su entrambi i mercati vince **MOT** (dedup per ISIN).

## YTM (sempre calcolato, mai scaricato)

`finance/` calcola YTM **lordo** (brentq) e **netto** (fisco IT 2026: 12,5%
Stato/white-list, 26% corporate; bollo 0,2%/anno opzionale). Modalità
"Bilanciata": frequenza cedola assunta (configurabile) e rateo stimato dalle date
cedola ricostruite dalla scadenza, **senza visitare le schede ISIN**. La cedola
annua è ricavata dal nome (la colonna CEDOLA di BI mostra spesso quella periodica).

## Scraping: zero-coupon sempre, salvataggio incrementale

Gli **zero-coupon** (BOT, CTZ, …) sono **sempre inclusi**. Durante lo scraping i
record vengono scritti su parquet **a blocchi** (non solo a fine run): il progresso
è visibile su disco e un crash non perde tutto (su errore salva il parziale).

## Moduli

```
app.py            entrypoint Streamlit (Overview / Aggiorna Dati / Bond Ladder)
config.py         URL, id <select>, value filtri, mappa categorie EuroTLX, default
scraper/          search.py (MOT), eurotlx.py (EuroTLX), price_updater.py, probe.py, detail.py(stub)
finance/          daycount.py, yield_calc.py, tax.py, enrich.py
ladder/           builder.py (LadderParams + build_ladder)
data/             store.py (parquet idempotente) + universe/prices.parquet + scrape_log.txt
ui/               charts.py (Plotly), sidebar.py, filters.py (pannello filtri condiviso)
tests/            test unitari (finance, store, parser/profili, builder, filtri, eurotlx)
```

## Dati & dati di esempio

I dati di runtime stanno in `data/` come **parquet**: `universe.parquet`
(catalogo, 1 riga/ISIN, con campo `mercato` MOT|EuroTLX), `prices.parquet`
(storico `(isin,date,price)`), `scrape_log.txt` (log). Upsert **idempotente**:
rieseguire lo scraping non duplica gli ISIN e preserva i campi di prima vista.

⚠️ I file `data/` committati in questo repo sono **dati di ESEMPIO inventati**
(`make_sample_data.py`, descrizioni `… (FAKE)`), non reali: servono solo a far
partire l'app e vengono sovrascritti dal primo *Scarica universo*. I dati veri
sono git-ignored. Vedi la sezione "Dati di esempio" del [README](README.md).

## Note / limiti

- La spec originale è in [`PROMPT_bond_ladder_plan_v4.md`](PROMPT_bond_ladder_plan_v4.md).
- Esclusi per disegno: floater/step/multi-coupon, subordinati, inflation/index-
  linked, convertibili, ABS; callable scartati dal nome.
- Calcolo finanziario **indicativo**, non è consulenza fiscale; aliquote 2026.
