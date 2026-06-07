"""Costruzione dell'universo obbligazioni via form di ricerca avanzata di BI.

Sorgente unica: la pagina "Obbligazioni - Cerca Strumento" di Borsa Italiana.
I FILTRI determinano sia l'eligibilità (Plain Vanilla, cedola fissa, no
subordinati) sia la CATEGORIA (gov/corp, ita/estero). Nessuna inferenza dal
nome del bond. Ogni riga della tabella espone già ISIN | Descrizione | Ultimo |
Cedola | Scadenza, quindi non si visitano le schede dei singoli ISIN.

Tecnica Selenium ripresa (e collaudata) dallo screener `bonds`:
  - filtri impostati via JS sui <select> (deselect-all + select + dispatch
    eventi, per i Select2 multipli);
  - page-size hook su jQuery.fn.load per forzare size=200 e catturare l'URL
    filtrato in window.__lastSearchUrl;
  - paginazione manuale via fetch() su quell'URL con &page=N.

I parser puri (parse_results_html, _normalise_*) sono importabili senza Selenium
per i test.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Tuple

from bs4 import BeautifulSoup

import config

log = logging.getLogger(__name__)

_BASE = "https://www.borsaitaliana.it"

# Best-effort: applica il filtro Valuta se il <select> esiste (id da verificare
# col probe). Se non applicabile, la valuta del record resta quella del profilo.
CURRENCY_FILTER_ENABLED = True


# ──────────────────────────────────────────────────────────────────────────────
# Modello profili / record
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SearchProfile:
    """Una combinazione di filtri da applicare al form.

    `categoria` è la categoria target (gov_ita|gov_eur|corp_ita|corp_eur) oppure
    il sentinella "corp" quando ita/estero va risolto dal prefisso ISIN
    (resolve_country_from_isin=True).
    """
    categoria: str
    tipologia_bi: str
    valuta: str
    paese: Optional[str] = None
    cedola: str = config.OPT_CEDOLA_FISSA
    resolve_country_from_isin: bool = False

    @property
    def label(self) -> str:
        bits = [self.tipologia_bi, self.valuta]
        if self.paese:
            bits.append(self.paese)
        if self.resolve_country_from_isin:
            bits.append("paese da ISIN")
        return " - ".join(bits)


@dataclass
class BondRecord:
    isin: str
    descrizione: str
    cedola_pct: Optional[float]
    scadenza: Optional[str]
    valuta: str
    categoria: str
    tipologia_bi: str
    paese: Optional[str]
    paese_da_isin_fallback: bool
    ultimo_price: Optional[float]
    url_scheda: Optional[str]
    mercato: str = "MOT"


@dataclass
class ScrapeProgress:
    profile_label: str
    categoria: str
    page: int = 0
    rows_so_far: int = 0
    done: bool = False
    error: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Profili
# ──────────────────────────────────────────────────────────────────────────────
def build_profiles(
    valute: Tuple[str, ...] = config.VALUTE,
    include_zero_coupon: bool = False,
    split_by_country: bool = False,
) -> List[SearchProfile]:
    """Costruisce i profili di ricerca per le 4 categorie × valute.

    split_by_country=False (default, VELOCE): gov_eur e corporate in un'unica
      query per tipologia; paese (e per i corp anche ita/estero) ricavato dal
      prefisso ISIN — fallback esplicito e segnalato.
    split_by_country=True (LENTO, autoritativo): itera il dropdown Paese per
      gov_eur e per i corporate (decine di query in più).

    Nota: gov_ita vs gov_eur deriva SEMPRE dalla Tipologia (Italiani vs Esteri),
    non dal paese; corp_ita vs corp_eur deriva dal Paese (o dal prefisso ISIN).
    """
    cedole = [config.OPT_CEDOLA_FISSA]
    if include_zero_coupon:
        cedole.append(config.OPT_ZERO_COUPON)

    profs: List[SearchProfile] = []
    for val in valute:
        for ced in cedole:
            # gov_ita — dai filtri Tipologia (2 tipologie), paese implicito Italia
            for tip in config.TIPOLOGIE_GOV_ITA:
                profs.append(SearchProfile("gov_ita", tip, val, config.PAESE_ITALIA, ced))
            # gov_eur — Tipologia "Titoli Di Stato Esteri"
            if split_by_country:
                for paese in config.PAESI_NON_ITALIA:
                    profs.append(SearchProfile("gov_eur", config.TIPOLOGIE_GOV_EUR[0],
                                               val, paese, ced))
            else:
                profs.append(SearchProfile("gov_eur", config.TIPOLOGIE_GOV_EUR[0],
                                           val, None, ced, resolve_country_from_isin=True))
            # corp_ita / corp_eur — Tipologia corporate
            for tip in config.TIPOLOGIE_CORP:
                if split_by_country:
                    profs.append(SearchProfile("corp_ita", tip, val, config.PAESE_ITALIA, ced))
                    for paese in config.PAESI_NON_ITALIA:
                        profs.append(SearchProfile("corp_eur", tip, val, paese, ced))
                else:
                    profs.append(SearchProfile("corp", tip, val, None, ced,
                                               resolve_country_from_isin=True))
    return profs


# ──────────────────────────────────────────────────────────────────────────────
# Classificatori ammessi (callable dal nome + paese da prefisso ISIN)
# ──────────────────────────────────────────────────────────────────────────────
def is_callable_from_name(name: str) -> bool:
    """Rileva i callable dal nome (l'unico uso ammesso del nome): il filtro
    server-side 'Rimborso Anticipato=No' di BI è buggato (azzera i sovereign)."""
    if not name:
        return False
    upper = f" {name.upper()} "
    return any(f" {tok} " in upper for tok in ("CALL", "CALLABLE", "CALL.", "C/A"))


# Prefisso ISIN (ISO 6166) → paese. Usato SOLO come fallback esplicito e
# segnalato per il paese (mai per la categoria gov/corp, che viene dai filtri).
_PAESE_BY_ISIN = {
    "IT": "Italia", "FR": "Francia", "DE": "Germania", "ES": "Spagna",
    "AT": "Austria", "BE": "Belgio", "NL": "Olanda", "PT": "Portogallo",
    "FI": "Finlandia", "IE": "Irlanda", "LU": "Lussemburgo", "GR": "Grecia",
    "CY": "Cipro", "SI": "Slovenia", "EE": "Estonia", "LV": "Lettonia",
    "LT": "Lituania", "PL": "Polonia", "HU": "Ungheria", "RO": "Romania",
    "BG": "Bulgaria", "HR": "Croazia", "GB": "Gran Bretagna", "CH": "Svizzera",
    "NO": "Norvegia", "SE": "Svezia", "US": "Stati Uniti", "CA": "Canada",
    "XS": "Eurobond/Intl",
}


def paese_from_isin(isin: str) -> Optional[str]:
    return _PAESE_BY_ISIN.get((isin or "")[:2].upper())


def coupon_from_name(name: str) -> Optional[float]:
    """Estrae la cedola ANNUA (%) dal nome — estrazione FATTUALE del numero,
    non categorizzazione (la categoria resta dai filtri).

    Necessario perché la colonna CEDOLA della tabella BI mostra spesso la cedola
    PERIODICA (es. semestrale = annua/2): 'Btp 7,25%' ha tabella 3,625. Il nome
    riporta la cedola annua, fonte più affidabile per il NUMERO. Range [0, 25].
    Ritorna None se non parseable (allora si usa il valore di tabella).
    """
    if not name:
        return None
    upper = name.upper()
    tokens = re.split(r"\s+", upper)
    if "ZC" in tokens or any(t.startswith("ZC") for t in tokens):
        return 0.0
    if "ZERO" in tokens and ("COUPON" in tokens or "CPN" in tokens):
        return 0.0

    def _ok(v: float) -> Optional[float]:
        return v if 0 <= v <= 25 else None

    m = re.search(r"(\d+(?:[,.]\d+)?)\s*%", name)        # "7,25%"
    if m:
        try:
            return _ok(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    m = re.search(r"\bTF\s+(\d+(?:[,.]\d+)?)", upper)     # "Tf 3,5"
    if m:
        try:
            return _ok(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    if tokens:                                            # trailing "... 5,75"
        last = tokens[-1]
        if re.match(r"^\d+[,.]\d+$", last):
            try:
                v = float(last.replace(",", "."))
                if 0 < v <= 25:
                    return v
            except ValueError:
                pass
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Parser puri (testabili senza Selenium) — ripresi da `bonds/scraper.py`
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
    """Ritorna 'YYYY-MM-DD' o None (accetta dd/mm/yyyy o yyyy-mm-dd)."""
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
        y, mo, d = s.split("-")
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    except ValueError:
        return None


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_results_html(html: str) -> List[dict]:
    """Estrae un record per riga dalla tabella risultati.

    Layout standard: ISIN | DESCRIZIONE | ULTIMO | CEDOLA | SCADENZA.
    Chiavi: isin, name, ultimo_price, coupon, maturity_date, url_scheda.
    Le righe STRIP (zero-coupon non ordinari) vengono scartate.
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
        for tr in soup.select("table tr"):
            if _ISIN_RE.search(tr.get_text(" ", strip=True)):
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
        if isin in seen or "STRIP" in full_text:
            continue
        isin_idx = next((i for i, c in enumerate(cells) if isin in c.upper()), -1)
        if isin_idx < 0:
            continue
        name = cells[isin_idx + 1] if isin_idx + 1 < len(cells) else ""
        ultimo = _normalise_number(cells[isin_idx + 2]) if isin_idx + 2 < len(cells) else None
        coupon = _normalise_number(cells[isin_idx + 3]) if isin_idx + 3 < len(cells) else None
        maturity = (
            _normalise_date(cells[isin_idx + 4]) if isin_idx + 4 < len(cells) else None
        ) or _normalise_date(" ".join(cells))

        url = None
        a = tr.select_one("a[href*='/scheda/']")
        if a and a.get("href"):
            href = a["href"]
            url = href if href.startswith("http") else _BASE + href

        out.append({
            "isin": isin,
            "name": name or isin,
            "ultimo_price": ultimo,
            "coupon": coupon,
            "maturity_date": maturity,
            "url_scheda": url,
        })
        seen.add(isin)
    return out


def detect_pagination_state(html: str) -> Tuple[Optional[int], Optional[int], bool]:
    """(current_page, total_pages, has_next). Campi None se non esposti."""
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
# Selenium (import locali: il modulo si carica anche senza Selenium per i test)
# ──────────────────────────────────────────────────────────────────────────────
def _build_chrome_driver(headless: bool = True):
    import os
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
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
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    })
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(config.WAIT_RESULTS_SECONDS + 5)
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
            log.info("[scraper] cookie banner chiuso (%s)", sel)
            return
        except Exception:
            continue


