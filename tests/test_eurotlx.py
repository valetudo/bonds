"""Test del modulo EuroTLX (eligibilità, parser, record, profili, mappa categorie)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from scraper.eurotlx import (  # noqa: E402
    _price_from,
    _to_record,
    build_eurotlx_profiles,
    is_eligible,
    parse_eurotlx_html,
)

SAMPLE = """
<table>
<tr><th>ISIN</th><th>Descrizione</th><th>Ultimo Prezzo</th><th>Cedola</th>
    <th>Scadenza</th><th>Acquisto</th><th>Vendita</th></tr>
<tr><td><a href="/borsa/obbligazioni/eurotlx/scheda/XS0214965963-ETLX.html">XS0214965963</a></td>
    <td>Telecom Italia 5.25% 17mz55</td><td>102,10</td><td></td><td>17/03/2055</td>
    <td>101,9</td><td>102,3</td></tr>
<tr><td><a href="/borsa/obbligazioni/eurotlx/scheda/DE000A169NC2-ETLX.html">DE000A169NC2</a></td>
    <td>Daimler Tf 1,375% Mg28 Eur</td><td></td><td></td><td>11/05/2028</td>
    <td>97,0</td><td>97,4</td></tr>
<tr><td><a href="/borsa/obbligazioni/eurotlx/scheda/XS9999999999-ETLX.html">XS9999999999</a></td>
    <td>Some Bk Tv Ot27 Eur</td><td>99,0</td><td></td><td>20/10/2027</td><td></td><td></td></tr>
</table>
"""


class EligibilityTests(unittest.TestCase):
    def test_keep_fixed(self) -> None:
        self.assertTrue(is_eligible("Telecom Italia 5.25% 17mz55"))
        self.assertTrue(is_eligible("Daimler Tf 1,375% Mg28 Eur"))
        self.assertTrue(is_eligible("Voestalpine Green Fx 3.75% Oct29 Eur"))

    def test_exclude_structures(self) -> None:
        self.assertFalse(is_eligible("Some Bk Tv Ot27 Eur"))          # floater
        self.assertFalse(is_eligible("Anheuserbusc Fx 2% Mar28 Call Eur"))  # callable
        self.assertFalse(is_eligible("Deutsche Bk Oc Ind Link Nv27 E"))     # index-linked
        self.assertFalse(is_eligible("Issuer Step Up 2030"))          # step
        self.assertFalse(is_eligible("Issuer FRN 2030"))              # FRN

    def test_zero_coupon_flag(self) -> None:
        self.assertFalse(is_eligible("Issuer Zc 2030"))
        self.assertTrue(is_eligible("Issuer Zc 2030", include_zero_coupon=True))


class ParserTests(unittest.TestCase):
    def test_columns_and_bidask(self) -> None:
        rows = parse_eurotlx_html(SAMPLE)
        self.assertEqual(len(rows), 3)
        tim = next(r for r in rows if r["isin"] == "XS0214965963")
        self.assertEqual(tim["ultimo_price"], 102.10)
        self.assertEqual(tim["bid"], 101.9)
        self.assertEqual(tim["ask"], 102.3)
        self.assertEqual(tim["maturity_date"], "2055-03-17")
        self.assertTrue(tim["url_scheda"].endswith("XS0214965963-ETLX.html"))

    def test_price_mid_fallback(self) -> None:
        rows = parse_eurotlx_html(SAMPLE)
        daimler = next(r for r in rows if r["isin"] == "DE000A169NC2")
        self.assertIsNone(daimler["ultimo_price"])
        self.assertEqual(_price_from(daimler), 97.2)  # mid(97.0, 97.4)


class ToRecordTests(unittest.TestCase):
    def _rec(self, isin, name, **kw):
        base = {"isin": isin, "name": name, "ultimo_price": None, "coupon": None,
                "maturity_date": "2030-01-01", "bid": None, "ask": None, "url_scheda": None}
        base.update(kw)
        return base

    def test_corp_estero_from_isin(self) -> None:
        r = _to_record(self._rec("XS0214965963", "Telecom Italia 5.25% 17mz55", ultimo_price=102.1),
                       "corp", "EUR", "CORPORATE_BONDS")
        self.assertEqual(r.categoria, "corp_eur")
        self.assertTrue(r.paese_da_isin_fallback)
        self.assertEqual(r.mercato, "EuroTLX")
        self.assertEqual(r.cedola_pct, 5.25)  # dal nome
        self.assertEqual(r.ultimo_price, 102.1)

    def test_corp_italia_from_isin(self) -> None:
        r = _to_record(self._rec("IT0001234567", "Enel Tf 4% 2030"), "corp", "EUR", "CORPORATE_BONDS")
        self.assertEqual(r.categoria, "corp_ita")
        self.assertEqual(r.paese, "Italia")

    def test_gov_eur_keeps_bucket(self) -> None:
        r = _to_record(self._rec("DE000A169NC2", "Bund 1% 2028"), "gov_eur", "EUR", "BUND")
        self.assertEqual(r.categoria, "gov_eur")
        self.assertEqual(r.paese, "Germania")

    def test_price_mid_in_record(self) -> None:
        r = _to_record(self._rec("DE000A169NC2", "Daimler Tf 1,375% Mg28 Eur", bid=97.0, ask=97.4),
                       "corp", "EUR", "CORPORATE_BONDS")
        self.assertEqual(r.ultimo_price, 97.2)


class ProfileAndMapTests(unittest.TestCase):
    def test_build_profiles_counts(self) -> None:
        n_cat = len(config.EUROTLX_CATEGORY_BUCKET)
        self.assertEqual(len(build_eurotlx_profiles(("EUR",))), n_cat)
        self.assertEqual(len(build_eurotlx_profiles(("EUR", "USD"))), n_cat * 2)
        with_zero = build_eurotlx_profiles(("EUR",), include_zero_coupon=True)
        self.assertEqual(len(with_zero), n_cat + len(config.EUROTLX_ZERO_BUCKET))

    def test_category_map_valid(self) -> None:
        valid = {"gov_ita", "gov_eur", "corp"}
        self.assertTrue(set(config.EUROTLX_CATEGORY_BUCKET.values()) <= valid)
        self.assertTrue(set(config.EUROTLX_ZERO_BUCKET.values()) <= valid)
        # nessuna sovrapposizione tra mappati, zero ed esclusi
        keys = set(config.EUROTLX_CATEGORY_BUCKET)
        self.assertFalse(keys & config.EUROTLX_EXCLUDED)
        self.assertFalse(keys & set(config.EUROTLX_ZERO_BUCKET))


if __name__ == "__main__":
    unittest.main()
