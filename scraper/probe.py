"""Probe diagnostico su Borsa Italiana (da eseguire una tantum).

Obiettivi:
  1) Elencare TUTTI i <select> del form (id + opzioni) → trovare l'id del filtro
     Valuta e confermare structures/typologies/types/subordination/countries.
  2) Verificare se il filtro Paese resta attivo INSIEME a una tipologia corporate
     (decide l'approccio corp_ita/corp_eur: iterazione paesi vs fallback ISIN).
  3) Verificare il filtro Valuta (se presente) e contare le righe risultanti.

Uso:  python -m scraper.probe [--show]
"""
from __future__ import annotations

import sys
import time

import config
from scraper.search import (
    _build_chrome_driver,
    _click_cerca,
    _dismiss_cookie_banner,
    _install_page_size_hook,
    _set_select_by_id,
    _wait_for_results,
    dump_selects,
    parse_results_html,
)


def _read_state(driver, ids):
    js = """
    const ids = arguments[0]; const out = {};
    for (const id of ids){
      const s = document.getElementById(id);
      out[id] = s ? Array.from(s.selectedOptions).map(o => o.text.trim()) : '(missing)';
    }
    return out;
    """
    try:
        return driver.execute_script(js, ids)
    except Exception as exc:
        return {"error": str(exc)}


def main(headless: bool = True) -> None:
    driver = _build_chrome_driver(headless=headless)
    try:
        driver.get(config.ADVANCED_SEARCH_URL)
        time.sleep(1.0)
        _dismiss_cookie_banner(driver)
        time.sleep(0.5)

        print("\n================ DUMP <select> DEL FORM ================")
        selects = dump_selects(driver)
        print(f"trovati {len(selects)} <select>")
        currency_id = None
        for s in selects:
            sid = s.get("id")
            opts = s.get("options") or []
            print(f"\n  id={sid!r} name={s.get('name')!r}")
            print(f"    options[{len(opts)}]: {opts[:25]}")
            joined = " ".join(o.upper() for o in opts)
            if ("EUR" in joined and "USD" in joined) or "VALUT" in (sid or "").upper():
                currency_id = sid
        print(f"\n>>> candidato id Valuta: {currency_id!r} (config.SEL_VALUTA={config.SEL_VALUTA!r})")

        # ---- Scenario corp + Paese ----
        print("\n================ TEST corp + Paese=Italia ================")
        driver.get(config.ADVANCED_SEARCH_URL)
        time.sleep(0.8)
        _dismiss_cookie_banner(driver)
        _install_page_size_hook(driver)
        _set_select_by_id(driver, config.SEL_STRUTTURA, config.OPT_PLAIN_VANILLA, "Struttura")
        _set_select_by_id(driver, config.SEL_TIPOLOGIA, "Corporate", "Tipologia")
        _set_select_by_id(driver, config.SEL_TIPO_CEDOLA, config.OPT_CEDOLA_FISSA, "Tipo Cedola")
        _set_select_by_id(driver, config.SEL_SUBORDINAZIONE, config.OPT_NO, "Subordinazione")
        country_ok = _set_select_by_id(driver, config.SEL_PAESE, "Italia", "Paese")
        ids = [config.SEL_STRUTTURA, config.SEL_TIPOLOGIA, config.SEL_TIPO_CEDOLA,
               config.SEL_SUBORDINAZIONE, config.SEL_PAESE]
        if currency_id:
            ids.append(currency_id)
        print(f"  set Paese ok={country_ok}")
        print(f"  stato pre-CERCA: {_read_state(driver, ids)}")
        _click_cerca(driver)
        _wait_for_results(driver)
        time.sleep(0.5)
        recs = parse_results_html(driver.page_source)
        print(f"  stato post-CERCA: {_read_state(driver, ids)}")
        print(f"  righe corporate Italia: {len(recs)}")
        for r in recs[:5]:
            print(f"    {r['isin']:<14} {r['name'][:48]}")

        # ---- Scenario Valuta ----
        if currency_id:
            print(f"\n================ TEST Valuta=EUR (id={currency_id}) ================")
            driver.get(config.ADVANCED_SEARCH_URL)
            time.sleep(0.8)
            _dismiss_cookie_banner(driver)
            _install_page_size_hook(driver)
            _set_select_by_id(driver, config.SEL_STRUTTURA, config.OPT_PLAIN_VANILLA, "Struttura")
            _set_select_by_id(driver, config.SEL_TIPOLOGIA, "Titoli Di Stato Italiani", "Tipologia")
            _set_select_by_id(driver, config.SEL_TIPO_CEDOLA, config.OPT_CEDOLA_FISSA, "Tipo Cedola")
            ccy_ok = _set_select_by_id(driver, currency_id, "EUR", "Valuta")
            print(f"  set Valuta ok={ccy_ok}")
            _click_cerca(driver)
            _wait_for_results(driver)
            time.sleep(0.5)
            recs = parse_results_html(driver.page_source)
            print(f"  stato post-CERCA: {_read_state(driver, ids + [currency_id])}")
            print(f"  righe BTP EUR: {len(recs)}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main(headless="--show" not in sys.argv)
