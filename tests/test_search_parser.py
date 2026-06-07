"""Test delle funzioni pure dello scraper (parser + profili + classificatori).

Niente Selenium: si alimenta HTML sintetico. Fixture ripresa dallo screener.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper.search import (  # noqa: E402
    SearchProfile,
    _normalise_date,
    _normalise_number,
    _resolve_record,
    build_profiles,
    coupon_from_name,
    detect_pagination_state,
    is_callable_from_name,
    paese_from_isin,
    parse_results_html,
)

SAMPLE_HTML = """
<html><body>
<table>
  <thead><tr><th>ISIN</th><th>DESCRIZIONE</th><th>ULTIMO</th>
      <th>CEDOLA</th><th>SCADENZA</th></tr></thead>
  <tbody>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005454241-MOTX.html">IT0005454241</a></td>
      <td>BTP-1.65 OT2032 EU</td><td>89,500</td><td>1,65</td><td>01/10/2032</td>
    </tr>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005436693-MOTX.html">IT0005436693</a></td>
      <td>BTP GREEN 30AP2045 EU</td><td>58,40</td><td>1,50</td><td>30/04/2045</td>
    </tr>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005498701-MOTX.html">IT0005498701</a></td>
      <td>BTP STRIP ZC DEC26 EUR</td><td>94,50</td><td>0,00</td><td>01/12/2026</td>
    </tr>
  </tbody>
