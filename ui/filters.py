"""Filtro condiviso dell'universo (funzione pura, testabile).

Usato dalla Overview per filtrare CONTEMPORANEAMENTE grafico e tabella con lo
stesso sottoinsieme di dati.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

import pandas as pd


def apply_filters(
    df: pd.DataFrame,
    *,
    categorie: Optional[Iterable[str]] = None,
    valute: Optional[Iterable[str]] = None,
    mercati: Optional[Iterable[str]] = None,
    anni_range: Optional[Tuple[float, float]] = None,
    ytm_range: Optional[Tuple[float, float]] = None,
    ycol: str = "ytm_netto",
    query: Optional[str] = None,
) -> pd.DataFrame:
    """Applica i filtri attivi e ritorna il sottoinsieme.

    Un argomento None/assente significa "non filtrare su quella dimensione".
    I range usano `between` (i valori NaN sulla colonna filtrata sono esclusi).
    `query` cerca (case-insensitive, substring) in isin e descrizione.
    """
    out = df
    if categorie is not None:
        out = out[out["categoria"].isin(list(categorie))]
    if valute is not None and "valuta" in out.columns:
        out = out[out["valuta"].isin(list(valute))]
    if mercati is not None and "mercato" in out.columns:
        out = out[out["mercato"].isin(list(mercati))]
    if anni_range is not None and "anni_scadenza" in out.columns:
        lo, hi = anni_range
        out = out[out["anni_scadenza"].between(lo, hi)]
    if ytm_range is not None and ycol in out.columns:
        lo, hi = ytm_range
        out = out[out[ycol].between(lo, hi)]
    if query:
        q = str(query).strip().lower()
        if q:
            desc = out["descrizione"].fillna("").astype(str).str.lower()
            isin = out["isin"].fillna("").astype(str).str.lower()
            out = out[desc.str.contains(q, regex=False) | isin.str.contains(q, regex=False)]
    return out
