"""Selenium scraper for borsaitaliana.it advanced bond search.

Single source: https://www.borsaitaliana.it/borsa/obbligazioni/ricerca-avanzata.html
We let the site itself filter callable / floating / inflation-linked / strip
bonds out, by setting Struttura=Plain Vanilla and Tipo Cedola=Fisso (or Zero
Coupon). Each row already contains ISIN | Descrizione | ULTIMO | CEDOLA |
SCADENZA, so we never visit individual bond pages.

Public entry points
-------------------
run_scrape(db, ...)   : full sync of both profiles (fixed_vanilla + zero_coupon)
parse_results_html(...): pure-function parser, exposed for testability
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from bs4 import BeautifulSoup

from calculations import (
    coupon_from_name,
    currency_from_name,
    geo_area_from_isin,
    is_callable_from_name,
    issuer_type_from_name,
)


log = logging.getLogger(__name__)

ADVANCED_SEARCH_URL = (
    "https://www.borsaitaliana.it/borsa/obbligazioni/ricerca-avanzata.html"
)

# Anti-ban delay between pages (seconds). Kept minimal as the user asked.
PAGE_DELAY_MIN = 0.6
PAGE_DELAY_MAX = 1.5
WAIT_FORM_SECONDS = 15
WAIT_RESULTS_SECONDS = 20
WAIT_DROPDOWN_SECONDS = 6


# ──────────────────────────────────────────────────────────────────────────────
# Scrape profiles
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class FilterStep:
    """One filter to apply on the advanced-search form.

    `select_id` is the DOM id of the underlying <select> on the
    borsaitaliana ricerca-avanzata page. `option_text` is the visible
    text of the <option> to pick (case-insensitive substring match
    is used at runtime).
    """
    select_id: str
    option_text: str
    label: str  # human-friendly name for logging


@dataclass(frozen=True)
class ScrapeProfile:
    name: str
    label: str
    steps: Tuple[FilterStep, ...] = field(default_factory=tuple)


# Selector IDs come from the live DOM diagnostic dump (see logs).
#   structures   -> "Struttura"     ['Plain Vanilla', 'Inflation Linked', ...]
#   typologies   -> "Tipologia"     ['Banche', 'Corporate', 'Eurobonds Republic Of Italy',
#                                    'Secured', 'Titoli Di Stato Esteri', 'Titoli Di Stato Italiani']
#   types        -> "Tipo Cedola"   ['Titolo Con Cedole Tf', 'Zero Coupon', ...]
#   callable     -> "Rimborso Anticipato"  ['Sì', 'No']

# Six authoritative typologies offered by Borsa Italiana's advanced search.
TIPOLOGIE: Tuple[str, ...] = (
    "Titoli Di Stato Italiani",
    "Titoli Di Stato Esteri",
    "Eurobonds Republic Of Italy",
    "Banche",
    "Corporate",
    "Secured",
)

# Coupon types we screen for.
CEDOLA_TYPES: Tuple[Tuple[str, str], ...] = (
    ("fixed", "Titolo Con Cedole Tf"),
    ("zero", "Zero Coupon"),
)

# For "Titoli Di Stato Esteri" we additionally split by Paese — that single
# tipology aggregates the bonds of all foreign sovereigns, so without
# splitting the 20-row first-page cap loses most countries. Each per-country
# sub-profile easily fits in one page.
ESTERI_COUNTRIES: Tuple[str, ...] = (
    # Full set sourced from BI's Paese dropdown on the advanced-search
    # page. Each option text matches the dropdown label verbatim — the
    # scraper's _set_select_by_id() uses option text, so any deviation
    # would fail the filter step.
    "Italia",
    # Eurozone core
    "Francia", "Germania", "Spagna", "Austria", "Belgio", "Olanda",
    "Portogallo", "Finlandia", "Irlanda", "Lussemburgo",
    # Eurozone periphery
    "Grecia", "Cipro", "Slovenia", "Estonia", "Lettonia", "Lituania",
    # CEE (mostly EUR + own currency)
    "Polonia", "Ungheria", "Romania", "Bulgaria", "Croazia",
    # Non-eurozone Europe
    "Gran Bretagna", "Svizzera", "Norvegia", "Svezia",
    # Non-Europe
    "Stati Uniti", "Canada", "Cina, Repubblica Popolare Della",
    "Filippine", "Honduras", "Costa D Avorio", "Jersey C.i.",
)


def _slugify(text: str) -> str:
    return (
        text.lower()
        .replace("è", "e").replace("ì", "i").replace("ò", "o").replace("à", "a")
        .replace(" ", "_").replace("'", "").replace(".", "")
    )


def _build_profiles() -> List[ScrapeProfile]:
    """Build the per-(Tipologia × Cedola) scrape profiles.

    Pre-filters always applied server-side:
      - Struttura = Plain Vanilla        (excludes Strutturate Su Tassi,
                                          Index Linked, Inflation Linked
                                          incl. BTP€i / Bundei / OAT€i)
      - Subordinazione = No              (excludes Tier1/Tier2/Sub bonds —
                                          safe on sovereigns: their NULL
                                          subordination field is treated
                                          as "not subordinated" by BI)

    NOT applied server-side:
      - "Rimborso Anticipato = No": the BI backend treats NULL ≠ "No", so
        this filter wipes out every sovereign (the field is unset for them
        — verified empirically: BTP+Rimborso=No returns 0 rows). We strip
        callable bonds client-side instead, via is_callable_from_name().

    "Titoli Di Stato Esteri" is exploded into one sub-profile per Paese
    because that single bucket aggregates *all* foreign sovereigns; without
    splitting, the per-page noise would mix issuers from different countries
    and we'd lose the per-nation attribution we now persist.
    """
    profiles: List[ScrapeProfile] = []
    for tipologia in TIPOLOGIE:
        for cedola_short, cedola_value in CEDOLA_TYPES:
            base_steps = (
                FilterStep("structures", "Plain Vanilla", "Struttura"),
                FilterStep("typologies", tipologia, "Tipologia"),
                FilterStep("types", cedola_value, "Tipo Cedola"),
                FilterStep("subordination", "No", "Subordinazione"),
            )
            if tipologia == "Titoli Di Stato Esteri":
                for country in ESTERI_COUNTRIES:
                    profiles.append(ScrapeProfile(
                        name=f"{_slugify(tipologia)}__{cedola_short}__{_slugify(country)}",
                        label=f"{tipologia} – {country} – {cedola_value}",
                        steps=base_steps + (
                            FilterStep("countries", country, "Paese"),
                        ),
                    ))
            else:
                profiles.append(ScrapeProfile(
                    name=f"{_slugify(tipologia)}__{cedola_short}",
                    label=f"{tipologia} – {cedola_value}",
                    steps=base_steps,
                ))
    return profiles


SCRAPE_PROFILES: List[ScrapeProfile] = _build_profiles()


def _profile_tipologia(profile: ScrapeProfile) -> Optional[str]:
    for step in profile.steps:
        if step.select_id == "typologies":
            return step.option_text
    return None


def _profile_nation(profile: ScrapeProfile) -> Optional[str]:
    """Authoritative sovereign nation for a profile, taken from the filters
    we applied at scrape time. None for non-sovereign tipologie."""
    tipologia = _profile_tipologia(profile)
    if tipologia in ("Titoli Di Stato Italiani", "Eurobonds Republic Of Italy"):
        return "Italia"
    if tipologia == "Titoli Di Stato Esteri":
        for step in profile.steps:
            if step.select_id == "countries":
                return step.option_text
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Pure parsing helpers (testable without Selenium)
# ──────────────────────────────────────────────────────────────────────────────
_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{10})\b")
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})")


def _normalise_number(raw: str) -> Optional[float]:
    if raw is None:
        return None
    cleaned = str(raw).replace("\xa0", " ").strip()
    if not cleaned or cleaned in {"-", "--", "N.A.", "N/A"}:
        return None
    cleaned = re.sub(r"[^0-9,.-]", "", cleaned)
    if not cleaned or cleaned in {"-", "--"}:
        return None
    if "," in cleaned and "." in cleaned:
        # Italian thousands (.) + decimal (,) when comma is rightmost
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalise_date(raw: str) -> Optional[str]:
    """Return 'YYYY-MM-DD' or None.

    Accepts dd/mm/yyyy or yyyy-mm-dd substrings.
    """
    if not raw:
        return None
    m = _DATE_RE.search(str(raw))
    if not m:
        return None
    s = m.group(1)
    try:
        if "/" in s:
            d, mo, y = s.split("/")
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        # already iso-like
        y, mo, d = s.split("-")
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return None


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_results_html(html: str) -> List[dict]:
    """Extract one record per result-row from the advanced-search table.

    Each row layout (Borsa Italiana standard):
        ISIN | DESCRIZIONE | ULTIMO | CEDOLA | SCADENZA

    Returns dicts with keys: isin, name, ultimo_price, coupon, maturity_date.
    Strip-bonds (name contains 'STRIP') are dropped — they are zero-coupons
    that aren't tradable as ordinary fixed-income for our purposes.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []
    seen: set = set()

    rows: List = []
    for anchor in soup.select("table a[href*='/scheda/']"):
        row = anchor.find_parent("tr")
        if row is not None and id(row) not in {id(r) for r in rows}:
            rows.append(row)

    if not rows:
        # Fallback: try all rows that contain an ISIN-shaped token
        for tr in soup.select("table tr"):
            text = tr.get_text(" ", strip=True)
            if _ISIN_RE.search(text):
                rows.append(tr)

    for tr in rows:
        cells = [_clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        full_text = " ".join(cells).upper()
        m = _ISIN_RE.search(full_text)
        if not m:
            continue
        isin = m.group(1)
        if isin in seen:
            continue
        if "STRIP" in full_text:
            continue
        # Find the cell that contains the ISIN and use the next cells as
        # description / ultimo / cedola / scadenza in the standard layout.
        isin_idx = next(
            (i for i, c in enumerate(cells) if isin in c.upper()),
            -1,
        )
        if isin_idx < 0:
            continue
        name = cells[isin_idx + 1] if isin_idx + 1 < len(cells) else ""
        ultimo = _normalise_number(cells[isin_idx + 2]) if isin_idx + 2 < len(cells) else None
        coupon = _normalise_number(cells[isin_idx + 3]) if isin_idx + 3 < len(cells) else None
        maturity = (
            _normalise_date(cells[isin_idx + 4]) if isin_idx + 4 < len(cells) else None
        ) or _normalise_date(" ".join(cells))

        out.append({
            "isin": isin,
            "name": name or isin,
            "ultimo_price": ultimo,
            "coupon": coupon,
            "maturity_date": maturity,
        })
        seen.add(isin)
    return out


def detect_pagination_state(html: str) -> Tuple[Optional[int], Optional[int], bool]:
    """Inspect the results page for pagination markers.

    Returns (current_page, total_pages, has_next). Any field may be None
    when the markup doesn't expose it; `has_next` falls back to the
    presence of a 'Successiva' link.
    """
    soup = BeautifulSoup(html, "html.parser")
    current: Optional[int] = None
    total: Optional[int] = None
    has_next = False

    for span in soup.select("li.m-pagination__item--current, li.active, .current"):
        m = re.search(r"\b(\d+)\b", span.get_text(" ", strip=True))
        if m:
            current = int(m.group(1))
            break

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"(?i)pagina\s+(\d+)\s+di\s+(\d+)", page_text)
    if m:
        current = current or int(m.group(1))
        total = int(m.group(2))

    for a in soup.select("a"):
        title = (a.get("title") or "").lower()
        text = a.get_text(" ", strip=True).lower()
        if "successiva" in title or text in {"successiva", ">", "»", "next"}:
            href = a.get("href") or ""
            if href and "disabled" not in (a.get("class") or []):
                has_next = True
                break
    return current, total, has_next


