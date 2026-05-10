"""Pure functions for yield, duration, and anomaly detection.

No I/O. Easy to unit-test.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, List, Optional


GOV_TAX_RATE = 0.125
CORP_TAX_RATE = 0.26


_GEO_BY_PREFIX = {
    "IT": "Italia",
    "FR": "Francia",
    "DE": "Germania",
    "ES": "Spagna",
    "AT": "Austria",
    "BE": "Belgio",
    "FI": "Finlandia",
    "GR": "Grecia",
    "IE": "Irlanda",
    "NL": "Olanda",
    "PT": "Portogallo",
    "SI": "Slovenia",
    "EU": "Sovranazionale",
    "US": "USA",
    "GB": "Regno Unito",
    "XS": "Eurobond/Intl",
}


def geo_area_from_isin(isin: str) -> str:
    prefix = (isin or "")[:2].upper()
    return _GEO_BY_PREFIX.get(prefix, "Altro")


# Currency extraction from bond names. Borsa Italiana uses both standard
# 3-letter ISO codes (USD, GBP) and a 2-letter shorthand (EU=EUR, US=USD).
_CCY_3LETTER = (
    "EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD",
    "SEK", "NOK", "DKK", "PLN", "TRY", "BRL", "MXN", "ZAR",
    "HUF", "CZK", "RON", "RUB", "CNY", "INR", "IDR", "PHP", "ITL",
)
_CCY_2LETTER = {
    "EU": "EUR", "US": "USD", "GB": "GBP", "CH": "CHF", "JP": "JPY",
    "CA": "CAD", "AU": "AUD", "NZ": "NZD", "SE": "SEK", "NO": "NOK",
    "DK": "DKK", "PL": "PLN", "TR": "TRY", "BR": "BRL", "ZA": "ZAR",
    "HU": "HUF", "CZ": "CZK", "RO": "RON", "RU": "RUB", "CN": "CNY",
    "IN": "INR", "MX": "MXN",
}


def currency_from_name(name: str, default: str = "EUR") -> str:
    """Heuristic currency detection from a bond description.

    Borsa Italiana names tend to embed either a 3-letter ISO code
    (e.g. "BANCO BPM 7.50 USD PERP") or a 2-letter shorthand
    (e.g. "BTP-1.65 OT2032 EU"). We try 3-letter codes first, then
    the 2-letter map, and fall back to `default` (EUR).
    """
    import re as _re
    if not name:
        return default
    upper = name.upper()
    # 3-letter codes (whole word)
    for code in _CCY_3LETTER:
        if _re.search(rf"\b{code}\b", upper):
            return code
    # 2-letter Borsa Italiana shorthand (only as standalone token)
    for short, full in _CCY_2LETTER.items():
        if _re.search(rf"\b{short}\b", upper):
            return full
    return default


# Country names that, when they appear at the START of a Borsa Italiana bond
# description, indicate the issuer is a sovereign (e.g. "Poland Tf 5,75%..."
# is a Polish government bond). White-list countries are taxed at 12.5% in
# Italy on coupons, like Italian govies.
_SOVEREIGN_PREFIXES = (
    # Eurozone & EU
    "FRANCE", "GERMANY", "ITALY", "ITALIA", "SPAIN", "BELGIUM", "NETHERLANDS",
    "PORTUGAL", "AUSTRIA", "FINLAND", "GREECE", "IRELAND", "SLOVENIA",
    "SLOVAKIA", "ESTONIA", "LATVIA", "LITHUANIA", "LUXEMBOURG", "CYPRUS",
    "MALTA", "CROATIA", "BULGARIA", "ROMANIA", "POLAND", "HUNGARY",
    "CZECH", "DENMARK", "SWEDEN", "NORWAY",
    # Anglo
    "UNITED KINGDOM", "UK ", "USA", "U.S.", "CANADA", "AUSTRALIA", "NEW ZEALAND",
    # Emerging
    "TURKEY", "MEXICO", "BRAZIL", "CHILE", "PERU", "COLOMBIA", "ARGENTINA",
    "INDIA", "INDONESIA", "PHILIPPINES", "SOUTH AFRICA", "JAPAN",
    "RUSSIA", "CHINA", "ICELAND", "ISRAEL", "EGYPT", "MOROCCO",
    "SERBIA", "MONTENEGRO", "ALBANIA", "MACEDONIA", "BOSNIA",
    "UKRAINE", "BELARUS",
)


def issuer_type_from_name(name: str) -> str:
    """Government if the name contains a sovereign or recognized supranational
    issuer token, OR starts with a sovereign country name.

    Italy's white-list rule taxes most supranationals and white-list sovereign
    issuers at 12.5%, same as Italian government bonds.
    """
    upper = (name or "").upper().strip()
    # Sovereign-issuer prefix check (e.g. "Poland Tf 5,75%..." → Polish govt)
    for prefix in _SOVEREIGN_PREFIXES:
        if upper.startswith(prefix):
            return "Government"
    govt_tokens = (
        # Sovereigns (Italian/EU shorthand and English)
        "BTP", "BOT", "CTZ", "CCT", "OAT", "BUND", "BUNDEI", "TREASURY", "GILT",
        "GGB",      # Greek Government Bond
        "BOBL",     # German Federal Notes (5y)
        "SCHATZ",   # German Federal Treasury Notes (2y)
        "JGB",      # Japan Government Bond
        "ACGB",     # Australian Commonwealth Government Bond
        "BONOS", "BONO ",   # Spanish Government Bonds
        "REPUBLIC", "REPUBBLICA", "SOVEREIGN", "GOVERNMENT", "GOVT",
        # Supranationals (Italian white-list, taxed at 12.5%)
        "EIB", "BEI ",            # European Investment Bank
        "ESM ",                   # European Stability Mechanism
        "EFSF",                   # European Financial Stability Facility
        "EBRD",                   # European Bank for Reconstruction and Development
        "EU NEXT GEN", "EU NEXTGEN",  # NextGenerationEU bonds (EU Commission)
        "WORLD BANK", "WORLDBANK", "IBRD", "IFC", "IDA",
        "ASIAN DEVELOPMENT", "ADB ",
        "AFDB", "AFRICAN DEVELOPMENT",
        "AIIB", "INTER-AMERICAN", "IADB",
        "EUROFIMA", "NIB ",       # Nordic Investment Bank
        "COUNCIL OF EUROPE",
    )
    if any(t in upper for t in govt_tokens):
        return "Government"
    return "Corporate"


# Map distinctive sovereign tokens (uppercase) → display nation name.
# A bond's *true* sovereign country is determined from the issuer's name,
# NOT the ISIN prefix (CUSIP US-prefix is handed out to supranationals,
# foreign sovereigns issuing in USD, etc., so it can't be trusted).
_SOVEREIGN_NATION_BY_TOKEN = (
    # (regex pattern with word boundary, nation label)
    (r"\bBTP[SI]?\b|\bBOT\b|\bCTZ\b|\bCCT\b|\bITALY\b|\bITALIA\b", "Italia"),
    (r"\bOAT(?:EI)?\b|\bFRANCE\b|\bFRANCIA\b", "Francia"),
    (r"\bBUND(?:EI)?\b|\bBOBL\b|\bSCHATZ\b|\bGERMANY\b|\bGERMANIA\b", "Germania"),
    (r"\bBONOS?\b|\bSPAIN\b|\bSPAGNA\b|\bOBLIGACIONES(?:EI)?\b", "Spagna"),
    (r"\bGGB\b|\bGREECE\b|\bGRECIA\b|\bHELLENIC\b", "Grecia"),
    (r"\bPOLAND\b|\bPOLONIA\b", "Polonia"),
    (r"\bPORTUGAL\b|\bPORTOGALLO\b", "Portogallo"),
    (r"\bAUSTRIA\b", "Austria"),
    (r"\bBELGIUM\b|\bBELGIO\b", "Belgio"),
    (r"\bNETHERLANDS\b|\bDUTCH STATE\b|\bNEDERLAND\b|\bOLANDA\b", "Olanda"),
    (r"\bFINLAND\b|\bFINLANDIA\b", "Finlandia"),
    (r"\bSLOVENIA\b", "Slovenia"),
    (r"\bSLOVAKIA\b", "Slovacchia"),
    (r"\bIRELAND\b|\bIRLANDA\b", "Irlanda"),
    (r"\bHUNGARY\b|\bUNGHERIA\b", "Ungheria"),
    (r"\bROMANIA\b", "Romania"),
    (r"\bCROATIA\b|\bCROAZIA\b", "Croazia"),
    (r"\bCYPRUS\b|\bCIPRO\b", "Cipro"),
    (r"\bBULGARIA\b", "Bulgaria"),
    (r"\bSERBIA\b", "Serbia"),
    (r"\bU\.S\. TREASURY\b|\bUS TREASURY\b|\bTREASURY NOTE\b|\bTREASURY BOND\b", "USA"),
    (r"\bGILT\b|\bUNITED KINGDOM\b|\bGRAN BRETAGNA\b", "Regno Unito"),
    (r"\bMEXICO\b|\bMESSICO\b", "Messico"),
    (r"\bBRAZIL\b|\bBRASILE\b", "Brasile"),
    (r"\bTURKEY\b|\bTURCHIA\b", "Turchia"),
    (r"\bJAPAN\b|\bGIAPPONE\b", "Giappone"),
)


def sovereign_nation_from_name(name: str) -> Optional[str]:
    """Return the sovereign country a bond's name identifies, or None.

    Detects:
      - Italian shorthand (BTP/BOT/CTZ → Italia, OAT → Francia, BUND →
        Germania, BONOS → Spagna, GGB → Grecia, ...)
      - Country names directly mentioned (Poland, Romania, ...)
      - English sovereign descriptors (Treasury, Gilt, ...)

    Returns None for supranationals (EIB, World Bank, EFSF, ESM, EBRD, ...),
    corporates (banks, utilities, ...), and anything ambiguous. The caller
    should treat None as "not eligible for sovereign-rating comparison".
    """
    import re as _re
    if not name:
        return None
    upper = name.upper()
    # Skip obvious supranationals so they don't accidentally match a country
    supranational_hints = ("EIB", "BEI ", "EFSF", "ESM ", "EBRD",
                           "WORLD BANK", "WORLDBANK", "IBRD", "IFC",
                           "AFDB", "AIIB", "EU NEXT GEN", "EU NEXTGEN",
                           "ASIAN DEVELOPMENT", "INTER-AMERICAN", "EUROFIMA")
    if any(s in upper for s in supranational_hints):
        return None
    for pattern, nation in _SOVEREIGN_NATION_BY_TOKEN:
        if _re.search(pattern, upper):
            return nation
    return None


def is_callable_from_name(name: str) -> bool:
    """Detect callable bonds by name. Borsa Italiana doesn't expose a
    reliable `callable=No` server filter (the backend treats NULL ≠ No
    so it wipes out every sovereign), and the issuer-side flag isn't on
    the description page either. The advanced-search description column
    DOES tag callable bonds with a standalone "Call" / "Callable" token,
    so we detect them by name. Used to flag (NOT drop) callable bonds —
    consumers that need a reliable YTM can filter them out at query time.
    """
    if not name:
        return False
    upper = name.upper()
    return any(
        f" {tok} " in f" {upper} " or upper.endswith(f" {tok}")
        for tok in ("CALL", "CALLABLE", "CALL.", "C/A")
    )


def is_inflation_linked(name: str) -> bool:
    """Detect inflation-linked bonds masquerading as Plain Vanilla on BI.

    BTPi (Italy), BTP€i, Bundei (Germany), OAT€i (France), TIPS (US),
    Linkers (UK) — Borsa Italiana sometimes lists them under Plain Vanilla
    even though their nominal coupon ≠ realised coupon. We exclude them
    from yield calculations because the real yield depends on inflation
    accrual that we don't track.
    """
    import re as _re
    if not name:
        return False
    upper = name.upper()
    # Common patterns: 'BTPi', 'BTPI', 'BTP I' (with separator),
    # 'BTP€I', 'OAT€I', 'BUNDEI', 'TIPS', 'LINKER', 'INFLAZ', 'INDICIZZ'
    if _re.search(r"\bBTPI\b", upper):
        return True
    if _re.search(r"\bBTP[€E]I\b", upper):
        return True
    if "BUNDEI" in upper:
        return True
    if _re.search(r"\bOAT[€E]I\b", upper):
        return True
    if _re.search(r"\bOATEI\b", upper):
        return True
    if "TIPS" in upper.split():
        return True
    if "LINKER" in upper or "LINKED" in upper:
        return True
    if "INFLAZ" in upper or "INDICIZZ" in upper:
        return True
    return False


def coupon_from_name(name: str) -> Optional[float]:
    """Extract the annual coupon (%) from a Borsa Italiana bond description.

    Strategies, in order:
      1. Zero-coupon markers ("Zc", "Zero Coupon")          → 0.0
      2. "<number>%" anywhere                                → number
      3. "Tf <number>" / "TF <number>" (tasso fisso prefix) → number
      4. Trailing decimal at end of name (old OAT style)     → number

    Returns None if nothing parseable. Sanity range: [0, 25].

    Reliable than the table's CEDOLA column on borsaitaliana, which on many
    rows shows the *periodic* (e.g., semestrale = annual/2) rather than the
    annual coupon.
    """
    import re as _re
    if not name:
        return None
    upper = name.upper()
    tokens = _re.split(r"\s+", upper)
    if "ZC" in tokens or any(t.startswith("ZC") for t in tokens):
        return 0.0
    if "ZERO" in tokens and ("COUPON" in tokens or "CPN" in tokens):
        return 0.0

    def _maybe(value: float) -> Optional[float]:
        return value if 0 <= value <= 25 else None

    # Pattern 1: number followed by '%'
    m = _re.search(r"(\d+(?:[,.]\d+)?)\s*%", name)
    if m:
        raw = m.group(1).replace(",", ".")
        try:
            return _maybe(float(raw))
        except ValueError:
            pass

    # Pattern 2: "Tf <number>" or "TF <number>"
    m = _re.search(r"\bTF\s+(\d+(?:[,.]\d+)?)", upper)
    if m:
        raw = m.group(1).replace(",", ".")
        try:
            return _maybe(float(raw))
        except ValueError:
            pass

    # Pattern 3: trailing decimal at the very end (e.g. "Oat Oct32 Eur 5,75")
    if tokens:
        last = tokens[-1]
        if _re.match(r"^\d+[,.]\d+$", last):
            try:
                v = float(last.replace(",", "."))
                if 0 < v <= 25:
                    return v
            except ValueError:
                pass
    return None


def years_to_maturity(maturity_date: Optional[str], reference: Optional[date] = None) -> Optional[float]:
    """Return years between today and maturity date string (YYYY-MM-DD).

    Returns None if input is missing/invalid or in the past.
    """
    if not maturity_date:
        return None
    try:
        target = datetime.strptime(str(maturity_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = reference or date.today()
    delta = (target - today).days / 365.25
    if delta <= 0:
        return None
    return delta


def duration_bucket(years: Optional[float]) -> str:
    if years is None:
        return "N/D"
    if years < 3:
        return "Short (<3y)"
    if years <= 7:
        return "Medium (3-7y)"
    return "Long (>7y)"


def _ytm_bisection(
    price: float,
    coupon: float,
    years: float,
    *,
    face: float = 100.0,
    lo: float = -0.20,
    hi: float = 2.00,
    iterations: int = 80,
) -> Optional[float]:
    """Solve for the annual rate `r` such that the present value of the bond's
    cash flows equals `price`. Returns the rate as a fraction (e.g. 0.0336 = 3.36%).

    Cash flows assumed: `coupon` paid each year for `years` years, plus `face`
    at maturity. Annual compounding. Last coupon is paid at maturity along with
    the face value (works for non-integer years too).
    """
    if price is None or price <= 0 or years is None or years <= 0:
        return None
    if coupon is None:
        coupon = 0.0

    def pv(r: float) -> float:
        if abs(r) < 1e-12:
            return coupon * years + face
        annuity = coupon * (1.0 - (1.0 + r) ** (-years)) / r
        return annuity + face * (1.0 + r) ** (-years)

    a, b = lo, hi
    fa = pv(a) - price
    fb = pv(b) - price
    if fa * fb > 0:
        # Both same sign — no root in [lo, hi]; fall back to None.
        return None
    for _ in range(iterations):
        mid = 0.5 * (a + b)
        fm = pv(mid) - price
        if fa * fm <= 0:
            b, fb = mid, fm
        else:
            a, fa = mid, fm
        if abs(b - a) < 1e-10:
            break
    return 0.5 * (a + b)


def net_annual_yield(
    coupon: Optional[float],
    price: Optional[float],
    maturity_date: Optional[str],
    issuer_type: Optional[str],
    reference: Optional[date] = None,
) -> Optional[float]:
    """Net Yield To Maturity (after-tax), as a percent.

    Mirrors Borsa Italiana's "Rendimento Effettivo Netto" methodology:
        - Coupons taxed at the issuer rate (Government 12.5%, Corporate 26%);
          we reduce each coupon to `coupon * (1 - tax)`.
        - Face value at maturity is left untaxed (BI does the same — the
          implicit capital gain on a discount bond is *not* withheld at
          source on Italian bonds for individual investors).
        - YTM solved via bisection on annual cash flows.

    The formula is far more accurate than the simple-yield approximation
    used previously, especially for premium/discount bonds with several
    years to maturity. For very short maturities (<3-6 months) the annual
    cash-flow approximation can over-state YTM; those bonds are excluded
    from the per-nation aggregations by `yield_by_nation`.
    """
    if price is None or price <= 0:
        return None
    years = years_to_maturity(maturity_date, reference)
    if years is None or years <= 0:
        return None
    tax = GOV_TAX_RATE if (issuer_type or "").lower() == "government" else CORP_TAX_RATE
    net_coupon = float(coupon or 0) * (1.0 - tax)
    r = _ytm_bisection(price, net_coupon, years)
    if r is None:
        return None
    return r * 100.0


def enrich_bond(bond: dict, reference: Optional[date] = None) -> dict:
    """Return a copy of `bond` with derived fields set:

    issuer_type (if missing), geo_area (if missing), years_to_maturity,
    duration_bucket, net_yield_pa, inflation_linked.

    Inflation-linked bonds get net_yield_pa = None (real yield can't be
    computed from nominal coupon alone) and a flag for downstream filtering.
    """
    out = dict(bond)
    if not out.get("issuer_type"):
        out["issuer_type"] = issuer_type_from_name(out.get("name", ""))
    if not out.get("geo_area"):
        out["geo_area"] = geo_area_from_isin(out.get("isin", ""))
    out["years_to_maturity"] = years_to_maturity(out.get("maturity_date"), reference)
    out["duration_bucket"] = duration_bucket(out["years_to_maturity"])
    out["inflation_linked"] = is_inflation_linked(out.get("name", ""))
    out["is_callable"] = is_callable_from_name(out.get("name", ""))
    # Authoritative sovereign nation: prefer the value persisted at scrape
    # time (the BI Paese filter we applied), fall back to the name-based
    # regex for legacy rows where `nation` is NULL.
    out["sovereign_nation"] = (
        bond.get("nation")
        or sovereign_nation_from_name(out.get("name", ""))
    )
    if out["inflation_linked"]:
        out["net_yield_pa"] = None
    else:
        out["net_yield_pa"] = net_annual_yield(
            out.get("coupon"),
            out.get("latest_price") if out.get("latest_price") is not None else out.get("price"),
            out.get("maturity_date"),
            out["issuer_type"],
            reference,
        )
    return out


def average_yield(bonds: Iterable[dict]) -> Optional[float]:
    values = [b.get("net_yield_pa") for b in bonds if b.get("net_yield_pa") is not None]
    return sum(values) / len(values) if values else None


SOVEREIGN_TIPOLOGIE = (
    "Titoli Di Stato Italiani",
    "Titoli Di Stato Esteri",
    "Eurobonds Republic Of Italy",
)


def yield_by_nation(
    bonds: Iterable[dict],
    min_count: int = 1,
    *,
    min_years: float = 0.75,
    years_range: Optional[tuple] = None,
    yield_range: tuple = (0.1, 15.0),
    currency: Optional[str] = None,
    sovereign_only: bool = False,
    tipologie: Optional[Iterable[str]] = None,
) -> List[dict]:
    """Aggregate net yield by nation, sorted by median yield descending.

    Filters:
      - net_yield_pa is None         -> dropped
      - years_to_maturity < min_years -> dropped
      - years_to_maturity outside `years_range` (if given)
      - yield outside `yield_range`
      - currency != `currency` (if given, e.g. "EUR")
      - tipologia not in `tipologie` (if given) — uses the authoritative
        Borsa Italiana classification stored on each bond
      - if `sovereign_only`, defaults `tipologie` to the three sovereign
        Borsa Italiana categories and falls back to `sovereign_nation`
        (name-derived) for country grouping when `tipologia` is missing.

    Group key: `sovereign_nation` if sovereign_only; otherwise `geo_area`.
    """
    by_nation: dict = {}
    lo, hi = yield_range
    yr_lo, yr_hi = (years_range if years_range is not None else (None, None))
    want_ccy = (currency or "").upper() if currency else None
    if sovereign_only and tipologie is None:
        tipologie = SOVEREIGN_TIPOLOGIE
    want_tipologie = set(tipologie) if tipologie is not None else None
    for b in bonds:
        y = b.get("net_yield_pa")
        if y is None:
            continue
        years = b.get("years_to_maturity")
        if years is None or years < min_years:
            continue
        if yr_lo is not None and (years < yr_lo or years > yr_hi):
            continue
        if y < lo or y > hi:
            continue
        if want_ccy and (b.get("currency") or "").upper() != want_ccy:
            continue
        if want_tipologie is not None:
            tip = b.get("tipologia")
            if tip not in want_tipologie:
                continue
        if sovereign_only:
            nation = b.get("sovereign_nation")
            if not nation:
                continue
        else:
            nation = b.get("geo_area") or "Altro"
        by_nation.setdefault(nation, []).append(float(y))
    out: List[dict] = []
    for geo, yields in by_nation.items():
        if len(yields) < min_count:
            continue
        sorted_y = sorted(yields)
        n = len(sorted_y)
        median = (
            sorted_y[n // 2]
            if n % 2 == 1
            else (sorted_y[n // 2 - 1] + sorted_y[n // 2]) / 2
        )
        out.append({
            "nation": geo,
            "count": n,
            "avg": sum(yields) / n,
            "min": sorted_y[0],
            "max": sorted_y[-1],
            "median": median,
        })
    out.sort(key=lambda r: r["avg"], reverse=True)
    return out


def find_anomalies(
    bonds: Iterable[dict],
    *,
    window_years: float = 1.0,
    min_peers: int = 2,
    top_n: int = 2,
) -> List[dict]:
    """Top-N Italian Government EUR bonds whose yield exceeds the mean of
    peers within +/- window_years of duration the most."""
    pool = [
        b for b in bonds
        if (b.get("geo_area") or "").lower() == "italia"
        and (b.get("currency") or "").upper() == "EUR"
        and (b.get("issuer_type") or "").lower() == "government"
        and b.get("years_to_maturity") and b.get("years_to_maturity") > 0
        and b.get("net_yield_pa") is not None
    ]
    if len(pool) < min_peers + 1:
        return []
    out = []
    for p in pool:
        peers = [
            q for q in pool
            if q["isin"] != p["isin"]
            and abs(q["years_to_maturity"] - p["years_to_maturity"]) <= window_years
        ]
        if len(peers) < min_peers:
            continue
        peer_mean = sum(q["net_yield_pa"] for q in peers) / len(peers)
        out.append({
            "isin": p["isin"],
            "name": p.get("name"),
            "maturity_date": p.get("maturity_date"),
            "years": p["years_to_maturity"],
            "yield": p["net_yield_pa"],
            "peer_mean": peer_mean,
            "peer_count": len(peers),
            "spread": p["net_yield_pa"] - peer_mean,
        })
    out.sort(key=lambda x: x["spread"], reverse=True)
    return out[:top_n]