_SET_SELECT_JS = r"""
const selectId = arguments[0];
const optionText = arguments[1];
function norm(s){return (s||'').replace(/\s+/g,' ').trim();}
const select = document.getElementById(selectId);
if (!select){ return {ok: false, reason: 'no element id="' + selectId + '"'}; }
if (select.tagName !== 'SELECT'){
  return {ok: false, reason: 'id="' + selectId + '" not a <select> (' + select.tagName + ')'};
}
const opts = Array.from(select.options);
const want = optionText.toLowerCase();
let matched = opts.find(o => norm(o.text).toLowerCase() === want);
if (!matched) matched = opts.find(o => norm(o.text).toLowerCase().includes(want));
if (!matched){
  return {ok: false, reason: 'option "' + optionText + '" not in [' + opts.map(o => norm(o.text)).join(' | ') + ']'};
}
for (const o of opts) o.selected = false;
matched.selected = true;
select.value = matched.value;
try { select.dispatchEvent(new Event('input', {bubbles: true})); } catch(e) {}
try { select.dispatchEvent(new Event('change', {bubbles: true})); } catch(e) {}
if (window.jQuery){
  try { window.jQuery(select).val([matched.value]).trigger('change'); } catch(e) {}
}
return {ok: true, value: matched.value, text: norm(matched.text),
        is_multiple: select.multiple,
        final_selection: Array.from(select.selectedOptions).map(o => norm(o.text))};
"""