# ──────────────────────────────────────────────────────────────────────────────
# Selenium driver helpers
# ──────────────────────────────────────────────────────────────────────────────
def _build_chrome_driver(headless: bool = True):
    """Build a Chrome webdriver. Imports kept local so the module loads even
    when Selenium isn't installed yet (useful for unit tests that exercise
    only the parser)."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    import os
    os.environ.setdefault("WDM_SSL_VERIFY", "0")
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=it-IT")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.page_load_strategy = "eager"
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        },
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(WAIT_RESULTS_SECONDS + 5)
    return driver


def _dismiss_cookie_banner(driver) -> None:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    selectors = [
        (By.XPATH, "//button[contains(., 'Rifiuta tutti i cookie di profilazione')]"),
        (By.XPATH, "//button[contains(@aria-label, 'Close Cookie Control')]"),
        (By.XPATH, "//button[contains(., 'Salva preferenze')]"),
        (By.XPATH, "//button[contains(., 'Accetta')]"),
    ]
    for by, sel in selectors:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, sel)))
            driver.execute_script("arguments[0].click();", btn)
            log.info("[scraper] Cookie banner dismissed via selector %s", sel)
            return
        except Exception:
            continue


# ──────────────────────────────────────────────────────────────────────────────
# Select-setting JS — finds the <select> by DOM id, picks the matching option
# (case-insensitive contains), then dispatches input/change events so any
# Select2 / Chosen / jQuery listener picks it up.
# ──────────────────────────────────────────────────────────────────────────────
_SET_SELECT_JS = r"""
const selectId = arguments[0];
const optionText = arguments[1];
function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
const select = document.getElementById(selectId);
if (!select){ return {ok: false, reason: 'no element with id="' + selectId + '"'}; }
if (select.tagName !== 'SELECT'){
  return {ok: false, reason: 'element id="' + selectId + '" is not a <select> (' + select.tagName + ')'};
}
const opts = Array.from(select.options);
const want = optionText.toLowerCase();
let matched = opts.find(o => norm(o.text).toLowerCase() === want);
if (!matched) matched = opts.find(o => norm(o.text).toLowerCase().includes(want));
if (!matched){
  return {ok: false, reason: 'option "' + optionText + '" not in [' + opts.map(o => norm(o.text)).join(' | ') + ']'};
}
// Deselect everything first — this is essential when the underlying <select>
// is `multiple` (Select2 multi-select). Otherwise previously-selected options
// remain active and the filter combines instead of replacing.
for (const o of opts) o.selected = false;
matched.selected = true;
select.value = matched.value;
try { select.dispatchEvent(new Event('input', {bubbles: true})); } catch(e) {}
try { select.dispatchEvent(new Event('change', {bubbles: true})); } catch(e) {}
if (window.jQuery){
  try { window.jQuery(select).val([matched.value]).trigger('change'); } catch(e) {}
}
const finalSelected = Array.from(select.selectedOptions).map(o => norm(o.text));
return {ok: true, value: matched.value, text: norm(matched.text),
        is_multiple: select.multiple, final_selection: finalSelected};
