"""Test della persistenza parquet (idempotenza, storico prezzi)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import store  # noqa: E402


def rec(isin: str, **kw) -> dict:
    base = dict(
        isin=isin, descrizione="X", cedola_pct=3.0, scadenza="2030-01-01",
        valuta="EUR", categoria="gov_ita", tipologia_bi="Titoli Di Stato Italiani",
        paese="Italia", paese_da_isin_fallback=False, url_scheda="u",
    )
    base.update(kw)
    return base


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.u = os.path.join(self.tmp.name, "universe.parquet")
        self.p = os.path.join(self.tmp.name, "prices.parquet")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_missing_returns_empty_schema(self) -> None:
        df = store.load_universe(self.u)
        self.assertTrue(df.empty)
        self.assertEqual(list(df.columns), store.UNIVERSE_COLUMNS)

    def test_upsert_adds_then_skips(self) -> None:
        r1 = store.upsert_universe([rec("IT0001"), rec("IT0002")], path=self.u)
        self.assertEqual(r1["added"], 2)
        r2 = store.upsert_universe([rec("IT0001"), rec("IT0003")], path=self.u)
        self.assertEqual(r2["added"], 1)
        self.assertEqual(r2["skipped"], 1)
        df = store.load_universe(self.u)
        self.assertEqual(len(df), 3)
        self.assertEqual(set(df["isin"].tolist()), {"IT0001", "IT0002", "IT0003"})

    def test_first_seen_preserved_catalog_refreshed(self) -> None:
        store.upsert_universe([rec("IT0001", categoria="gov_ita")], path=self.u)
        ts1 = store.load_universe(self.u).set_index("isin").loc["IT0001", "timestamp_aggiunta"]
        store.upsert_universe(
            [rec("IT0001", categoria="corp_ita", descrizione="NEW")], path=self.u
        )
        df = store.load_universe(self.u).set_index("isin")
        self.assertEqual(df.loc["IT0001", "categoria"], "gov_ita")        # first-seen
        self.assertEqual(df.loc["IT0001", "timestamp_aggiunta"], ts1)     # preserved
        self.assertEqual(df.loc["IT0001", "descrizione"], "NEW")          # refreshed

    def test_fallback_count(self) -> None:
        r = store.upsert_universe(
            [rec("XS01", categoria="corp_eur", paese="Germania",
                 paese_da_isin_fallback=True)],
            path=self.u,
        )
        self.assertEqual(r["fallback_isin"], 1)

    def test_prices_history_and_latest(self) -> None:
        store.save_prices({"IT0001": 99.0, "IT0002": 100.0}, on_date="2026-06-01", path=self.p)
        store.save_prices({"IT0001": 98.5}, on_date="2026-06-02", path=self.p)
        store.save_prices({"IT0002": 101.0}, on_date="2026-06-01", path=self.p)  # dedup same date
        allp = store.load_prices(self.p)
        self.assertEqual(len(allp), 3)
        latest = store.latest_prices(self.p).set_index("isin")
        self.assertEqual(latest.loc["IT0001", "price"], 98.5)
        self.assertEqual(latest.loc["IT0001", "date"], "2026-06-02")
        self.assertEqual(latest.loc["IT0002", "price"], 101.0)
        self.assertEqual(store.last_price_timestamp(self.p), "2026-06-02")

    def test_universe_with_latest_price(self) -> None:
        store.upsert_universe([rec("IT0001")], path=self.u)
        store.save_prices({"IT0001": 95.0}, on_date="2026-06-01", path=self.p)
        df = store.universe_with_latest_price(self.u, self.p)
        self.assertIn("prezzo", df.columns)
        self.assertEqual(df.set_index("isin").loc["IT0001", "prezzo"], 95.0)


if __name__ == "__main__":
    unittest.main()