_DUMP_SELECTS_JS = r"""
return Array.from(document.querySelectorAll('select')).map(s => ({
  id: s.id || null, name: s.name || null,
  options: Array.from(s.options).slice(0, 40).map(o => (o.text || '').trim())
}));
"""


def dump_selects(driver) -> list:
    """Diagnostica: id/name/options di tutti i <select> in pagina (per il probe)."""
    try:
        return driver.execute_script(_DUMP_SELECTS_JS) or []
    except Exception:
        return []


def _set_select_by_id(driver, select_id: str, option_text: str, label: str = "") -> bool:
    log_label = f"{label}({select_id})" if label else select_id
    try:
        result = driver.execute_script(_SET_SELECT_JS, select_id, option_text)
    except Exception as exc:
        log.error("[scraper] %s='%s' errore JS: %s", log_label, option_text, exc)
        return False
    if result and result.get("ok"):
        log.info("[scraper] %s='%s' (final=%s)", log_label, option_text,
                 result.get("final_selection"))
        time.sleep(0.25)
        return True
    log.error("[scraper] %s='%s' FALLITO: %s", log_label, option_text,
              (result or {}).get("reason"))
    return False


def _install_page_size_hook(driver, size: int = config.PAGE_SIZE_OVERRIDE) -> None:
    js = f"""
        (function() {{
          if (window.__blSizeHook) return;
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
            }} catch (e) {{ }}
            return origLoad.apply(this, arguments);
          }};
          window.__blSizeHook = true;
        }})();
    """
    try:
        driver.execute_script(js)
    except Exception as exc:
        log.warning("[scraper] page-size hook non installato: %s", exc)


