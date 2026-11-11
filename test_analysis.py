from pathlib import Path
import unittest

import pandas as pd

from analysis import analyze_store, parse_price, parse_timestamp, reconcile_users
from build_database import build
from data_api import DataAPI


class AnalysisTests(unittest.TestCase):
    def test_incomplete_dates_cannot_become_today(self):
        for raw in ("12:30", "2026", "October 29", "2026-10", "", None, "invalid"):
            with self.subTest(raw=raw):
                self.assertTrue(pd.isna(parse_timestamp(raw)))
        for raw in ("2026-10-29 12:30", "29 Oct 2026; 12:30 PM", "2026-02-28"):
            with self.subTest(raw=raw):
                self.assertFalse(pd.isna(parse_timestamp(raw)))
        self.assertTrue(pd.isna(parse_timestamp("2026-02-30")))

    def test_price_formats_and_conversion(self):
        self.assertEqual(str(parse_price("EUR 60")), "72.00")
        self.assertEqual(str(parse_price("51.25 $")), "51.25")
        self.assertEqual(str(parse_price("$55�50")), "55.50")

    def test_reconciliation_allows_one_changed_field(self):
        users = pd.DataFrame(
            [
                {"id": 1, "name": "Ada Lovelace", "address": "1 Main St", "phone": "111-222", "email": "ada@example.test"},
                {"id": 2, "name": "A. Lovelace", "address": "1 Main St", "phone": "111222", "email": "ada@example.test"},
                {"id": 3, "name": "Grace Hopper", "address": "2 Main St", "phone": "333444", "email": "grace@example.test"},
            ]
        )
        reconciled, _ = reconcile_users(users)
        self.assertEqual(reconciled["entity_id"].nunique(), 2)

    def test_store1_end_to_end(self):
        store = analyze_store(Path(__file__).resolve().parent / "data" / "STORE1")
        self.assertEqual(store["store"], "STORE1")
        self.assertEqual(len(store["top_days"]), 5)
        self.assertGreater(store["unique_users"], 0)
        self.assertGreater(store["unique_author_sets"], 0)
        self.assertTrue(store["daily_revenue"])

    @classmethod
    def setUpClass(cls):
        project = Path(__file__).resolve().parent
        cls.database = project / "analytics.sqlite3"
        if not cls.database.exists():
            build(project / "data", cls.database)
        cls.api = DataAPI(cls.database)

    def test_email_prefix_filter_returns_database_count(self):
        result = self.api.users("STORE2", ["s", "p"], None, 100)
        self.assertGreater(result["matched_rows"], 0)
        self.assertTrue(all(row["email"].casefold().startswith(("s", "p")) for row in result["rows"]))

    def test_person_name_title_filter_is_applied(self):
        result = self.api.books("STORE3", None, None, "exclude", 100)
        self.assertGreater(result["matched_books"], 0)
        self.assertTrue(all(row["title_has_person_name"] == 0 for row in result["rows"]))

    def test_dashboard_action_is_explicit(self):
        action = self.api.dashboard_action("STORE1", "books", "exclude", [], None, None, None)
        self.assertEqual(action["type"], "dashboard_update")
        self.assertEqual(action["filters"]["person_name_filter"], "exclude")


if __name__ == "__main__":
    unittest.main()