"""

_DUMP_SELECTS_JS = r"""
return Array.from(document.querySelectorAll('select')).map(s => ({
  id: s.id || null,
  name: s.name || null,
  options: Array.from(s.options).slice(0, 30).map(o => (o.text || '').trim())
}));
"""


def _set_select_by_id(driver, select_id: str, option_text: str, label: str = "") -> bool:
    """Set the <select id=`select_id`> to the option whose visible text
    matches `option_text` (substring, case-insensitive). Triggers events
    so any Select2 / jQuery listener picks up the change."""
    log_label = f"{label}({select_id})" if label else select_id
    try:
        result = driver.execute_script(_SET_SELECT_JS, select_id, option_text)
    except Exception as exc:
        log.error("[scraper] %s = '%s': JS error: %s", log_label, option_text, exc)
        return False
    if result and result.get("ok"):
        log.info(
            "[scraper] %s = '%s'  (value=%s, text=%s, multi=%s, final=%s)",
            log_label, option_text,
            result.get("value"), result.get("text"),
            result.get("is_multiple"), result.get("final_selection"),
        )
        time.sleep(0.25)
        return True
    log.error("[scraper] %s = '%s' FAILED: %s",
              log_label, option_text, (result or {}).get("reason"))
    try:
        dump = driver.execute_script(_DUMP_SELECTS_JS) or []
        log.error("[scraper] DOM diagnostic — %d <select> elements:", len(dump))
        for s in dump[:25]:
            log.error("[scraper]   id=%-22s opts=%s",
                      str(s.get("id")), s.get("options"))
    except Exception:
        pass
    return False


PAGE_SIZE_OVERRIDE = 200
MAX_PAGES_PER_PROFILE = 50  # safety stop; real profiles never need this many


def _install_page_size_hook(driver, size: int = PAGE_SIZE_OVERRIDE) -> None:
    """Monkey-patch jQuery.fn.load so the AJAX URL submitSearchForm() fires
    carries `size=N` (vs server default 20) AND so we can capture that URL
    afterwards (stored in `window.__lastSearchUrl`) to paginate manually.

    submitSearchForm builds a URL like
      /borsa/obbligazioni/advanced-search.html?typology=BNC&type=1&lang=it
    and dispatches it via `jQuery('#tableResults').load(url)`. The URL
    already encodes every filter — preserving filter state across our
    custom pagination is automatic because we replay the exact same URL
    with `&page=N` appended."""
    js = f"""
        (function() {{
          if (window.__bondsSizeHook) return;
          if (!window.jQuery || !window.jQuery.fn || !window.jQuery.fn.load) return;
          const origLoad = window.jQuery.fn.load;
          window.__lastSearchUrl = null;
          window.jQuery.fn.load = function(url) {{
            try {{
              if (typeof url === 'string' && url.indexOf('/advanced-search.html') !== -1) {{
                if (/[?&]size=\\d+/.test(url)) {{
                  url = url.replace(/([?&]size=)\\d+/, '$1{size}');
                }} else {{
                  url += (url.indexOf('?') === -1 ? '?' : '&') + 'size={size}';
                }}
                window.__lastSearchUrl = url;
                arguments[0] = url;
              }}
            }} catch (e) {{ /* swallow & forward */ }}
            return origLoad.apply(this, arguments);
          }};
          window.__bondsSizeHook = true;
        }})();
    """
    try:
        driver.execute_script(js)
    except Exception as exc:
        log.warning("[scraper] page-size hook install failed: %s", exc)


def _fetch_results_page(driver, base_url: str, page_n: int) -> str:
    """Fetch a single results page directly via XHR (no DOM rendering).

    `base_url` is the URL captured from the first submitSearchForm call,
    already carrying every filter and `size=N`. We strip any existing
    `page=` and append the requested page number. Returns raw HTML."""
    cleaned = re.sub(r'[?&]page=\d+', '', base_url)
    sep = '&' if '?' in cleaned else '?'
    page_url = f"{cleaned}{sep}page={page_n}"
    driver.set_script_timeout(60)
    return driver.execute_async_script(
        "const done = arguments[arguments.length-1]; "
        "fetch(arguments[0], {credentials:'include'}).then(r=>r.text()).then(done)"
        ".catch(e => done('<!--fetch-error:' + e + '-->'));",
        page_url,
    )


def _click_cerca(driver) -> bool:
    """Submit the advanced search form.

    The visible "Cerca" element on Borsa Italiana's advanced search page is
    NOT a <button> or <input type=submit> — it's an <a id="findButton"
    href="javascript:submitSearchForm()">Cerca</a>. Calling the JS function
    directly is the most reliable way to trigger the AJAX submit (a regular
    Selenium click on `<a href="javascript:...">` works too but going via the
    function bypasses any styling/visibility issues).
    """
    try:
        # Direct call to the page-defined JS function — same effect as a
        # real user clicking the "Cerca" link.
        driver.execute_script("submitSearchForm();")
        log.info("[scraper] Triggered submitSearchForm() directly")
        time.sleep(0.8)
        return True
    except Exception as exc_js:
        log.warning("[scraper] submitSearchForm() failed: %s — falling back to #findButton click", exc_js)
    # Fallback: click the actual <a id="findButton"> element
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    try:
        link = WebDriverWait(driver, WAIT_FORM_SECONDS).until(
            EC.element_to_be_clickable((By.ID, "findButton"))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        driver.execute_script("arguments[0].click();", link)
        log.info("[scraper] Clicked #findButton")
        time.sleep(0.8)
        return True
    except Exception as exc:
        log.error("[scraper] CERCA click failed: %s", exc)
        return False


def _click_annulla(driver) -> bool:
    """Click ANNULLA between profiles to reset the form."""
    from selenium.webdriver.common.by import By
    try:
        btn = driver.find_element(
            By.XPATH,
            "//button[contains(translate(normalize-space(.), 'annulla', 'ANNULLA'), 'ANNULLA')]",
        )
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _wait_for_results(driver) -> bool:
    """Wait for the results section to populate. We accept any of:

    - At least one anchor pointing to /scheda/<ISIN>-... (classic markup)
    - Any visible ISIN-shaped token (12 chars: AA + 10 alphanums) in page text
    - An explicit "Nessun titolo trovato" message
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    def _ready(d) -> bool:
        try:
            anchors = d.find_elements(By.XPATH, "//a[contains(@href, '/scheda/')]")
            if anchors:
                return True
            body = d.find_element(By.TAG_NAME, "body").text
            if _ISIN_RE.search(body):
                return True
            if "nessun titolo" in body.lower() or "nessun risultato" in body.lower():
                return True
        except Exception:
            return False
        return False

    try:
        WebDriverWait(driver, WAIT_RESULTS_SECONDS).until(_ready)
        # Brief settle for AJAX-rendered rows to fully populate
        time.sleep(0.5)
        return True
    except Exception as exc:
        # Diagnostic: what does the page actually contain?
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            log.error("[scraper] Results not detected. Body preview (first 500 chars):")
            log.error("[scraper]   %s", body[:500].replace("\n", " | "))
            tables = driver.find_elements(By.TAG_NAME, "table")
            log.error("[scraper] %d <table> on page", len(tables))
            for i, t in enumerate(tables[:3]):
                snippet = (t.get_attribute("outerHTML") or "")[:300].replace("\n", " ")
                log.error("[scraper]   table[%d]: %s", i, snippet)
        except Exception:
            pass
        log.error("[scraper] Results table did not appear: %s", exc)
        return False


