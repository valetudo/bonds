"""Test del ladder builder su universo fittizio."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ladder.builder import LadderParams, build_ladder  # noqa: E402


def _bond(isin, categoria, anni, ytm_net, prezzo=100.0, paese="Italia"):
    return {
        "isin": isin, "descrizione": f"BOND {isin}", "categoria": categoria,
        "paese": paese, "scadenza": "2030-01-01", "cedola_pct": 3.0,
        "prezzo": prezzo, "anni_scadenza": anni,
        "ytm_lordo": ytm_net + 1.0, "ytm_netto": ytm_net,
    }


UNIVERSE = pd.DataFrame([
    _bond("A", "gov_ita", 1.0, 3.0),
    _bond("B", "gov_ita", 1.5, 3.5),    # miglior gov_ita in fascia 1
    _bond("C", "gov_ita", 3.0, 4.0),    # gov_ita fascia 2
    _bond("D", "corp_ita", 1.0, 2.0),   # unico corp_ita fascia 1
    _bond("E", "corp_ita", 3.5, 5.0),   # corp_ita fascia 2
])


class BuildLadderTests(unittest.TestCase):
    def test_selection_and_allocation(self) -> None:
        params = LadderParams(
            capital=40000, n_steps=2, max_duration_years=4,
            alloc_gov_ita=50, alloc_corp_ita=50, alloc_gov_eur=0, alloc_corp_eur=0,
            use_net_yield=True,
        )
        res = build_ladder(UNIVERSE, params)
        self.assertEqual(res.n_bonds, 4)
        t = res.table.set_index(["gradino", "categoria"])
        self.assertEqual(t.loc[(1, "gov_ita"), "isin"], "B")   # argmax YTM netto
        self.assertEqual(t.loc[(1, "corp_ita"), "isin"], "D")
        self.assertEqual(t.loc[(2, "gov_ita"), "isin"], "C")
        self.assertEqual(t.loc[(2, "corp_ita"), "isin"], "E")
        # 4 celle × 10.000€ (lotti interi, prezzo 100) = 40.000€
        self.assertAlmostEqual(res.capital_allocated, 40000.0, places=2)
        # YTM medio ponderato netto = (3.5+2.0+4.0+5.0)/4
        self.assertAlmostEqual(res.weighted_ytm_net, 3.625, places=3)

    def test_empty_category_warns(self) -> None:
        params = LadderParams(
            capital=20000, n_steps=2, max_duration_years=4,
            alloc_gov_ita=0, alloc_corp_ita=0, alloc_gov_eur=100, alloc_corp_eur=0,
        )
        res = build_ladder(UNIVERSE, params)
        self.assertTrue(res.table.empty)
        self.assertTrue(any("nessun bond" in w for w in res.warnings))

    def test_lot_too_small_warns(self) -> None:
        params = LadderParams(
            capital=500, n_steps=1, max_duration_years=4,
            alloc_gov_ita=100, alloc_corp_ita=0, alloc_gov_eur=0, alloc_corp_eur=0,
        )
        res = build_ladder(UNIVERSE, params)
        self.assertTrue(res.table.empty)
        self.assertTrue(any("1 lotto" in w for w in res.warnings))

    def test_invalid_allocations_raise(self) -> None:
        params = LadderParams(
            capital=10000, n_steps=2, max_duration_years=4,
            alloc_gov_ita=50, alloc_corp_ita=40, alloc_gov_eur=0, alloc_corp_eur=0,
        )
        with self.assertRaises(ValueError):
            build_ladder(UNIVERSE, params)

    def test_max_duration_excludes_long(self) -> None:
        # max_duration 2 → solo fascia 1; E (3.5y) e C (3y) esclusi.
        params = LadderParams(
            capital=10000, n_steps=1, max_duration_years=2,
            alloc_gov_ita=100, alloc_corp_ita=0, alloc_gov_eur=0, alloc_corp_eur=0,
        )
        res = build_ladder(UNIVERSE, params)
        self.assertEqual(res.n_bonds, 1)
        self.assertEqual(res.table.iloc[0]["isin"], "B")


if __name__ == "__main__":
    unittest.main()
