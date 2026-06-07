"""Test del calcolo YTM lordo e del rateo (esempi noti a mano).

Si usa la convenzione 30/360 per ottenere frazioni d'anno intere e quindi
valori attesi esatti.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance.yield_calc import accrued_interest, ytm_gross  # noqa: E402

C = "30/360"
SETTLE = date(2026, 1, 15)
MAT5 = date(2031, 1, 15)  # esattamente 5 anni dopo (anniversario)


class YtmGrossTests(unittest.TestCase):
    def test_at_par_yields_coupon(self) -> None:
        # Prezzo alla pari, settlement su anniversario (rateo 0) → YTM ≈ cedola.
        y = ytm_gross(100.0, 3.0, 1, MAT5, SETTLE, convention=C)
        self.assertIsNotNone(y)
        self.assertAlmostEqual(y, 0.03, places=4)

    def test_below_par_yield_above_coupon(self) -> None:
        y = ytm_gross(95.0, 3.0, 1, MAT5, SETTLE, convention=C)
        self.assertIsNotNone(y)
        self.assertGreater(y, 0.03)

    def test_above_par_yield_below_coupon(self) -> None:
        y = ytm_gross(105.0, 3.0, 1, MAT5, SETTLE, convention=C)
        self.assertIsNotNone(y)
        self.assertLess(y, 0.03)

    def test_zero_coupon_closed_form(self) -> None:
        # (100/80)^(1/5) - 1 ≈ 0.045639
        y = ytm_gross(80.0, 0.0, 1, MAT5, SETTLE, convention=C)
        self.assertIsNotNone(y)
        self.assertAlmostEqual(y, (100.0 / 80.0) ** (1.0 / 5.0) - 1.0, places=6)

    def test_semiannual_close_to_annual_at_par(self) -> None:
        # Alla pari, semestrale → YTM nominale ≈ cedola (capitalizzazione interna).
        y = ytm_gross(100.0, 3.0, 2, MAT5, SETTLE, convention=C)
        self.assertIsNotNone(y)
        self.assertAlmostEqual(y, 0.03, places=4)

    def test_invalid_inputs_return_none(self) -> None:
        self.assertIsNone(ytm_gross(0.0, 3.0, 1, MAT5, SETTLE, convention=C))
        self.assertIsNone(ytm_gross(100.0, 3.0, 1, date(2020, 1, 1), SETTLE, convention=C))
        self.assertIsNone(ytm_gross(100.0, 3.0, 1, None, SETTLE, convention=C))


class AccruedTests(unittest.TestCase):
    def test_half_period_explicit(self) -> None:
        # cedola 4% annua, metà periodo → rateo ≈ 2.0
        accr = accrued_interest(4.0, 1, date(2026, 1, 15), date(2026, 7, 15),
                                convention=C)
        self.assertAlmostEqual(accr, 2.0, places=6)

    def test_reconstruction_matches_explicit(self) -> None:
        # Senza last_coupon_date → ricostruito da maturity: stesso risultato.
        explicit = accrued_interest(4.0, 1, date(2026, 1, 15), date(2026, 7, 15),
                                    convention=C)
        reconstructed = accrued_interest(4.0, 1, None, date(2026, 7, 15),
                                         maturity_date=MAT5, convention=C)
        self.assertAlmostEqual(explicit, reconstructed, places=6)

    def test_on_coupon_date_zero(self) -> None:
        accr = accrued_interest(4.0, 1, None, SETTLE, maturity_date=MAT5, convention=C)
        self.assertAlmostEqual(accr, 0.0, places=6)

    def test_zero_coupon_no_accrual(self) -> None:
        self.assertEqual(accrued_interest(0.0, 1, None, SETTLE, MAT5), 0.0)
        self.assertEqual(accrued_interest(4.0, 0, None, SETTLE, MAT5), 0.0)


if __name__ == "__main__":
    unittest.main()
