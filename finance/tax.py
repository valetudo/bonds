"""Rendimento **netto** (after-tax) — regime fiscale italiano 2026.

Aliquote come costanti CONFIGURABILI in testa (possono cambiare con le leggi di
bilancio). L'aliquota deriva dalla **CATEGORIA** (ricavata dai filtri di Borsa
Italiana), NON dal nome del bond.

Nota: calcolo finanziario indicativo, non consulenza fiscale. Le aliquote sono
quelle vigenti nel 2026 e vanno verificate nel tempo.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from finance.daycount import to_date, year_fraction
from finance.yield_calc import accrued_interest, build_coupon_times, solve_tir

# ── Aliquote vigenti (configurabili) ───────────────────────────────────────────
ALIQUOTA_GOV = 0.125    # Titoli di Stato IT, esteri white-list, Eurobonds Republic of Italy
ALIQUOTA_CORP = 0.26    # Banche, Corporate, Secured
BOLLO_ANNUO = 0.002     # imposta di bollo annua sul controvalore di mercato (0,2%)

_GOV_CATEGORIES = {"gov_ita", "gov_eur"}
_CORP_CATEGORIES = {"corp_ita", "corp_eur"}


def aliquota_for(categoria: str, white_list: bool = True) -> float:
    """Aliquota sostitutiva in base alla categoria (dai filtri).

    gov_ita / gov_eur → 12,5%  (gov_eur al 12,5% solo se white-list, altrimenti 26%).
    corp_ita / corp_eur → 26%.
    """
    cat = (categoria or "").lower()
    if cat in _GOV_CATEGORIES:
        if cat == "gov_eur" and not white_list:
            return ALIQUOTA_CORP
        return ALIQUOTA_GOV
    return ALIQUOTA_CORP


def ytm_net(
    clean_price: float,
    coupon_annual: float,
    freq: int,
    maturity_date,
    settlement_date,
    categoria: str,
    redemption: float = 100.0,
    apply_bollo: bool = False,
    white_list: bool = True,
    convention: str = "ACT/ACT",
) -> Optional[float]:
    """YTM netto annuo (frazione) sui flussi al netto delle imposte.

    Modello (come da prompt):
      - cedola netta per periodo = (coupon/m) · (1 − aliquota);
      - plusvalenza a scadenza tassata se redemption > clean_price
        (minusvalenza ignorata nel modello base);
      - se apply_bollo: sottrae BOLLO_ANNUO · controvalore per ogni anno di
        detenzione (modellato come riduzione del flusso finale).
    dirty = clean + rateo (lordo), coerente con ytm_gross.
    """
    settle = to_date(settlement_date) or date.today()
    mat = to_date(maturity_date)
    if clean_price is None or clean_price <= 0 or mat is None:
        return None
    big_t = year_fraction(settle, mat, convention)
    if big_t <= 0:
        return None

    aliq = aliquota_for(categoria, white_list)
    coupon_annual = float(coupon_annual or 0.0)

    # Plusvalenza a scadenza (la minus non genera credito nel modello base).
    gain = redemption - clean_price
    tax_on_gain = gain * aliq if gain > 0 else 0.0
    redemption_net = redemption - tax_on_gain

    # Imposta di bollo: 0,2%/anno sul controvalore (approssimato dal corso secco).
    if apply_bollo:
        redemption_net -= BOLLO_ANNUO * clean_price * big_t

    # Zero-coupon
    if coupon_annual == 0.0 or not freq or freq <= 0:
        try:
            return (redemption_net / clean_price) ** (1.0 / big_t) - 1.0
        except (ValueError, ZeroDivisionError):
            return None

    m = int(freq)
    coupon_period_net = (coupon_annual / m) * (1.0 - aliq)
    accr = accrued_interest(coupon_annual, m, None, settle, mat, convention)
    dirty = clean_price + accr
    times = build_coupon_times(settle, mat, m, convention)
    return solve_tir(dirty, coupon_period_net, redemption_net, times, m)
