"""Arricchimento dell'universo con le colonne calcolate di rendimento.

Aggiunge: anni_scadenza, ytm_lordo (%), ytm_netto (%). Gli YTM sono SEMPRE
calcolati (mai scaricati) dai prezzi correnti, con frequenza assunta e rateo
stimato (modalità "Bilanciata"). Funzione pura, testabile.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

import config
from finance.daycount import to_date, year_fraction
from finance.tax import ytm_net
from finance.yield_calc import ytm_gross


def _num(x) -> Optional[float]:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pct(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v * 100.0, 4)


def add_yield_columns(
    df: pd.DataFrame,
    *,
    freq: int = config.DEFAULT_COUPON_FREQ,
    convention: str = config.DEFAULT_DAYCOUNT,
    apply_bollo: bool = False,
    white_list: bool = True,
    settlement=None,
    price_col: str = "prezzo",
) -> pd.DataFrame:
    """Ritorna una copia di `df` con anni_scadenza, ytm_lordo, ytm_netto (%)."""
    out = df.copy()
    if out.empty:
        for c in ("anni_scadenza", "ytm_lordo", "ytm_netto"):
            out[c] = pd.Series(dtype="float64")
        return out

    settle = to_date(settlement) or date.today()

    def _anni(scad) -> Optional[float]:
        m = to_date(scad)
        if m is None:
            return None
        yf = year_fraction(settle, m, convention)
        return yf if yf > 0 else None

    def _row_gross(row) -> Optional[float]:
        return _pct(ytm_gross(
            _num(row.get(price_col)), _num(row.get("cedola_pct")), freq,
            row.get("scadenza"), settle, convention=convention,
        ))

    def _row_net(row) -> Optional[float]:
        return _pct(ytm_net(
            _num(row.get(price_col)), _num(row.get("cedola_pct")), freq,
            row.get("scadenza"), settle, row.get("categoria"),
            apply_bollo=apply_bollo, white_list=white_list, convention=convention,
        ))

    out["anni_scadenza"] = out["scadenza"].map(_anni)
    out["ytm_lordo"] = out.apply(_row_gross, axis=1)
    out["ytm_netto"] = out.apply(_row_net, axis=1)
    return out
