"""Seconda fonte: mercato EuroTLX di Borsa Italiana.

A differenza di MOT (form JS → Selenium), EuroTLX espone i risultati da un GET
diretto: `risultati.html?category=<CODE>&currency=<CCY>&size&page`. Quindi qui si
usa **requests + BeautifulSoup** (niente browser).

Differenze gestite:
  - tassonomia per CATEGORIA strumento → mappata nei 4 bucket (config);
  - nessun filtro Struttura/Cedola/Subordinazione → eligibilità (plain vanilla,
    fissa, no callable) applicata dal NOME (qui inevitabile);
  - colonne tabella: ISIN | Descrizione | Ultimo | Cedola | Scadenza | Acquisto |
    Vendita → prezzo = Ultimo, altrimenti mid(bid, ask); cedola dal nome;
  - corp ita/estero e paese dal prefisso ISIN.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Iterator, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

import config
from scraper.search import (
    BondRecord,
    ScrapeProgress,
    _clean_text,
    _ISIN_RE,
    _normalise_date,
    _normalise_number,
    coupon_from_name,
    is_callable_from_name,
    paese_from_isin,
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "it-IT,it;q=0.9",
}

# Token che indicano strutture NON plain-vanilla-fissa (esclusi per nome).
_EXCL_TOKENS = (" TV ", " FRN ", " FLOAT ", " CMS ", " STEP ", " STRIP ")
_EXCL_SUBSTR = ("IND LINK", "INDEX", "INDICIZZ", "INFLAZ", "LINKER")


def is_eligible(name: str, include_zero_coupon: bool = False) -> bool:
    """True se il bond è plain-vanilla a cedola fissa (da tenere)."""
    if not name:
        return True
    if is_callable_from_name(name):
        return False
    up = f" {name.upper()} "
    if any(t in up for t in _EXCL_TOKENS):
        return False
    if any(s in up for s in _EXCL_SUBSTR):
        return False
    if not include_zero_coupon and (" ZC " in up or "ZERO COUPON" in up):
        return False
    return True


def build_eurotlx_profiles(
    valute: Tuple[str, ...] = config.VALUTE, include_zero_coupon: bool = False
) -> List[Tuple[str, str, str]]:
    """Lista di (category_code, bucket, valuta) da interrogare."""
    profs: List[Tuple[str, str, str]] = []
    for val in valute:
        for code, bucket in config.EUROTLX_CATEGORY_BUCKET.items():
            profs.append((code, bucket, val))
        if include_zero_coupon:
            for code, bucket in config.EUROTLX_ZERO_BUCKET.items():
                profs.append((code, bucket, val))
    return profs


def parse_eurotlx_html(html: str) -> List[dict]:
    """Parser tabella EuroTLX (7 colonne). Chiavi: isin, name, ultimo_price,
    coupon, maturity_date, bid, ask, url_scheda."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []
    seen: set = set()
    rows: List = []
    for a in soup.select("table a[href*='/scheda/']"):
        tr = a.find_parent("tr")
        if tr is not None and id(tr) not in {id(r) for r in rows}:
            rows.append(tr)
    if not rows:
        for tr in soup.select("table tr"):
            if _ISIN_RE.search(tr.get_text(" ", strip=True)):
                rows.append(tr)
    for tr in rows:
        cells = [_clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        full = " ".join(cells).upper()
        m = _ISIN_RE.search(full)
        if not m:
            continue
        isin = m.group(1)
        if isin in seen or "STRIP" in full:
            continue
        i = next((k for k, c in enumerate(cells) if isin in c.upper()), -1)
        if i < 0:
            continue

        def cell(off: int):
            return cells[i + off] if i + off < len(cells) else None

        out.append({
            "isin": isin,
            "name": cell(1) or isin,
            "ultimo_price": _normalise_number(cell(2)),
            "coupon": _normalise_number(cell(3)),
            "maturity_date": _normalise_date(cell(4) or "") or _normalise_date(" ".join(cells)),
            "bid": _normalise_number(cell(5)),
            "ask": _normalise_number(cell(6)),
            "url_scheda": _scheda_url(tr),
        })
        seen.add(isin)
    return out


def _scheda_url(tr) -> Optional[str]:
    a = tr.select_one("a[href*='/scheda/']")
    if a and a.get("href"):
        href = a["href"]
        return href if href.startswith("http") else "https://www.borsaitaliana.it" + href
    return None


def _price_from(rec: dict) -> Optional[float]:
    """Prezzo = Ultimo; fallback al mid(bid, ask) per i (molti) bond EuroTLX
    senza ultimo prezzo."""
    if rec.get("ultimo_price") is not None:
        return rec["ultimo_price"]
    bid, ask = rec.get("bid"), rec.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 4)
    return None


