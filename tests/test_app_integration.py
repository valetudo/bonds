"""End-to-end test: feed parsed records into the DB, then hit Flask routes
with the test client, validate the JSON shape and the exported HTML.

No Selenium involved. Catches integration bugs (template substitution,
JSON shape, anomaly pipeline, /api/export download).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
from database import Database  # noqa: E402


SAMPLE = [
    # Cluster of 5y BTPs with one anomaly (used for the anomaly detector test)
    ("IT0000001", "BTP 2.00 OT2030 EU",  2.00, "2030-10-01", 95.0, "fixed_vanilla"),
    ("IT0000002", "BTP 2.10 OT2030 EU",  2.10, "2030-10-01", 95.5, "fixed_vanilla"),
    ("IT0000003", "BTP 1.90 OT2030 EU",  1.90, "2030-10-01", 94.8, "fixed_vanilla"),
    ("IT0000004", "BTP ANOMALO 2030",    4.00, "2030-10-01", 80.0, "fixed_vanilla"),
    # A bond with no price -> must be in DB but excluded from average yield
    ("IT0099999", "BTP NO PRICE 2032",   1.50, "2032-04-01", None, "fixed_vanilla"),
    # A non-Italian bond
    ("DE0000001", "BUND 2030", 2.00, "2030-10-01", 96.0, "fixed_vanilla"),
    # 10y-band bonds so the per-nation aggregation (which uses 7-12y) has data
    ("IT0010Y001", "BTP 3.50% 2034", 3.50, "2034-04-01", 100.0, "fixed_vanilla"),
    ("IT0010Y002", "BTP 3.20% 2035", 3.20, "2035-04-01",  99.5, "fixed_vanilla"),
    ("DE0010Y001", "BUND 2.50% 2034", 2.50, "2034-04-01", 100.0, "fixed_vanilla"),
    ("DE0010Y002", "BUND 2.40% 2035", 2.40, "2035-04-01",  99.0, "fixed_vanilla"),
]


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Swap the module-level DB to a temp one so the test doesn't pollute bonds.db
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        cls.db_path = Path(path)
        cls.db = Database(cls.db_path)
        app_module.DB = cls.db

        for isin, name, coupon, mat, price, cat in SAMPLE:
            tipologia = (
                "Titoli Di Stato Italiani" if isin.startswith("IT")
                else "Titoli Di Stato Esteri"
            )
            cls.db.upsert_bond(
                isin=isin, name=name, coupon=coupon, maturity_date=mat,
                currency="EUR", category=cat,
                issuer_type=None, geo_area=None,  # let enrich_bond fill them
                tipologia=tipologia,
            )
            if price is not None:
                cls.db.upsert_price(isin, "2025-05-01", price)

        app_module.app.config["TESTING"] = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.db_path.exists():
            cls.db_path.unlink()

    def test_api_bonds_payload_shape(self) -> None:
        resp = self.client.get("/api/bonds")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["total"], len(SAMPLE))
        # All sample rows except the one priceless entry produce a yield
        self.assertEqual(
            body["with_price"],
            sum(1 for s in SAMPLE if s[4] is not None),
        )
        self.assertIsNotNone(body["average_yield"])
        # Anomalies must include IT0000004 (the one we crafted as outlier)
        anom_isins = [a["isin"] for a in body["anomalies"]]
        self.assertIn("IT0000004", anom_isins)
        # German bund must NOT appear in anomalies (filter is IT EUR Government)
        self.assertNotIn("DE0000001", anom_isins)
        # Nations aggregation should be present and contain Italia
        self.assertIn("nations", body)
        nation_names = [n["nation"] for n in body["nations"]]
        self.assertIn("Italia", nation_names)

    def test_api_bonds_priceless_yields_none(self) -> None:
        body = self.client.get("/api/bonds").get_json()
        priceless = next(b for b in body["bonds"] if b["isin"] == "IT0099999")
        self.assertIsNone(priceless["latest_price"])
        self.assertIsNone(priceless["net_yield_pa"])

    def test_export_returns_html_attachment(self) -> None:
        resp = self.client.get("/api/export")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "text/html")
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn("screener_obbligazioni_", cd)
        # Sanity-check the bundled payload made it inline
        text = resp.get_data(as_text=True)
        self.assertIn("PAYLOAD=", text)
        self.assertIn("IT0000001", text)
        # Must NOT contain the raw placeholder
        self.assertNotIn("__PAYLOAD_JSON__", text)
        self.assertNotIn("__GENERATED_AT__", text)
        self.assertNotIn("__SHARED_CSS__", text)

    def test_sync_status_idle_initially(self) -> None:
        body = self.client.get("/api/sync/status").get_json()
        self.assertIn(body["status"], {"idle", "completed", "running", "failed", "stopped"})

    def test_index_serves_html(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        text = resp.get_data(as_text=True)
        self.assertIn("Screener Obbligazionario", text)
        self.assertNotIn("__SHARED_CSS__", text)
        self.assertNotIn("__SHARED_RENDER_JS__", text)


if __name__ == "__main__":
    unittest.main()
