"""Offline check of the production news path; no feed, model, or Qdrant calls."""
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.auto_label_news import EventType, ModelLabel
from tools.collect_agent1_news import label_optional, run

ROOT = Path(__file__).resolve().parents[1]


class Agent1NewsCollectionTests(unittest.TestCase):
    def test_optional_labels_use_injected_client_without_network(self):
        class FakeResponse:
            output_parsed = ModelLabel(primary_ticker="META", event_type=EventType.earnings)
            usage = None
            _request_id = "offline-test"

        class FakeClient:
            class responses:
                @staticmethod
                def parse(**kwargs):
                    return FakeResponse()

        row = {"ID": "SYN-1", "TITLE": "Meta earnings", "SOURCE": "Synthetic Wire",
               "URL": "https://example.invalid/meta", "PUBLISHED_AT": "2026-10-23T10:00:00-04:00",
               "TICKERS": ["META"], "CONTENT": "Meta reported synthetic test results."}
        labelled, telemetry = label_optional([row], "fake-model", FakeClient())
        self.assertEqual(labelled[0]["PRIMARY_TICKER"], "META")
        self.assertEqual(labelled[0]["EVENT_TYPE"], "earnings")
        self.assertEqual(telemetry[0]["MODEL"], "fake-model")
        self.assertNotIn("PRIMARY_TICKER", row)

    def test_all_eligible_articles_are_stored_without_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                {"ID": f"SYN-META-{number}", "TITLE": "Synthetic Meta earnings story",
                 "SOURCE": "Synthetic Wire", "URL": f"https://example.invalid/meta-{number}",
                 "PUBLISHED_AT": "2026-10-23T10:00:00-04:00",
                 "RETRIEVED_AT": "2026-10-23T11:00:00-04:00",
                 "CONTENT": "Meta reported synthetic numbers for this offline contract test."}
                for number in range(3)
            ]
            result = run(rows, datetime(2026, 10, 23, 15, tzinfo=timezone.utc),
                         root / "batch", root / "news.sqlite3",
                         ROOT / "config/sp500_companies.csv")
            self.assertEqual(result["articles_in_batch"], 3)
            self.assertIsNone(result["label_model"])
            self.assertIsNone(result["indexed_articles"])
            with closing(sqlite3.connect(root / "news.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM articles").fetchone()[0], 3)
                self.assertEqual(db.execute("SELECT count(*) FROM article_tickers WHERE ticker='META'").fetchone()[0], 3)
            self.assertTrue((root / "batch_news.txt").exists())
            self.assertTrue((root / "batch_manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