def _to_record(rec: dict, bucket: str, valuta: str, code: str) -> BondRecord:
    isin = rec["isin"]
    categoria, paese, fallback = bucket, None, False
    if bucket == "corp":
        fallback = True
        is_ita = isin[:2].upper() == "IT"
        categoria = "corp_ita" if is_ita else "corp_eur"
        paese = config.PAESE_ITALIA if is_ita else paese_from_isin(isin)
    elif bucket == "gov_eur":
        fallback = True
        paese = paese_from_isin(isin)
    elif bucket == "gov_ita":
        paese = config.PAESE_ITALIA
    cedola = coupon_from_name(rec.get("name") or "")
    if cedola is None:
        cedola = rec.get("coupon")
    return BondRecord(
        isin=isin, descrizione=rec.get("name") or isin, cedola_pct=cedola,
        scadenza=rec.get("maturity_date"), valuta=valuta, categoria=categoria,
        tipologia_bi=code, paese=paese, paese_da_isin_fallback=fallback,
        ultimo_price=_price_from(rec), url_scheda=rec.get("url_scheda"), mercato="EuroTLX",
    )


def _fetch_all_pages(session, code, valuta, cancel_flag, progress_cb, prog) -> List[dict]:
    all_rows: List[dict] = []
    seen: set = set()
    first_len: Optional[int] = None
    for page in range(1, config.MAX_PAGES_PER_PROFILE + 1):
        if cancel_flag and cancel_flag():
            break
        params = {"category": code, "currency": valuta, "lang": "it",
                  "size": str(config.EUROTLX_PAGE_SIZE), "page": str(page)}
        resp = session.get(config.EUROTLX_RESULTS_URL, params=params, timeout=30)
        if resp.status_code != 200:
            break
        page_rows = parse_eurotlx_html(resp.text)
        if first_len is None:
            first_len = len(page_rows)
        new = [r for r in page_rows if r["isin"] not in seen]
        if not new:
            break
        seen.update(r["isin"] for r in new)
        all_rows.extend(new)
        prog.page, prog.rows_so_far = page, len(all_rows)
        if progress_cb:
            progress_cb(prog)
        if first_len and len(page_rows) < first_len:
            break
        time.sleep(random.uniform(*config.EUROTLX_PAGE_DELAY))
    return all_rows


def scrape_eurotlx(
    profiles: List[Tuple[str, str, str]],
    *,
    include_zero_coupon: bool = False,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[ScrapeProgress], None]] = None,
) -> Iterator[BondRecord]:
    """Scrape EuroTLX via requests. Yielda BondRecord (mercato='EuroTLX') eleggibili
    e deduplicati. La categoria gov/corp viene dalla categoria EuroTLX (dai filtri);
    l'eligibilità plain-vanilla/fissa dal nome (qui inevitabile)."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    emitted: set = set()
    for code, bucket, valuta in profiles:
        if cancel_flag and cancel_flag():
            break
        prog = ScrapeProgress(f"{code} - {valuta}", bucket)
        if progress_cb:
            progress_cb(prog)
        try:
            rows = _fetch_all_pages(session, code, valuta, cancel_flag, progress_cb, prog)
        except Exception as exc:  # noqa: BLE001
            prog.error, prog.done = str(exc), True
            if progress_cb:
                progress_cb(prog)
            continue
        for rec in rows:
            if not is_eligible(rec.get("name") or "", include_zero_coupon):
                continue
            if rec["isin"] in emitted:
                continue
            emitted.add(rec["isin"])
            yield _to_record(rec, bucket, valuta, code)
        prog.done = True
        if progress_cb:
            progress_cb(prog)


def update_prices_eurotlx(
    known_isins,
    *,
    valute: Tuple[str, ...] = config.VALUTE,
    include_zero_coupon: bool = False,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[ScrapeProgress], None]] = None,
) -> dict:
    """{isin: prezzo} per gli ISIN EuroTLX noti, ri-scrapando l'endpoint."""
    known = {str(x) for x in known_isins}
    profiles = build_eurotlx_profiles(valute=tuple(valute), include_zero_coupon=include_zero_coupon)
    prices: dict = {}
    for rec in scrape_eurotlx(profiles, include_zero_coupon=include_zero_coupon,
                              cancel_flag=cancel_flag, progress_cb=progress_cb):
        if rec.isin in known and rec.ultimo_price is not None:
            prices[rec.isin] = float(rec.ultimo_price)
    return prices
