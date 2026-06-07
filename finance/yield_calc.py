"""YTM **lordo** — sempre CALCOLATO, mai scaricato.

Approccio "Bilanciata":
  - la frequenza cedolare `freq` è ASSUNTA (configurabile; default annuale);
  - il rateo è STIMATO dallo schedule cedolare ricostruito dalla scadenza
    (nessuna visita alla scheda del singolo ISIN);
  - lo YTM si risolve numericamente con scipy.optimize.brentq.

Convenzione day-count: ACT/ACT di default (vedi finance.daycount), configurabile.
Tutte le funzioni sono pure e testabili in isolamento.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from scipy.optimize import brentq

from finance.daycount import add_months, coupon_dates_back_from_maturity, to_date, year_fraction

# Bracket per la ricerca della radice del TIR.
_RATE_LO = -0.20
_RATE_HI = 2.00


def accrued_interest(
    coupon_annual: float,
    freq: int,
    last_coupon_date,
    settlement_date,
    maturity_date=None,
    convention: str = "ACT/ACT",
) -> float:
    """Rateo cedolare maturato tra l'ultima cedola e il settlement.

    coupon_annual : cedola annua in % del nominale (es. 3.5 per 3,5%)
    freq          : cedole/anno (1 annuale, 2 semestrale). <=0 → zero-coupon → 0.
    last_coupon_date : se None, ricostruita da maturity_date + freq.
    Ritorna il rateo in unità di nominale=100 (cioè % del nominale).
    """
    if not coupon_annual or not freq or freq <= 0:
        return 0.0
    settle = to_date(settlement_date)
    last = to_date(last_coupon_date)
    mat = to_date(maturity_date)
    if settle is None:
        return 0.0
    if last is None:
        if mat is None:
            return 0.0
        last, nxt = coupon_dates_back_from_maturity(mat, freq, settle)
    else:
        nxt = add_months(last, 12 // freq)
    if last is None or nxt is None or nxt <= last:
        return 0.0
    period = year_fraction(last, nxt, convention)
    if period <= 0:
        return 0.0
    elapsed = year_fraction(last, settle, convention)
    frac = max(0.0, min(1.0, elapsed / period))
    coupon_period = coupon_annual / freq
    return coupon_period * frac


def build_coupon_times(
    settlement: date, maturity: date, freq: int, convention: str = "ACT/ACT"
) -> List[float]:
    """Tempi (in anni, da settlement) di tutte le cedole future, scadenza
    inclusa, ricostruiti a ritroso dalla scadenza in passi di 12//freq mesi.
    Ordinati crescenti. L'ultimo elemento è il tempo alla scadenza T."""
    times: List[float] = []
    if freq <= 0:
        return times
    step = 12 // freq
    d = maturity
    while True:
        t = year_fraction(settlement, d, convention)
        if t <= 0:
            break
        times.append(t)
        d = add_months(d, -step)
    times.sort()
    return times


def solve_tir(
    dirty_price: float,
    coupon_period: float,
    redemption: float,
    times: List[float],
    m: int,
    lo: float = _RATE_LO,
    hi: float = _RATE_HI,
) -> Optional[float]:
    """Risolve y in: dirty = Σ coupon_period/(1+y/m)^(m·t) + redemption/(1+y/m)^(m·T).

    `redemption` è il flusso finale (oltre alla cedola) pagato a T = times[-1].
    Ritorna None se gli estremi non bracketano (stesso segno) o input non validi.
    """
    if not times or dirty_price is None or dirty_price <= 0:
        return None
    last_t = times[-1]

    def pv(y: float) -> float:
        r = y / m
        base = 1.0 + r
        total = 0.0
        for t in times:
            total += coupon_period / base ** (m * t)
        total += redemption / base ** (m * last_t)
        return total

    try:
        f_lo = pv(lo) - dirty_price
        f_hi = pv(hi) - dirty_price
        if f_lo == 0.0:
            return lo
        if f_hi == 0.0:
            return hi
        if f_lo * f_hi > 0:
            return None
        return brentq(lambda y: pv(y) - dirty_price, lo, hi, maxiter=200, xtol=1e-10)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def ytm_gross(
    clean_price: float,
    coupon_annual: float,
    freq: int,
    maturity_date,
    settlement_date=None,
    redemption: float = 100.0,
    convention: str = "ACT/ACT",
) -> Optional[float]:
    """YTM lordo annuo (frazione, es. 0.034 = 3,4%).

    dirty = clean + rateo stimato; risolve il TIR con brentq.
    Zero-coupon (cedola 0 o freq<=0) → forma chiusa (R/dirty)^(1/T)-1.
    Ritorna None se input non validi o radice non bracketabile.
    """
    settle = to_date(settlement_date) or date.today()
    mat = to_date(maturity_date)
    if clean_price is None or clean_price <= 0 or mat is None:
        return None
    big_t = year_fraction(settle, mat, convention)
    if big_t <= 0:
        return None
    coupon_annual = float(coupon_annual or 0.0)

    # Zero-coupon
    if coupon_annual == 0.0 or not freq or freq <= 0:
        try:
            return (redemption / clean_price) ** (1.0 / big_t) - 1.0
        except (ValueError, ZeroDivisionError):
            return None

    m = int(freq)
    coupon_period = coupon_annual / m
    accr = accrued_interest(coupon_annual, m, None, settle, mat, convention)
    dirty = clean_price + accr
    times = build_coupon_times(settle, mat, m, convention)
    return solve_tir(dirty, coupon_period, redemption, times, m)
