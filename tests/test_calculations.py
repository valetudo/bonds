"""Unit tests for the pure-function calculations module."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calculations import (  # noqa: E402
    average_yield,
    coupon_from_name,
    currency_from_name,
    duration_bucket,
    enrich_bond,
    find_anomalies,
    geo_area_from_isin,
    is_inflation_linked,
    issuer_type_from_name,
    net_annual_yield,
    yield_by_nation,
    years_to_maturity,
)


class InflationLinkedTests(unittest.TestCase):
    def test_btpi(self):
        self.assertTrue(is_inflation_linked("Btpi Tf 2,4% Mg39 Eur"))
        self.assertTrue(is_inflation_linked("BTPi 0.5% 2030"))
    def test_bundei(self):
        self.assertTrue(is_inflation_linked("Bundei 0,5% Ap30 Eur"))
    def test_tips(self):
        self.assertTrue(is_inflation_linked("US Treasury TIPS 2.0% 2030"))
    def test_inflation_token(self):
        self.assertTrue(is_inflation_linked("Some Bond Inflation Linked"))
        self.assertTrue(is_inflation_linked("BTP Indicizzato Inflazione"))
    def test_negatives(self):
        self.assertFalse(is_inflation_linked("Btp Tf 5% 2030"))
        self.assertFalse(is_inflation_linked("Bund 4,75% 2040"))
        self.assertFalse(is_inflation_linked(""))


class GeoAndIssuerTests(unittest.TestCase):
    def test_geo_area_known_prefixes(self) -> None:
        self.assertEqual(geo_area_from_isin("IT0001234567"), "Italia")
        self.assertEqual(geo_area_from_isin("DE0001"), "Germania")
        self.assertEqual(geo_area_from_isin("US123"), "USA")
        self.assertEqual(geo_area_from_isin("XS0001"), "Eurobond/Intl")

    def test_geo_area_unknown_prefix(self) -> None:
        self.assertEqual(geo_area_from_isin("ZZ0001"), "Altro")
        self.assertEqual(geo_area_from_isin(""), "Altro")
        self.assertEqual(geo_area_from_isin(None), "Altro")  # type: ignore[arg-type]

    def test_issuer_type_government(self) -> None:
        self.assertEqual(issuer_type_from_name("BTP 1.65 OT2032"), "Government")
        self.assertEqual(issuer_type_from_name("BOT 12M"), "Government")
        self.assertEqual(issuer_type_from_name("OAT 2030"), "Government")
        self.assertEqual(issuer_type_from_name("US TREASURY 2.5 2031"), "Government")

    def test_issuer_type_corporate(self) -> None:
        self.assertEqual(issuer_type_from_name("ENEL FINANCE 2030"), "Corporate")
        self.assertEqual(issuer_type_from_name("Generali 2028"), "Corporate")
        self.assertEqual(issuer_type_from_name(""), "Corporate")

    def test_issuer_type_supranationals(self) -> None:
        # Italian white-list supranationals should be Government for tax purposes
        self.assertEqual(issuer_type_from_name("Eib Tf 4,875% Mz28 Eur"), "Government")
        self.assertEqual(issuer_type_from_name("Bei Sustainable Fx 0.875% May30 Usd"), "Government")
        self.assertEqual(issuer_type_from_name("Afdb Tf 4,375% Mz28 Usd"), "Government")
        self.assertEqual(issuer_type_from_name("World Bank 2.5 2030 USD"), "Government")
        self.assertEqual(issuer_type_from_name("Bundei 0,5% Ap30 Eur"), "Government")

    def test_issuer_type_more_supranationals_and_sovereigns(self) -> None:
        # Bonds we found misclassified during sample audit
        self.assertEqual(issuer_type_from_name("Bonos Fx 2.7% Jan30 Eur"), "Government")
        self.assertEqual(issuer_type_from_name("Worldbank Sustainable Tf 3,875% Fb30 Usd"), "Government")
        self.assertEqual(issuer_type_from_name("Eu Next Gen Tf 2% Ot27 Eur"), "Government")
        self.assertEqual(issuer_type_from_name("Eu Next Gen Ukr Fa Fx 2.5% Oct30 Eur"), "Government")
        self.assertEqual(issuer_type_from_name("Ebrd Tf 14,2% Nv26 Try"), "Government")

    def test_issuer_type_sovereign_country_prefix(self) -> None:
        # Foreign sovereign issuers identified by leading country name
        self.assertEqual(issuer_type_from_name("Poland Tf 5,75% Nv32 Call Usd"), "Government")
        self.assertEqual(issuer_type_from_name("Hungary 6.25 2029 USD"), "Government")
        self.assertEqual(issuer_type_from_name("Mexico 4% 2034 USD"), "Government")
        self.assertEqual(issuer_type_from_name("Romania Tf 3% 2030 EUR"), "Government")
        # But corporates that just contain a country word stay corporate
        self.assertEqual(issuer_type_from_name("Bank Polski Tier2 2030 EUR"), "Corporate")
        self.assertEqual(issuer_type_from_name("Polish Telecom 3% 2032 EUR"), "Corporate")


class CouponFromNameTests(unittest.TestCase):
    def test_extracts_percent(self) -> None:
        self.assertEqual(coupon_from_name("Btp-1nv26 7,25%"), 7.25)
        self.assertEqual(coupon_from_name("Bundei 0,5% Ap30 Eur"), 0.5)
        self.assertEqual(coupon_from_name("Eib Tf 4,875% Mz28 Eur"), 4.875)
        self.assertEqual(coupon_from_name("Oat Tf 2,75% Ot27 Eur"), 2.75)

    def test_old_oat_style_trailing_decimal(self) -> None:
        # OATs from older issuance series have "5,75" at the end with no '%'
        self.assertEqual(coupon_from_name("Oat Oct32 Eur 5,75"), 5.75)
        self.assertEqual(coupon_from_name("Oat Ott38 Eur 4"), None)  # int trailing isn't a coupon

    def test_zero_coupon_token(self) -> None:
        self.assertEqual(coupon_from_name("Comit-97/27 Zc"), 0.0)
        self.assertEqual(coupon_from_name("Btpstripital Zc Dec26 Eur"), 0.0)
        self.assertEqual(coupon_from_name("Bund Green Bond Tf 0% Ag50 Eur"), 0.0)

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(coupon_from_name(""))
        self.assertIsNone(coupon_from_name(None))  # type: ignore[arg-type]
        self.assertIsNone(coupon_from_name("BTP IT2030"))  # no % marker, no Zc

    def test_sanity_range_rejects_giant_numbers(self) -> None:
        # 100% would be unrealistic for a bond coupon; should be rejected
        self.assertIsNone(coupon_from_name("Bond 200% something"))


class CurrencyTests(unittest.TestCase):
    def test_3letter_codes(self):
        self.assertEqual(currency_from_name("BANCO BPM 7.50 USD PERP"), "USD")
        self.assertEqual(currency_from_name("BUND 2.5 EUR 2030"), "EUR")
        self.assertEqual(currency_from_name("Treasury 4.5 GBP 2034"), "GBP")
        self.assertEqual(currency_from_name("CHF coupon bond 1.5"), "CHF")

    def test_2letter_borsa_italiana_shorthand(self):
        self.assertEqual(currency_from_name("BTP-1.65 OT2032 EU"), "EUR")
        self.assertEqual(currency_from_name("BTP GREEN 30AP2045 EU"), "EUR")
        # 2-letter only when standalone token
        self.assertEqual(currency_from_name("MORGAN STANLEY 4.5 US 2034"), "USD")

    def test_default_when_unknown(self):
        self.assertEqual(currency_from_name("NESSUN INDIZIO QUI"), "EUR")
        self.assertEqual(currency_from_name(""), "EUR")
        self.assertEqual(currency_from_name(None), "EUR")  # type: ignore[arg-type]

    def test_no_false_positives_inside_words(self):
        # "useless" should not match US
        self.assertEqual(currency_from_name("useless coupon"), "EUR")


class MaturityTests(unittest.TestCase):
    def test_years_to_maturity_future(self) -> None:
        ref = date(2025, 1, 1)
        result = years_to_maturity("2030-01-01", reference=ref)
        self.assertIsNotNone(result)
        # ~5 years
        self.assertAlmostEqual(result, 5.0, delta=0.1)

    def test_years_to_maturity_past_returns_none(self) -> None:
        ref = date(2025, 1, 1)
        self.assertIsNone(years_to_maturity("2024-01-01", reference=ref))

    def test_years_to_maturity_invalid(self) -> None:
        self.assertIsNone(years_to_maturity(None))
        self.assertIsNone(years_to_maturity(""))
        self.assertIsNone(years_to_maturity("not-a-date"))

    def test_duration_bucket_boundaries(self) -> None:
        self.assertEqual(duration_bucket(None), "N/D")
        self.assertEqual(duration_bucket(0.5), "Short (<3y)")
        self.assertEqual(duration_bucket(2.99), "Short (<3y)")
        self.assertEqual(duration_bucket(3.0), "Medium (3-7y)")
        self.assertEqual(duration_bucket(7.0), "Medium (3-7y)")
        self.assertEqual(duration_bucket(7.01), "Long (>7y)")


class NetYieldTests(unittest.TestCase):
    def test_par_zero_coupon_returns_zero(self) -> None:
        ref = date(2025, 1, 1)
        # price=100, coupon=0, maturity=5y → yield = 0
        y = net_annual_yield(0.0, 100.0, "2030-01-01", "Government", ref)
        self.assertIsNotNone(y)
        self.assertAlmostEqual(y, 0.0, delta=0.05)

    def test_below_par_positive_yield(self) -> None:
        ref = date(2025, 1, 1)
        # price=90, coupon=2, 5y, govt → positive yield
        y = net_annual_yield(2.0, 90.0, "2030-01-01", "Government", ref)
        self.assertIsNotNone(y)
        self.assertGreater(y, 0)

    def test_government_yields_more_than_corporate(self) -> None:
        # Same parameters, only tax differs (gov 12.5%, corp 26%)
        ref = date(2025, 1, 1)
        gov = net_annual_yield(3.0, 95.0, "2030-01-01", "Government", ref)
        corp = net_annual_yield(3.0, 95.0, "2030-01-01", "Corporate", ref)
        self.assertIsNotNone(gov)
        self.assertIsNotNone(corp)
        self.assertGreater(gov, corp)

    def test_returns_none_for_missing_inputs(self) -> None:
        self.assertIsNone(net_annual_yield(2.0, None, "2030-01-01", "Government"))
        self.assertIsNone(net_annual_yield(2.0, 95.0, None, "Government"))
        self.assertIsNone(net_annual_yield(2.0, 0.0, "2030-01-01", "Government"))
        self.assertIsNone(net_annual_yield(2.0, -5.0, "2030-01-01", "Government"))


class EnrichTests(unittest.TestCase):
    def test_enrich_fills_derived_fields(self) -> None:
        ref = date(2025, 1, 1)
        bond = {
            "isin": "IT0001",
            "name": "BTP Test",
            "coupon": 2.0,
            "maturity_date": "2030-01-01",
            "latest_price": 95.0,
            "currency": "EUR",
        }
        out = enrich_bond(bond, reference=ref)
        self.assertEqual(out["geo_area"], "Italia")
        self.assertEqual(out["issuer_type"], "Government")
        self.assertEqual(out["duration_bucket"], "Medium (3-7y)")
        self.assertIsNotNone(out["years_to_maturity"])
        self.assertIsNotNone(out["net_yield_pa"])

    def test_enrich_with_no_price_yields_none(self) -> None:
        ref = date(2025, 1, 1)
        bond = {
            "isin": "IT0001", "name": "BTP No Price",
            "coupon": 2.0, "maturity_date": "2030-01-01",
            "latest_price": None, "currency": "EUR",
        }
        out = enrich_bond(bond, reference=ref)
        self.assertIsNone(out["net_yield_pa"])


class AggregateTests(unittest.TestCase):
    def test_average_yield_skips_nones(self) -> None:
        bonds = [
            {"net_yield_pa": 2.0},
            {"net_yield_pa": 4.0},
            {"net_yield_pa": None},
        ]
        self.assertEqual(average_yield(bonds), 3.0)

    def test_average_yield_empty(self) -> None:
        self.assertIsNone(average_yield([]))
        self.assertIsNone(average_yield([{"net_yield_pa": None}]))

    def test_find_anomalies_picks_top_spread(self) -> None:
        # Build a mini-universe of 5 IT-EUR-Government bonds, all 5y duration.
        # One has a much higher yield than the cluster.
        bonds = []
        for i, y in enumerate([2.0, 2.1, 1.9, 2.0, 4.0]):
            bonds.append({
                "isin": f"IT000{i}",
                "name": f"BTP {i}",
                "currency": "EUR",
                "geo_area": "Italia",
                "issuer_type": "Government",
                "years_to_maturity": 5.0,
                "net_yield_pa": y,
                "maturity_date": "2030-01-01",
            })
        out = find_anomalies(bonds, top_n=2, min_peers=2)
        self.assertEqual(len(out), 2)
        # Highest spread first
        self.assertEqual(out[0]["isin"], "IT0004")
        self.assertGreater(out[0]["spread"], out[1]["spread"])

    def test_yield_by_nation_aggregates_and_sorts(self) -> None:
        bonds = [
            {"geo_area": "Italia", "net_yield_pa": 3.0, "years_to_maturity": 5.0},
            {"geo_area": "Italia", "net_yield_pa": 4.0, "years_to_maturity": 5.0},
            {"geo_area": "Italia", "net_yield_pa": 5.0, "years_to_maturity": 5.0},
            {"geo_area": "Germania", "net_yield_pa": 1.0, "years_to_maturity": 5.0},
            {"geo_area": "Germania", "net_yield_pa": 1.5, "years_to_maturity": 5.0},
            {"geo_area": "Francia", "net_yield_pa": 2.0, "years_to_maturity": 5.0},
            {"geo_area": "USA", "net_yield_pa": None, "years_to_maturity": 5.0},  # skipped
            {"geo_area": "Italia", "net_yield_pa": None, "years_to_maturity": 5.0},  # skipped
        ]
        out = yield_by_nation(bonds)
        names = [r["nation"] for r in out]
        self.assertEqual(names, ["Italia", "Francia", "Germania"])
        self.assertEqual(out[0]["count"], 3)
        self.assertAlmostEqual(out[0]["avg"], 4.0)
        self.assertEqual(out[0]["median"], 4.0)

    def test_yield_by_nation_min_count_filter(self) -> None:
        bonds = [
            {"geo_area": "Italia", "net_yield_pa": 3.0, "years_to_maturity": 5.0},
            {"geo_area": "Italia", "net_yield_pa": 4.0, "years_to_maturity": 5.0},
            {"geo_area": "Germania", "net_yield_pa": 1.0, "years_to_maturity": 5.0},
        ]
        out = yield_by_nation(bonds, min_count=2)
        self.assertEqual([r["nation"] for r in out], ["Italia"])

    def test_yield_by_nation_filters_short_maturity_and_outliers(self) -> None:
        bonds = [
            {"geo_area": "Italia", "net_yield_pa": 3.0, "years_to_maturity": 5.0},
            # < 3 months: excluded
            {"geo_area": "Italia", "net_yield_pa": 50.0, "years_to_maturity": 0.05},
            # extreme yield (>15%): excluded
            {"geo_area": "Italia", "net_yield_pa": 99.0, "years_to_maturity": 5.0},
            # zero / negative yield (often inflation-linked mis-tagged Plain Vanilla): excluded
            {"geo_area": "Italia", "net_yield_pa": 0.0, "years_to_maturity": 5.0},
            {"geo_area": "Italia", "net_yield_pa": -0.5, "years_to_maturity": 5.0},
        ]
        out = yield_by_nation(bonds)
        # Only the first row survives
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["count"], 1)
        self.assertAlmostEqual(out[0]["avg"], 3.0)

    def test_yield_by_nation_filter_by_tipologia(self) -> None:
        # Only sovereign typologies should be aggregated
        bonds = [
            {"geo_area": "Italia", "sovereign_nation": "Italia",
             "tipologia": "Titoli Di Stato Italiani",
             "currency": "EUR", "net_yield_pa": 3.0, "years_to_maturity": 9.0},
            {"geo_area": "Italia", "sovereign_nation": None,
             "tipologia": "Banche", "currency": "EUR",
             "net_yield_pa": 4.0, "years_to_maturity": 9.0},
            {"geo_area": "Italia", "sovereign_nation": None,
             "tipologia": "Corporate", "currency": "EUR",
             "net_yield_pa": 5.0, "years_to_maturity": 9.0},
            {"geo_area": "Germania", "sovereign_nation": "Germania",
             "tipologia": "Titoli Di Stato Esteri", "currency": "EUR",
             "net_yield_pa": 2.5, "years_to_maturity": 9.0},
        ]
        out = yield_by_nation(bonds, sovereign_only=True)
        nations = [r["nation"] for r in out]
        # Banche/Corporate excluded; Italian govt + German govt remain
        self.assertIn("Italia", nations)
        self.assertIn("Germania", nations)
        self.assertEqual(len(out), 2)

    def test_yield_by_nation_filter_by_currency(self) -> None:
        bonds = [
            {"geo_area": "Italia", "sovereign_nation": "Italia",
             "tipologia": "Titoli Di Stato Italiani",
             "currency": "EUR", "net_yield_pa": 3.0, "years_to_maturity": 9.0},
            {"geo_area": "Italia", "sovereign_nation": "Italia",
             "tipologia": "Eurobonds Republic Of Italy",
             "currency": "USD", "net_yield_pa": 4.5, "years_to_maturity": 9.0},
        ]
        out = yield_by_nation(bonds, currency="EUR", sovereign_only=True, min_count=1)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["median"], 3.0)

    def test_yield_by_nation_empty(self) -> None:
        self.assertEqual(yield_by_nation([]), [])
        self.assertEqual(yield_by_nation([{"geo_area": "Italia", "net_yield_pa": None}]), [])

    def test_find_anomalies_filters_non_italian(self) -> None:
        bonds = [
            {
                "isin": "DE0001", "name": "Bund", "currency": "EUR",
                "geo_area": "Germania", "issuer_type": "Government",
                "years_to_maturity": 5.0, "net_yield_pa": 8.0,
            },
            {
                "isin": "IT0001", "name": "BTP", "currency": "EUR",
                "geo_area": "Italia", "issuer_type": "Government",
                "years_to_maturity": 5.0, "net_yield_pa": 2.0,
            },
            {
                "isin": "IT0002", "name": "BTP", "currency": "EUR",
                "geo_area": "Italia", "issuer_type": "Government",
                "years_to_maturity": 5.0, "net_yield_pa": 2.1,
            },
            {
                "isin": "IT0003", "name": "BTP", "currency": "EUR",
                "geo_area": "Italia", "issuer_type": "Government",
                "years_to_maturity": 5.0, "net_yield_pa": 1.9,
            },
        ]
        out = find_anomalies(bonds, top_n=5, min_peers=2)
        # German bund must NOT appear despite having the highest absolute yield
        self.assertNotIn("DE0001", [r["isin"] for r in out])


if __name__ == "__main__":
    unittest.main()
