"""Unit tests for database.py — uses an in-memory SQLite via temp file."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the parent package importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db_path = Path(path)
        self.db = Database(self.db_path)

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_schema_creates_tables(self) -> None:
        with self.db.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
        self.assertEqual(tables, {"bonds", "bond_prices", "scrape_runs"})

    def test_upsert_bond_inserts_then_updates(self) -> None:
        self.db.upsert_bond(
            isin="IT0001",
            name="BTP Test",
            coupon=2.5,
            maturity_date="2030-01-01",
            currency="EUR",
            category="fixed_vanilla",
            issuer_type="Government",
            geo_area="Italia",
        )
        self.assertEqual(self.db.count_bonds(), 1)

        # Update the same ISIN (price doesn't change row count, name does)
        self.db.upsert_bond(
            isin="IT0001",
            name="BTP Updated",
            coupon=2.5,
            maturity_date="2030-01-01",
            currency="EUR",
            category="fixed_vanilla",
            issuer_type=None,           # COALESCE should keep old value
            geo_area=None,
        )
        self.assertEqual(self.db.count_bonds(), 1)
        rows = self.db.list_bonds_with_latest_price()
        self.assertEqual(rows[0]["name"], "BTP Updated")
        self.assertEqual(rows[0]["issuer_type"], "Government",
                         "COALESCE should preserve previous issuer_type when None passed")
        self.assertEqual(rows[0]["geo_area"], "Italia")

    def test_upsert_price_and_latest_price_join(self) -> None:
        self.db.upsert_bond(
            isin="IT0001", name="BTP Test", coupon=2.5,
            maturity_date="2030-01-01", currency="EUR",
            category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        self.db.upsert_price("IT0001", "2025-01-01", 95.0)
        self.db.upsert_price("IT0001", "2025-02-01", 96.0)  # newer
        self.db.upsert_price("IT0001", "2024-12-01", 94.0)  # older
        rows = self.db.list_bonds_with_latest_price()
        self.assertEqual(rows[0]["latest_price"], 96.0)
        self.assertEqual(rows[0]["latest_price_date"], "2025-02-01")

    def test_upsert_price_overwrites_same_date(self) -> None:
        self.db.upsert_bond(
            isin="IT0001", name="BTP Test", coupon=2.5,
            maturity_date="2030-01-01", currency="EUR",
            category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        self.db.upsert_price("IT0001", "2025-01-01", 95.0)
        self.db.upsert_price("IT0001", "2025-01-01", 96.5)  # same date
        rows = self.db.list_bonds_with_latest_price()
        self.assertEqual(rows[0]["latest_price"], 96.5)

    def test_count_with_price_excludes_priceless_bonds(self) -> None:
        self.db.upsert_bond(
            isin="IT0001", name="A", coupon=1.0, maturity_date="2030-01-01",
            currency="EUR", category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        self.db.upsert_bond(
            isin="IT0002", name="B", coupon=1.0, maturity_date="2030-01-01",
            currency="EUR", category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        self.db.upsert_price("IT0001", "2025-01-01", 99.0)
        self.assertEqual(self.db.count_bonds(), 2)
        self.assertEqual(self.db.count_with_price(), 1)

    def test_bond_priceless_listed_with_null_latest(self) -> None:
        self.db.upsert_bond(
            isin="IT0099", name="No price bond", coupon=None,
            maturity_date="2031-06-01", currency="EUR",
            category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        rows = self.db.list_bonds_with_latest_price()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["latest_price"])
        self.assertIsNone(rows[0]["latest_price_date"])

    def test_scrape_run_lifecycle(self) -> None:
        run_id = self.db.start_scrape_run("fixed_vanilla")
        self.assertIsInstance(run_id, int)
        self.db.finish_scrape_run(run_id, rows_scraped=42, status="completed")
        last = self.db.last_scrape_run()
        self.assertEqual(last["id"], run_id)
        self.assertEqual(last["rows_scraped"], 42)
        self.assertEqual(last["status"], "completed")
        self.assertIsNotNone(last["finished_at"])

    def test_delete_bond_cascades_prices(self) -> None:
        self.db.upsert_bond(
            isin="IT0001", name="A", coupon=1.0, maturity_date="2030-01-01",
            currency="EUR", category="fixed_vanilla", issuer_type="Government",
            geo_area="Italia",
        )
        self.db.upsert_price("IT0001", "2025-01-01", 99.0)
        self.db.delete_bond("IT0001")
        with self.db.connect() as conn:
            n = conn.execute("SELECT COUNT(*) FROM bond_prices").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
