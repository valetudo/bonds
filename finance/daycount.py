"""Day-count e ricostruzione del calendario cedolare.

Convenzione day-count usata nel progetto: **ACT/ACT** (giorni effettivi / 365.25),
configurabile a **30/360**. Dichiarata esplicitamente qui come richiesto dalla spec.

Approccio "Bilanciata": lo schedule cedolare viene ricostruito a ritroso dalla
data di scadenza in passi di 12/freq mesi, SENZA visitare la scheda del singolo
ISIN. Per i plain vanilla le cedole cadono sull'anniversario della scadenza (e
sulle sue sotto-ricorrenze periodiche), quindi lo schedule è determinato da
(maturity_date, freq).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Optional, Tuple


def to_date(value) -> Optional[date]:
    """Coerce str 'YYYY-MM-DD' | date | datetime → date (o None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# alias interno per compatibilità
_to_date = to_date


def add_months(d: date, months: int) -> date:
    """Aggiunge `months` (anche negativi) a `d`, clampando il giorno alla fine
    del mese di destinazione (gestisce es. 31 gen − 1 mese → 28/29 feb)."""
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last_day))


def year_fraction(start: date, end: date, convention: str = "ACT/ACT") -> float:
    """Frazione d'anno tra due date.

    ACT/ACT : giorni effettivi / 365.25 (default).
    30/360  : convenzione 30/360 (US).
    """
    conv = (convention or "ACT/ACT").upper()
    if conv in ("30/360", "30E/360", "30/360 US"):
        d1, d2 = min(start.day, 30), end.day
        if d1 == 30:
            d2 = min(d2, 30)
        return (
            (end.year - start.year) * 360
            + (end.month - start.month) * 30
            + (d2 - d1)
        ) / 360.0
    # ACT/ACT (approssimazione /365.25)
    return (end - start).days / 365.25


def coupon_dates_back_from_maturity(
    maturity: date, freq: int, settlement: date
) -> Tuple[Optional[date], Optional[date]]:
    """Ritorna (ultima_cedola, prossima_cedola) che bracketano `settlement`,
    ricostruite a ritroso da `maturity` in passi di 12//freq mesi.

    Zero-coupon (freq<=0) → (None, None). Se settlement >= maturity → degenere.
    """
    if not freq or freq <= 0 or maturity is None or settlement is None:
        return (None, None)
    if settlement >= maturity:
        return (maturity, maturity)
    step = 12 // freq
    nxt = maturity
    prev = add_months(maturity, -step)
    while prev > settlement:
        nxt = prev
        prev = add_months(prev, -step)
    # prev <= settlement < nxt
    return (prev, nxt)