</table>
</body></html>
"""


class NumberDateTests(unittest.TestCase):
    def test_italian_decimal(self) -> None:
        self.assertEqual(_normalise_number("89,500"), 89.5)
        self.assertEqual(_normalise_number("1.234,56"), 1234.56)
        self.assertEqual(_normalise_number("1,234.56"), 1234.56)
        self.assertEqual(_normalise_number("100"), 100.0)

    def test_blanks(self) -> None:
        for v in ("", "-", "--", "N/A", None):
            self.assertIsNone(_normalise_number(v))  # type: ignore[arg-type]

    def test_dates(self) -> None:
        self.assertEqual(_normalise_date("01/10/2032"), "2032-10-01")
        self.assertEqual(_normalise_date("Scad 30/04/2045"), "2045-04-30")
        self.assertIsNone(_normalise_date("nope"))


class ParseResultsTests(unittest.TestCase):
    def test_parse_and_drop_strip(self) -> None:
        recs = parse_results_html(SAMPLE_HTML)
        isins = [r["isin"] for r in recs]
        self.assertIn("IT0005454241", isins)
        self.assertIn("IT0005436693", isins)
        self.assertNotIn("IT0005498701", isins)  # STRIP escluso
        btp = next(r for r in recs if r["isin"] == "IT0005454241")
        self.assertEqual(btp["name"], "BTP-1.65 OT2032 EU")
        self.assertEqual(btp["ultimo_price"], 89.5)
        self.assertEqual(btp["coupon"], 1.65)
        self.assertEqual(btp["maturity_date"], "2032-10-01")
        self.assertTrue(btp["url_scheda"].startswith("https://www.borsaitaliana.it/"))

    def test_dedupe(self) -> None:
        html = """
        <table>
        <tr><td><a href="/scheda/IT0001234567-X.html">IT0001234567</a></td>
            <td>A</td><td>100</td><td>2,5</td><td>01/01/2030</td></tr>
        <tr><td><a href="/scheda/IT0001234567-X.html">IT0001234567</a></td>
            <td>A dup</td><td>101</td><td>2,5</td><td>01/01/2030</td></tr>
        </table>
        """
        recs = parse_results_html(html)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["ultimo_price"], 100.0)


class PaginationTests(unittest.TestCase):
    def test_next_link(self) -> None:
        html = """
        <ul class="m-pagination">
          <li class="m-pagination__item--current"><span>2</span></li>
          <li><a href="?page=3" title="Successiva">Successiva</a></li>
        </ul>
        """
        cur, _tot, has_next = detect_pagination_state(html)
        self.assertTrue(has_next)
        self.assertEqual(cur, 2)


class ClassifierTests(unittest.TestCase):
    def test_callable(self) -> None:
        self.assertTrue(is_callable_from_name("ENI 4% CALL 2030"))
        self.assertTrue(is_callable_from_name("BANCO BPM CALLABLE 2031"))
        self.assertFalse(is_callable_from_name("BTP 1.65 OT2032"))

    def test_paese_from_isin(self) -> None:
        self.assertEqual(paese_from_isin("IT0005454241"), "Italia")
        self.assertEqual(paese_from_isin("DE0001102614"), "Germania")
        self.assertIsNone(paese_from_isin("ZZ0000000000"))

    def test_coupon_from_name_annual(self) -> None:
        self.assertEqual(coupon_from_name("Btp-1nv26 7,25%"), 7.25)
        self.assertEqual(coupon_from_name("OAT Tf 3,5 Ot2030"), 3.5)
        self.assertEqual(coupon_from_name("BOT ZC 2026"), 0.0)
        self.assertIsNone(coupon_from_name("DESCR SENZA CEDOLA"))


class ProfileTests(unittest.TestCase):
    def test_default_fast_profiles(self) -> None:
        profs = build_profiles(valute=("EUR", "USD"), split_by_country=False)
        # 6 profili per valuta (2 gov_ita + 1 gov_eur + 3 corp) × 2 = 12
        self.assertEqual(len(profs), 12)
        cats = {p.categoria for p in profs}
        self.assertEqual(cats, {"gov_ita", "gov_eur", "corp"})
        # gov_eur e corp risolvono il paese dall'ISIN
        for p in profs:
            if p.categoria in ("gov_eur", "corp"):
                self.assertTrue(p.resolve_country_from_isin)
            if p.categoria == "gov_ita":
                self.assertEqual(p.paese, "Italia")

    def test_split_by_country_profiles(self) -> None:
        profs = build_profiles(valute=("EUR",), split_by_country=True)
        cats = {p.categoria for p in profs}
        self.assertIn("corp_ita", cats)
        self.assertIn("corp_eur", cats)
        self.assertNotIn("corp", cats)  # nessun sentinel quando si itera il paese
        self.assertFalse(any(p.resolve_country_from_isin for p in profs))


class ResolveRecordTests(unittest.TestCase):
    def test_corp_sentinel_italia(self) -> None:
        p = SearchProfile("corp", "Corporate", "EUR", None, resolve_country_from_isin=True)
        rec = _resolve_record(p, {"isin": "IT0001234567", "name": "ENEL 4%"})
        self.assertEqual(rec.categoria, "corp_ita")
        self.assertEqual(rec.paese, "Italia")
        self.assertTrue(rec.paese_da_isin_fallback)

    def test_corp_sentinel_estero(self) -> None:
        p = SearchProfile("corp", "Banche", "EUR", None, resolve_country_from_isin=True)
        rec = _resolve_record(p, {"isin": "XS1234567890", "name": "BNP 3%"})
        self.assertEqual(rec.categoria, "corp_eur")
        self.assertTrue(rec.paese_da_isin_fallback)

    def test_gov_eur_sentinel_keeps_category(self) -> None:
        p = SearchProfile("gov_eur", "Titoli Di Stato Esteri", "EUR", None,
                          resolve_country_from_isin=True)
        rec = _resolve_record(p, {"isin": "DE0001102614", "name": "BUND 0%"})
        self.assertEqual(rec.categoria, "gov_eur")
        self.assertEqual(rec.paese, "Germania")

    def test_gov_ita_no_fallback(self) -> None:
        p = SearchProfile("gov_ita", "Titoli Di Stato Italiani", "EUR", "Italia")
        rec = _resolve_record(p, {"isin": "IT0005454241", "name": "BTP 1.65%"})
        self.assertEqual(rec.categoria, "gov_ita")
        self.assertFalse(rec.paese_da_isin_fallback)

    def test_resolve_uses_annual_coupon_from_name(self) -> None:
        # La tabella mostra 3.625 (periodica); dal nome si ricava l'annua 7.25.
        p = SearchProfile("gov_ita", "Titoli Di Stato Italiani", "EUR", "Italia")
        rec = _resolve_record(p, {"isin": "IT0001086567", "name": "Btp 7,25%",
                                  "coupon": 3.625})
        self.assertEqual(rec.cedola_pct, 7.25)


if __name__ == "__main__":
    unittest.main()
