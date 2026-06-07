"""Aggiornamento on-demand dei soli PREZZI per gli ISIN già in universo.

Il prezzo "Ultimo" arriva gratis dalla stessa tabella di ricerca: ri-eseguiamo
le query (profili veloci) e raccogliamo isin→prezzo per gli ISIN noti. Gli YTM
si ricalcolano a valle dai nuovi prezzi (non sono mai scaricati).
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional

import config
from scraper.search import ScrapeProgress, build_profiles, scrape_universe


def update_prices(
    known,
    *,
    valute: Optional[Iterable[str]] = None,
    include_zero_coupon: bool = False,
    headless: bool = True,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[ScrapeProgress], None]] = None,
) -> Dict[str, float]:
    """Ritorna {isin: ultimo_price} per gli ISIN noti ritrovati su BI.

    `known` può essere un DataFrame universo (usa le colonne isin/valuta) oppure
    un iterabile di ISIN. La scrittura su prices.parquet (data odierna) la fa
    data.store.save_prices().
    """
    if hasattr(known, "columns"):
        known_isins = set(known["isin"].dropna().astype(str).tolist())
        if valute is None and "valuta" in known.columns:
            vals = [v for v in known["valuta"].dropna().astype(str).unique()]
            valute = tuple(sorted(vals)) if vals else None
    else:
        known_isins = {str(x) for x in known}

    valute = tuple(valute) if valute else config.VALUTE
    profiles = build_profiles(valute=valute, include_zero_coupon=include_zero_coupon)

    prices: Dict[str, float] = {}
    for rec in scrape_universe(
        profiles, headless=headless, cancel_flag=cancel_flag, progress_cb=progress_cb
    ):
        if rec.isin in known_isins and rec.ultimo_price is not None:
            prices[rec.isin] = float(rec.ultimo_price)
    return prices
