"""Test del filtro condiviso della Overview."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.filters import apply_filters  # noqa: E402

DF = pd.DataFrame([
    {"isin": "IT01", "descrizione": "BTP 3%", "categoria": "gov_ita", "valuta": "EUR",
     "mercato": "MOT", "anni_scadenza": 2.0, "ytm_netto": 3.0, "ytm_lordo": 3.5},
    {"isin": "DE02", "descrizione": "BUND 1%", "categoria": "gov_eur", "valuta": "EUR",
     "mercato": "MOT", "anni_scadenza": 8.0, "ytm_netto": 4.0, "ytm_lordo": 4.5},
    {"isin": "US03", "descrizione": "APPLE 5%", "categoria": "corp_eur", "valuta": "USD",
     "mercato": "EuroTLX", "anni_scadenza": 5.0, "ytm_netto": 5.0, "ytm_lordo": 5.5},
    {"isin": "IT04", "descrizione": "ENEL 4%", "categoria": "corp_ita", "valuta": "EUR",
     "mercato": "EuroTLX", "anni_scadenza": 1.0, "ytm_netto": None, "ytm_lordo": None},
])


class FilterTests(unittest.TestCase):
    def test_none_returns_all(self) -> None:
        self.assertEqual(len(apply_filters(DF)), 4)

    def test_categoria(self) -> None:
        out = apply_filters(DF, categorie=["gov_ita"])
        self.assertEqual(out["isin"].tolist(), ["IT01"])

    def test_valuta(self) -> None:
        out = apply_filters(DF, valute=["USD"])
        self.assertEqual(out["isin"].tolist(), ["US03"])

    def test_anni_range(self) -> None:
        out = apply_filters(DF, anni_range=(3.0, 10.0))
        self.assertEqual(set(out["isin"]), {"DE02", "US03"})

    def test_ytm_range_excludes_nan(self) -> None:
        out = apply_filters(DF, ytm_range=(3.5, 5.0), ycol="ytm_netto")
        self.assertEqual(set(out["isin"]), {"DE02", "US03"})  # IT01 sotto, IT04 NaN

    def test_query_descrizione_and_isin(self) -> None:
        self.assertEqual(apply_filters(DF, query="apple")["isin"].tolist(), ["US03"])
        self.assertEqual(apply_filters(DF, query="IT04")["isin"].tolist(), ["IT04"])
        self.assertEqual(set(apply_filters(DF, query="it0")["isin"]), {"IT01", "IT04"})

    def test_combined(self) -> None:
        out = apply_filters(DF, categorie=["gov_ita", "gov_eur"], valute=["EUR"])
        self.assertEqual(set(out["isin"]), {"IT01", "DE02"})

    def test_mercato(self) -> None:
        self.assertEqual(set(apply_filters(DF, mercati=["MOT"])["isin"]), {"IT01", "DE02"})
        self.assertEqual(set(apply_filters(DF, mercati=["EuroTLX"])["isin"]), {"US03", "IT04"})


if __name__ == "__main__":
    unittest.main()