def _go_next_page(driver) -> bool:
    from selenium.webdriver.common.by import By
    try:
        link = driver.find_element(
            By.XPATH,
            "//a[contains(translate(@title, 'successiva', 'SUCCESSIVA'), 'SUCCESSIVA')"
            " or contains(translate(normalize-space(.), 'successiva', 'SUCCESSIVA'), 'SUCCESSIVA')]",
        )
        cls = (link.get_attribute("class") or "").lower()
        if "disabled" in cls:
            return False
        if not link.is_displayed():
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        driver.execute_script("arguments[0].click();", link)
        time.sleep(random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX))
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class ScrapeStats:
    profile: str
    pages: int = 0
    rows: int = 0
    saved: int = 0
    error: Optional[str] = None

    def asdict(self) -> dict:
        return {
            "profile": self.profile,
            "pages": self.pages,
            "rows": self.rows,
            "saved": self.saved,
            "error": self.error,
        }


def _scrape_profile(
    driver,
    profile: ScrapeProfile,
    on_record: Callable[[dict], None],
    cancel_flag: Optional[Callable[[], bool]] = None,
    page_callback: Optional[Callable[[ScrapeStats], None]] = None,
) -> ScrapeStats:
    """Apply filters and walk every result page yielding records via on_record."""
    stats = ScrapeStats(profile=profile.name)
    log.info("[scraper] === Profile %s start ===", profile.name)
    if page_callback:
        page_callback(stats)  # register profile early so the UI shows it

    # Re-open the form for each profile to ensure a clean state
    driver.get(ADVANCED_SEARCH_URL)
    time.sleep(0.5)
    _dismiss_cookie_banner(driver)
    # Hook the AJAX submit so the URL carries `size=PAGE_SIZE_OVERRIDE`
    # instead of the default `size=20`. Must be installed BEFORE the
    # filters are set (so it's in place when submitSearchForm fires).
    _install_page_size_hook(driver)

    for step in profile.steps:
        if cancel_flag and cancel_flag():
            stats.error = "cancelled"
            if page_callback:
                page_callback(stats)
            return stats
        if not _set_select_by_id(driver, step.select_id, step.option_text, step.label):
            stats.error = f"Cannot set filter {step.label}='{step.option_text}'"
            if page_callback:
                page_callback(stats)
            return stats

    if not _click_cerca(driver):
        stats.error = "Cannot click CERCA"
        if page_callback:
            page_callback(stats)
        return stats

    if not _wait_for_results(driver):
        stats.error = "Results table did not appear"
        if page_callback:
            page_callback(stats)
        return stats

    # ── Walk all results pages for this filter set. ────────────────────
    # The first submitSearchForm call rendered page 1 in the DOM (with
    # size=PAGE_SIZE_OVERRIDE thanks to _install_page_size_hook). We grab
    # the URL it built (filters + size baked in), then fetch page 2..N
    # ourselves via XHR until BI runs out of ISINs. This drops the old
    # 20-row cap completely — we keep paginating until exhausted.
    if cancel_flag and cancel_flag():
        stats.error = "cancelled"
        return stats
    time.sleep(0.3)
    html = driver.page_source
    records = parse_results_html(html)
    seen_isins: set = {r["isin"] for r in records}
    stats.pages = 1
    stats.rows = len(records)
    if not records:
        log.warning("[scraper] No rows for %s. HTML snippet:", profile.name)
        log.warning("[scraper]   %s", html[:600].replace("\n", " ")[:600])
    base_url = driver.execute_script("return window.__lastSearchUrl;")
    if records and base_url and len(records) >= PAGE_SIZE_OVERRIDE:
        # Saturated page 1 → more pages exist. Walk them via direct XHR.
        for page_n in range(2, MAX_PAGES_PER_PROFILE + 1):
            if cancel_flag and cancel_flag():
                stats.error = "cancelled"
                break
            try:
                html_n = _fetch_results_page(driver, base_url, page_n)
            except Exception as exc:
                log.warning("[scraper] %s page=%d fetch crashed: %s — stopping pagination",
                            profile.name, page_n, exc)
                break
            recs_n = parse_results_html(html_n)
            new_recs = [r for r in recs_n if r["isin"] not in seen_isins]
            if not new_recs:
                # BI returned only already-seen ISINs (or empty) → exhausted
                break
            seen_isins.update(r["isin"] for r in new_recs)
            records.extend(new_recs)
            stats.pages = page_n
            stats.rows = len(records)
            # If BI returned fewer rows than the page size, this was the
            # last page — no need to fetch one more just to confirm.
            if len(recs_n) < PAGE_SIZE_OVERRIDE:
                break
        else:
            # Fell off the loop without break → MAX_PAGES_PER_PROFILE hit.
            log.warning("[scraper] %s hit MAX_PAGES_PER_PROFILE=%d — there may "
                        "still be more rows", profile.name, MAX_PAGES_PER_PROFILE)
    log.info("[scraper] %s -> %d rows over %d page(s)",
             profile.name, stats.rows, stats.pages)
    for rec in records:
        on_record(rec)
        stats.saved += 1
    if page_callback:
        page_callback(stats)
    log.info("[scraper] === Profile %s done: %s ===", profile.name, stats.asdict())
    return stats


