"""Test della fiscalità e del YTM netto."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance.tax import ALIQUOTA_CORP, ALIQUOTA_GOV, aliquota_for, ytm_net  # noqa: E402
from finance.yield_calc import ytm_gross  # noqa: E402

C = "30/360"
SETTLE = date(2026, 1, 15)
MAT5 = date(2031, 1, 15)


class AliquotaTests(unittest.TestCase):
    def test_categories(self) -> None:
        self.assertEqual(aliquota_for("gov_ita"), ALIQUOTA_GOV)
        self.assertEqual(aliquota_for("gov_eur"), ALIQUOTA_GOV)
        self.assertEqual(aliquota_for("gov_eur", white_list=False), ALIQUOTA_CORP)
        self.assertEqual(aliquota_for("corp_ita"), ALIQUOTA_CORP)
        self.assertEqual(aliquota_for("corp_eur"), ALIQUOTA_CORP)
        self.assertEqual(aliquota_for("sconosciuta"), ALIQUOTA_CORP)


class YtmNetTests(unittest.TestCase):
    def test_net_below_gross_corp(self) -> None:
        g = ytm_gross(100.0, 4.0, 1, MAT5, SETTLE, convention=C)
        n = ytm_net(100.0, 4.0, 1, MAT5, SETTLE, "corp_ita", convention=C)
        self.assertIsNotNone(n)
        self.assertLess(n, g)
        # Alla pari: redemption non tassata, cedola netta 4*(1-0.26)=2.96 → YTM≈2.96%
        self.assertAlmostEqual(n, 0.0296, places=4)

    def test_gov_taxed_less_than_corp(self) -> None:
        n_gov = ytm_net(100.0, 4.0, 1, MAT5, SETTLE, "gov_ita", convention=C)
        n_corp = ytm_net(100.0, 4.0, 1, MAT5, SETTLE, "corp_ita", convention=C)
        self.assertGreater(n_gov, n_corp)

    def test_capital_gain_taxed_on_discount(self) -> None:
        # Zero-coupon a sconto: la plusvalenza a scadenza è tassata.
        g = ytm_gross(90.0, 0.0, 1, MAT5, SETTLE, convention=C)
        n = ytm_net(90.0, 0.0, 1, MAT5, SETTLE, "gov_ita", convention=C)
        self.assertLess(n, g)

    def test_capital_loss_not_taxed_on_premium(self) -> None:
        # Zero-coupon sopra la pari: nessuna imposta sul capitale → netto == lordo.
        g = ytm_gross(105.0, 0.0, 1, MAT5, SETTLE, convention=C)
        n = ytm_net(105.0, 0.0, 1, MAT5, SETTLE, "corp_ita", convention=C)
        self.assertAlmostEqual(n, g, places=6)

    def test_bollo_reduces_yield(self) -> None:
        without = ytm_net(100.0, 4.0, 1, MAT5, SETTLE, "gov_ita", convention=C)
        with_bollo = ytm_net(100.0, 4.0, 1, MAT5, SETTLE, "gov_ita",
                             apply_bollo=True, convention=C)
        self.assertLess(with_bollo, without)


if __name__ == "__main__":
    unittest.main()
