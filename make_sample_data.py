"""Genera DATI DI ESEMPIO (inventati) per data/universe.parquet, data/prices.parquet
e data/scrape_log.txt.

⚠️  NON sono dati reali: sono pochi bond fittizi (descrizioni con "ESEMPIO … (FAKE)")
che servono solo a far partire l'app con qualcosa di mostrabile. Vengono
SOVRASCRITTI dal primo "Scarica universo da BI" nell'app.

Sicurezza: se in data/universe.parquet ci sono già bond REALI (non "FAKE"), lo
script si rifiuta di sovrascrivere — usa `--force` per forzare.

Uso:  python make_sample_data.py  [--force]
"""
from __future__ import annotations

import sys
from dataclasses import asdict

from data import store
from scraper.search import BondRecord


def _b(isin, desc, cedola, scad, val, cat, tip, paese, fb, px, mercato):
    return BondRecord(
        isin=isin, descrizione=desc, cedola_pct=cedola, scadenza=scad, valuta=val,
        categoria=cat, tipologia_bi=tip, paese=paese, paese_da_isin_fallback=fb,
        ultimo_price=px, url_scheda=None, mercato=mercato,
    )


SAMPLE = [
    _b("IT0000000001", "ESEMPIO BTP Tf 3% Mg30 (FAKE)", 3.0, "2030-05-01", "EUR",
       "gov_ita", "Titoli Di Stato Italiani", "Italia", False, 99.5, "MOT"),
    _b("IT0000000002", "ESEMPIO BTP Tf 4% St35 (FAKE)", 4.0, "2035-09-01", "EUR",
       "gov_ita", "Titoli Di Stato Italiani", "Italia", False, 101.2, "MOT"),
    _b("IT0000000009", "ESEMPIO BOT Zc Gn26 (FAKE)", 0.0, "2026-06-30", "EUR",
       "gov_ita", "Titoli Di Stato Italiani", "Italia", False, 98.8, "MOT"),
    _b("DE0000000003", "ESEMPIO Bund Tf 2,5% Fb32 (FAKE)", 2.5, "2032-02-15", "EUR",
       "gov_eur", "Titoli Di Stato Esteri", "Germania", True, 98.0, "MOT"),
    _b("FR0000000004", "ESEMPIO Oat Tf 3,2% Ap40 (FAKE)", 3.2, "2040-04-25", "EUR",
       "gov_eur", "Titoli Di Stato Esteri", "Francia", True, 97.3, "MOT"),
    _b("IT0000000005", "ESEMPIO Enel Tf 4,5% Gn29 (FAKE)", 4.5, "2029-06-30", "EUR",
       "corp_ita", "CORPORATE_BONDS", "Italia", True, 100.1, "EuroTLX"),
    _b("XS0000000006", "ESEMPIO Telco Fx 5% Mz31 (FAKE)", 5.0, "2031-03-17", "EUR",
       "corp_eur", "CORPORATE_BONDS", "Eurobond/Intl", True, 102.0, "EuroTLX"),
    _b("XS0000000007", "ESEMPIO Auto Fx 6% Dc33 (FAKE)", 6.0, "2033-12-01", "USD",
       "corp_eur", "CORPORATE_BONDS", "Eurobond/Intl", True, 95.5, "EuroTLX"),
]


def main(force: bool = False) -> None:
    existing = store.load_universe()
    if not existing.empty and not force:
        real = existing[~existing["descrizione"].fillna("").str.contains("FAKE", na=False)]
        if len(real) > 0:
            print(f"ATTENZIONE: data/universe.parquet contiene {len(real)} bond REALI. "
                  f"make_sample_data NON li sovrascrive. Usa --force per forzare.")
            return
    res = store.upsert_universe([asdict(b) for b in SAMPLE], )
    n = store.save_prices(
        {b.isin: b.ultimo_price for b in SAMPLE if b.ultimo_price is not None},
        on_date="2026-01-01",
    )
    store.append_log(store.log_line("SAMPLE", "dati di esempio inventati (FAKE) - non reali"))
    print("universe:", res, "| prezzi:", n)


if __name__ == "__main__":
    main(force="--force" in sys.argv)