def run_scrape(
    db,
    *,
    profiles: Iterable[ScrapeProfile] = SCRAPE_PROFILES,
    headless: bool = True,
    target_date: Optional[str] = None,
    cancel_flag: Optional[Callable[[], bool]] = None,
    page_callback: Optional[Callable[[ScrapeStats], None]] = None,
    dry_run: bool = False,
) -> Dict[str, dict]:
    """Run a full sync against every profile, persisting catalog + prices.

    `dry_run=True` parses everything but doesn't write to the database.
    """
    target_date = target_date or date.today().isoformat()
    started = datetime.now().isoformat(timespec="seconds")
    results: Dict[str, dict] = {}
    log.info("[scraper] run_scrape started at %s headless=%s dry_run=%s",
             started, headless, dry_run)

    driver = None
    try:
        # Clear category on every existing bond so the multi-profile run
        # repopulates each ISIN with the *first* profile it appears under,
        # rather than letting a previous run's stale category persist.
        if not dry_run:
            db.reset_categories()
        driver = _build_chrome_driver(headless=headless)
        for profile in profiles:
            run_id = None if dry_run else db.start_scrape_run(profile.name)

            def _on_record(rec: dict, _profile=profile) -> None:
                if dry_run:
                    return
                isin = rec.get("isin")
                if not isin:
                    return
                name = rec.get("name") or isin
                # Drop callable bonds at ingest: BI's `callable=No` server
                # filter is broken (treats NULL ≠ No so it wipes every
                # sovereign), but the description column reliably tags
                # callables with a standalone "Call" / "Callable" token.
                # We don't want them in the DB because a YTM computed
                # against final maturity is misleading — the issuer can
                # redeem early.
                if is_callable_from_name(name):
                    return
                geo = geo_area_from_isin(isin)
                issuer = issuer_type_from_name(name)
                ccy = currency_from_name(name)
                tipologia = _profile_tipologia(_profile)
                nation = _profile_nation(_profile)
                # Prefer coupon parsed from the bond name (annual rate);
                # fall back to the table value. For zero-coupon profiles
                # force coupon=0 if the name doesn't say otherwise.
                name_coupon = coupon_from_name(name)
                if name_coupon is not None:
                    coupon = name_coupon
                elif _profile.name.endswith("__zero"):
                    coupon = 0.0
                else:
                    coupon = rec.get("coupon")
                try:
                    db.upsert_bond(
                        isin=isin,
                        name=name,
                        coupon=coupon,
                        maturity_date=rec.get("maturity_date"),
                        currency=ccy,
                        category=_profile.name,
                        tipologia=tipologia,
                        nation=nation,
                        issuer_type=issuer,
                        geo_area=geo,
                    )
                except Exception as exc:
                    log.warning(
                        "[scraper] upsert_bond failed for %s (%r): %s — skipping",
                        isin, name[:60] if name else "", exc,
                    )
                    return
                price = rec.get("ultimo_price")
                if price is not None:
                    try:
                        db.upsert_price(isin, target_date, float(price))
                    except Exception as exc:
                        log.warning(
                            "[scraper] upsert_price failed for %s @ %s: %s",
                            isin, target_date, exc,
                        )

            stats = _scrape_profile(
                driver,
                profile,
                _on_record,
                cancel_flag=cancel_flag,
                page_callback=page_callback,
            )
            if not dry_run and run_id is not None:
                db.finish_scrape_run(
                    run_id,
                    rows_scraped=stats.rows,
                    status="completed" if not stats.error else "failed",
                    error_message=stats.error,
                )
            results[profile.name] = stats.asdict()
    except Exception as exc:
        log.error("[scraper] run_scrape crashed: %s", exc, exc_info=True)
        results["__error__"] = {"error": str(exc)}
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    # Soft-purge: mark as inactive any bond not seen in this run that has
    # been absent long enough to look delisted/matured. Does nothing on a
    # dry-run (no last_seen updates would have happened).
    if not dry_run and "__error__" not in results:
        try:
            purged = db.mark_stale_inactive()
            if purged:
                log.info("[scraper] soft-purge: marked %d stale bonds inactive", purged)
        except Exception as exc:
            log.warning("[scraper] soft-purge failed: %s", exc)
    log.info("[scraper] run_scrape finished: %s", results)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# CLI for manual debug
# ──────────────────────────────────────────────────────────────────────────────
def _cli() -> None:
    """python scraper.py [--dry-run] [--show] [--profile fixed_vanilla|zero_coupon]"""
    import argparse
    parser = argparse.ArgumentParser(description="Manual debug for the bonds scraper")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't write anything to the database")
    parser.add_argument("--show", action="store_true",
                        help="Run with a visible Chrome window")
    parser.add_argument("--profile", choices=[p.name for p in SCRAPE_PROFILES],
                        help="Run only one profile (default: all)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    profiles = SCRAPE_PROFILES
    if args.profile:
        profiles = [p for p in SCRAPE_PROFILES if p.name == args.profile]

    from database import Database
    db = Database()
    out = run_scrape(
        db,
        profiles=profiles,
        headless=not args.show,
        dry_run=args.dry_run,
    )
    print("\nResult summary:")
    for name, stats in out.items():
        print(f"  {name:>16}: {stats}")


if __name__ == "__main__":
    _cli()