def _fetch_results_page(driver, base_url: str, page_n: int) -> str:
    cleaned = re.sub(r"[?&]page=\d+", "", base_url)
    sep = "&" if "?" in cleaned else "?"
    page_url = f"{cleaned}{sep}page={page_n}"
    driver.set_script_timeout(60)
    return driver.execute_async_script(
        "const done = arguments[arguments.length-1]; "
        "fetch(arguments[0], {credentials:'include'}).then(r=>r.text()).then(done)"
        ".catch(e => done('<!--fetch-error:' + e + '-->'));",
        page_url,
    )


def _click_cerca(driver) -> bool:
    try:
        driver.execute_script("submitSearchForm();")
        time.sleep(0.8)
        return True
    except Exception:
        pass
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    try:
        link = WebDriverWait(driver, config.WAIT_FORM_SECONDS).until(
            EC.element_to_be_clickable((By.ID, "findButton"))
        )
        driver.execute_script("arguments[0].click();", link)
        time.sleep(0.8)
        return True
    except Exception as exc:
        log.error("[scraper] CERCA fallito: %s", exc)
        return False


def _wait_for_results(driver) -> bool:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    def _ready(d) -> bool:
        try:
            if d.find_elements(By.XPATH, "//a[contains(@href, '/scheda/')]"):
                return True
            body = d.find_element(By.TAG_NAME, "body").text
            if _ISIN_RE.search(body):
                return True
            low = body.lower()
            return "nessun titolo" in low or "nessun risultato" in low
        except Exception:
            return False

    try:
        WebDriverWait(driver, config.WAIT_RESULTS_SECONDS).until(_ready)
        time.sleep(0.5)
        return True
    except Exception as exc:
        log.error("[scraper] tabella risultati non comparsa: %s", exc)
        return False


def apply_profile_filters(driver, profile: SearchProfile) -> Tuple[bool, Optional[str]]:
    """Applica i filtri del profilo. Ritorna (ok, errore)."""
    steps = [
        (config.SEL_STRUTTURA, config.OPT_PLAIN_VANILLA, "Struttura"),
        (config.SEL_TIPOLOGIA, profile.tipologia_bi, "Tipologia"),
        (config.SEL_TIPO_CEDOLA, profile.cedola, "Tipo Cedola"),
        (config.SEL_SUBORDINAZIONE, config.OPT_NO, "Subordinazione"),
    ]
    for sid, val, lbl in steps:
        if not _set_select_by_id(driver, sid, val, lbl):
            return False, f"filtro {lbl}={val!r} non applicato"
    if CURRENCY_FILTER_ENABLED and profile.valuta:
        if not _set_select_by_id(driver, config.SEL_VALUTA, profile.valuta, "Valuta"):
            log.warning("[scraper] filtro Valuta non applicato (%s) — best-effort",
                        profile.valuta)
    if profile.paese:
        if not _set_select_by_id(driver, config.SEL_PAESE, profile.paese, "Paese"):
            return False, f"filtro Paese={profile.paese!r} non applicato"
    return True, None


