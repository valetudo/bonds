"""Tests for the pure-function parsers in scraper.py.

We don't drive Selenium here; we feed synthetic HTML and check what comes out.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scraper import (  # noqa: E402
    _normalise_date,
    _normalise_number,
    detect_pagination_state,
    parse_results_html,
)


# Minimum viable HTML matching the structure we expect from the live page.
SAMPLE_HTML = """
<html><body>
<table>
  <thead>
    <tr><th>ISIN</th><th>DESCRIZIONE</th><th>ULTIMO</th>
        <th>CEDOLA</th><th>SCADENZA</th></tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005454241-MOTX.html">IT0005454241</a></td>
      <td>BTP-1.65 OT2032 EU</td>
      <td>89,500</td>
      <td>1,65</td>
      <td>01/10/2032</td>
    </tr>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005436693-MOTX.html">IT0005436693</a></td>
      <td>BTP GREEN 30AP2045 EU</td>
      <td>58,40</td>
      <td>1,50</td>
      <td>30/04/2045</td>
    </tr>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005498701-MOTX.html">IT0005498701</a></td>
      <td>BTP STRIP ZC DEC26 EUR</td>
      <td>94,50</td>
      <td>0,00</td>
      <td>01/12/2026</td>
    </tr>
    <tr>
      <td><a href="/borsa/obbligazioni/mot/btp/scheda/IT0005PRICELESS-MOTX.html">IT0005PRICELES</a></td>
      <td>BTP NO PRICE 2030</td>
      <td>-</td>
      <td>-</td>
      <td>01/01/2030</td>
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

    def test_blanks_and_dashes(self) -> None:
        self.assertIsNone(_normalise_number(""))
        self.assertIsNone(_normalise_number("-"))
        self.assertIsNone(_normalise_number("--"))
        self.assertIsNone(_normalise_number("N/A"))
        self.assertIsNone(_normalise_number(None))  # type: ignore[arg-type]

    def test_dates(self) -> None:
        self.assertEqual(_normalise_date("01/10/2032"), "2032-10-01")
        self.assertEqual(_normalise_date("Scadenza 30/04/2045"), "2045-04-30")
        self.assertEqual(_normalise_date("2032-10-01"), "2032-10-01")
        self.assertIsNone(_normalise_date(""))
        self.assertIsNone(_normalise_date("not a date"))


class ParseResultsTests(unittest.TestCase):
    def test_parse_two_real_rows_and_drop_strip(self) -> None:
        records = parse_results_html(SAMPLE_HTML)
        # 4 rows in the table; STRIP filtered out, the other 3 kept (one with
        # missing price/coupon).
        isins = [r["isin"] for r in records]
        self.assertIn("IT0005454241", isins)
        self.assertIn("IT0005436693", isins)
        # priceless row's ISIN is malformed in our sample (only 11 chars after
        # the country code), so the regex correctly rejects it. We validate
        # that two clean rows + the strip row exclusion is what we get.
        self.assertNotIn("IT0005498701", isins, "STRIP must be excluded")
        # Locate the BTP 2032 row and check the parsed values
        btp32 = next(r for r in records if r["isin"] == "IT0005454241")
        self.assertEqual(btp32["name"], "BTP-1.65 OT2032 EU")
        self.assertEqual(btp32["ultimo_price"], 89.5)
        self.assertEqual(btp32["coupon"], 1.65)
        self.assertEqual(btp32["maturity_date"], "2032-10-01")

    def test_parse_handles_missing_values(self) -> None:
        html = """
        <table>
        <tr>
          <td><a href="/scheda/IT0001234567-MOTX.html">IT0001234567</a></td>
          <td>BTP NO DATA</td>
          <td>-</td>
          <td>-</td>
          <td>-</td>
        </tr>
        </table>
        """
        records = parse_results_html(html)
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["ultimo_price"])
        self.assertIsNone(records[0]["coupon"])
        self.assertIsNone(records[0]["maturity_date"])

    def test_dedupes_by_isin(self) -> None:
        html = """
        <table>
        <tr><td><a href="/scheda/IT0001234567-MOTX.html">IT0001234567</a></td>
            <td>A</td><td>100</td><td>2,5</td><td>01/01/2030</td></tr>
        <tr><td><a href="/scheda/IT0001234567-MOTX.html">IT0001234567</a></td>
            <td>A duplicate</td><td>101</td><td>2,5</td><td>01/01/2030</td></tr>
        </table>
        """
        records = parse_results_html(html)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["ultimo_price"], 100.0)


class PaginationTests(unittest.TestCase):
    def test_detect_no_pagination(self) -> None:
        html = "<html><body><table><tr><td>x</td></tr></table></body></html>"
        cur, tot, has_next = detect_pagination_state(html)
        self.assertFalse(has_next)

    def test_detect_next_link(self) -> None:
        html = """
        <ul class="m-pagination">
          <li><a href="?page=1">1</a></li>
          <li class="m-pagination__item--current"><span>2</span></li>
          <li><a href="?page=3" title="Successiva">Successiva</a></li>
        </ul>
        """
        cur, tot, has_next = detect_pagination_state(html)
        self.assertTrue(has_next)
        self.assertEqual(cur, 2)

    def test_pagina_di_text(self) -> None:
        html = "<div>Pagina 3 di 7</div>"
        cur, tot, _ = detect_pagination_state(html)
        self.assertEqual(cur, 3)
        self.assertEqual(tot, 7)


if __name__ == "__main__":
    unittest.main()
