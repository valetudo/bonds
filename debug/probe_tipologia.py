"""Diagnostic: open Borsa Italiana advanced-search, set Tipologia filter,
read back what the DOM and the visible Select2 widget actually show, and
how many result rows come out. Goal: figure out whether our JS-based
value setting is being honoured by the site.

Run with:  python debug/probe_tipologia.py
"""
from __future__ import annotations
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import (  # noqa: E402
    ADVANCED_SEARCH_URL,
    _build_chrome_driver,
    _click_cerca,
    _dismiss_cookie_banner,
    _set_select_by_id,
    parse_results_html,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


PROBE_JS = r"""
const select = document.getElementById('typologies');
if (!select) return {error: 'no #typologies'};
const selectedOpts = Array.from(select.selectedOptions).map(o => o.text.trim());
const allOpts = Array.from(select.options).map(o => o.text.trim());
const isMultiple = !!select.multiple;
const visibleSel = document.querySelector('.select2-selection');
const visibleText = visibleSel ? visibleSel.textContent.trim() : null;
// Find the Select2 wrapper around #typologies specifically
const wrap = select.parentElement.querySelector('.select2-selection');
const wrapText = wrap ? wrap.textContent.trim() : null;
return {
  is_multiple: isMultiple,
  all_options: allOpts,
  selected: selectedOpts,
  any_visible_select2: visibleText,
  this_field_visible_select2: wrapText,
  parent_html: select.parentElement.outerHTML.slice(0, 1200),
};
"""


def _read_state(driver):
    return driver.execute_script(r"""
    const ids = ['structures','typologies','types','callable'];
    const out = {};
    for (const id of ids){
      const s = document.getElementById(id);
      if (!s) { out[id] = '(missing)'; continue; }
      out[id] = Array.from(s.selectedOptions).map(o => o.text.trim());
    }
    return out;
    """)


def probe(headless: bool = False, scenario: str = "all4") -> None:
    driver = _build_chrome_driver(headless=headless)
    try:
        driver.get(ADVANCED_SEARCH_URL)
        time.sleep(1)
        _dismiss_cookie_banner(driver)
        time.sleep(0.5)

        # Replay the scraper's full filter sequence
        steps = [
            ("structures", "Plain Vanilla", "Struttura"),
            ("typologies", "Titoli Di Stato Italiani", "Tipologia"),
            ("types", "Titolo Con Cedole Tf", "Tipo Cedola"),
            ("callable", "No", "Rimborso Anticipato"),
        ]
        if scenario == "tipologia_only":
            steps = [steps[1]]

        for sid, val, lbl in steps:
            print(f"\n--- Setting {lbl} = {val} ---")
            ok = _set_select_by_id(driver, sid, val, lbl)
            print(f"  set ok={ok}")
            print(f"  state after: {_read_state(driver)}")

        print("\n=== Clicking CERCA ===")
        _click_cerca(driver)
        time.sleep(3)
        for _ in range(15):
            html = driver.page_source
            if "/scheda/" in html or "nessun titolo" in html.lower():
                break
            time.sleep(1)
        time.sleep(1)
        html = driver.page_source
        records = parse_results_html(html)
        print(f"  Rows on first results page: {len(records)}")
        body = driver.find_element("tag name", "body").text
        import re
        m = re.search(r"(\d+)\s*(?:risultat|titoli\s+trovati)", body, re.IGNORECASE)
        print(f"  Page-reported total: {m.group(0) if m else 'not found'}")
        # Check if Successiva is on page
        nxt = "successiva" in body.lower()
        print(f"  Has 'Successiva': {nxt}")
        # Re-read final state of selects after clicking CERCA
        print(f"  FINAL state of all 4 selects: {_read_state(driver)}")

        # Inspect what "Successiva" links the page actually has
        print("\n=== Inspecting next-page links ===")
        next_links = driver.execute_script(r"""
        const out = [];
        document.querySelectorAll('a').forEach(a => {
          const t = (a.textContent || '').trim().toLowerCase();
          const title = (a.title || '').toLowerCase();
          if (t === 'successiva' || title === 'successiva' || t === '>' || t === '»' || t === 'next'){
            out.push({text: a.textContent.trim(), href: a.href, title: a.title,
                      className: a.className, visible: !!(a.offsetWidth || a.offsetHeight)});
          }
        });
        return out;
        """)
        for nl in next_links:
            print(f"  {nl}")
        if not next_links:
            print("  (no candidate next-page links found at all)")

        # Look for any pagination-like markers
        page_info = driver.execute_script(r"""
        return {
          pagination_classes: Array.from(document.querySelectorAll('[class*=pagination]')).slice(0,5).map(e => e.outerHTML.slice(0, 250)),
          rows_in_results_table: document.querySelectorAll('table tr').length,
          tables_count: document.querySelectorAll('table').length,
        };
        """)
        print("\n=== Page structure ===")
        for k, v in page_info.items():
            if isinstance(v, list):
                for x in v: print(f"  {k}: {x}")
            else:
                print(f"  {k}: {v}")

        # CRITICAL TEST: Try the AJAX call to page=2 with the same browser
        # session and see if results stay filtered or revert to global.
        print("\n=== AJAX call to page=2 with size=200 ===")
        try:
            driver.execute_script(
                "loadBoxContentCustom('tableResults', "
                "'/borsa/obbligazioni/advanced-search.html?size=200&lang=it&page=1');"
            )
        except Exception as exc:
            print(f"  loadBoxContentCustom call failed: {exc}")
        time.sleep(3)
        html2 = driver.page_source
        rec2 = parse_results_html(html2)
        print(f"  After size=200&page=1: rows={len(rec2)}")
        # Sample first 3 ISINs to see if they look like BTPs
        for r in rec2[:5]:
            print(f"    {r['isin']:<15} {r['name'][:50]}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    headless = "--show" not in sys.argv
    scenario = "tipologia_only" if "--tipologia-only" in sys.argv else "all4"
    print(f"headless={headless}  scenario={scenario}")
    probe(headless=headless, scenario=scenario)