def _scrape_profile_rows(
    driver,
    profile: SearchProfile,
    cancel_flag: Optional[Callable[[], bool]],
    progress_cb: Optional[Callable[[ScrapeProgress], None]],
    prog: ScrapeProgress,
) -> List[dict]:
    """Applica i filtri e percorre tutte le pagine. Ritorna le righe parse-ate."""
    driver.get(config.ADVANCED_SEARCH_URL)
    time.sleep(0.5)
    _dismiss_cookie_banner(driver)
    _install_page_size_hook(driver)

    ok, err = apply_profile_filters(driver, profile)
    if not ok:
        raise RuntimeError(err or "filtri non applicati")
    if not _click_cerca(driver):
        raise RuntimeError("click CERCA fallito")
    if not _wait_for_results(driver):
        raise RuntimeError("risultati non comparsi")

    time.sleep(0.3)
    records = parse_results_html(driver.page_source)
    seen = {r["isin"] for r in records}
    prog.page, prog.rows_so_far = 1, len(records)
    if progress_cb:
        progress_cb(prog)

    base_url = driver.execute_script("return window.__lastSearchUrl;")
    if records and base_url and len(records) >= config.PAGE_SIZE_OVERRIDE:
        for page_n in range(2, config.MAX_PAGES_PER_PROFILE + 1):
            if cancel_flag and cancel_flag():
                break
            try:
                html_n = _fetch_results_page(driver, base_url, page_n)
            except Exception as exc:
                log.warning("[scraper] %s page=%d fetch err: %s", profile.label, page_n, exc)
                break
            recs_n = parse_results_html(html_n)
            new_recs = [r for r in recs_n if r["isin"] not in seen]
            if not new_recs:
                break
            seen.update(r["isin"] for r in new_recs)
            records.extend(new_recs)
            prog.page, prog.rows_so_far = page_n, len(records)
            if progress_cb:
                progress_cb(prog)
            if len(recs_n) < config.PAGE_SIZE_OVERRIDE:
                break
            time.sleep(random.uniform(*config.PAGE_DELAY))
    return records


def _resolve_record(profile: SearchProfile, parsed: dict) -> BondRecord:
    isin = parsed["isin"]
    categoria = profile.categoria
    paese = profile.paese
    fallback = False
    if profile.resolve_country_from_isin:
        fallback = True
        is_ita = isin[:2].upper() == "IT"
        paese = config.PAESE_ITALIA if is_ita else paese_from_isin(isin)
        if profile.categoria == "corp":
            categoria = "corp_ita" if is_ita else "corp_eur"
    # Cedola ANNUA: preferisci il numero dal nome (la tabella mostra spesso la
    # cedola periodica); fallback al valore di tabella.
    cedola = coupon_from_name(parsed.get("name") or "")
    if cedola is None:
        cedola = parsed.get("coupon")
    return BondRecord(
        isin=isin,
        descrizione=parsed.get("name") or isin,
        cedola_pct=cedola,
        scadenza=parsed.get("maturity_date"),
        valuta=profile.valuta,
        categoria=categoria,
        tipologia_bi=profile.tipologia_bi,
        paese=paese,
        paese_da_isin_fallback=fallback,
        ultimo_price=parsed.get("ultimo_price"),
        url_scheda=parsed.get("url_scheda"),
    )


def scrape_universe(
    profiles: List[SearchProfile],
    *,
    headless: bool = True,
    cancel_flag: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[ScrapeProgress], None]] = None,
) -> Iterator[BondRecord]:
    """Esegue lo scraping di tutti i profili, yieldando BondRecord deduplicati.

    I callable sono scartati lato client (is_callable_from_name). La categoria
    viene dai filtri; il paese dei record resolve_country_from_isin dal prefisso.
    """
    driver = _build_chrome_driver(headless=headless)
    emitted: set = set()
    try:
        for profile in profiles:
            if cancel_flag and cancel_flag():
                break
            prog = ScrapeProgress(profile.label, profile.categoria)
            if progress_cb:
                progress_cb(prog)
            try:
                rows = _scrape_profile_rows(driver, profile, cancel_flag, progress_cb, prog)
            except Exception as exc:
                prog.error, prog.done = str(exc), True
                log.error("[scraper] profilo %s errore: %s", profile.label, exc)
                if progress_cb:
                    progress_cb(prog)
                time.sleep(random.uniform(*config.PAGE_DELAY))
                continue
            for parsed in rows:
                name = parsed.get("name") or parsed["isin"]
                if is_callable_from_name(name):
                    continue
                if parsed["isin"] in emitted:
                    continue
                emitted.add(parsed["isin"])
                yield _resolve_record(profile, parsed)
            prog.done = True
            if progress_cb:
                progress_cb(prog)
            time.sleep(random.uniform(*config.PAGE_DELAY))
    finally:
        try:
            driver.quit()
        except Exception:
            pass
