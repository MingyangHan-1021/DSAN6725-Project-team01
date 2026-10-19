"""Tests for the provider-independent news formatting boundary."""
import json
from pathlib import Path
import tempfile
import unittest

from stock_agent.ingestion import parse_news
from stock_agent.models import DataError, timestamp
from stock_agent.storage import connect, ingest
from tools.news_formatter import format_jsonl


class NewsFormatterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.retrieved = timestamp("2026-10-15T12:00:00-04:00", "retrieved")

    def tearDown(self):
        self.temp.cleanup()

    def record(self) -> dict:
        return {
            "ID": "TEST-DELL-001",
            "TITLE": "Broker changes Dell rating",
            "SOURCE": "Synthetic Wire",
            "URL": "https://example.invalid/dell-rating",
            "PUBLISHED_AT": "2026-10-15T10:00:00-04:00",
            "TICKERS": ["DELL", "HPE"],
            "PRIMARY_TICKER": "DELL",
            "EVENT_TYPE": "rating",
            "IS_HEADLINE": "unknown",
            "CONTENT": "Synthetic content for contract testing only."
        }

    def write_jsonl(self, record: dict) -> Path:
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return source

    def test_round_trip_preserves_classification(self):
        output = self.root / "news.txt"
        count = format_jsonl(self.write_jsonl(self.record()), output, self.retrieved)
        self.assertEqual(count, 1)
        article = parse_news(output, self.retrieved)[0]
        self.assertEqual(article.primary_ticker, "DELL")
        self.assertEqual(article.event_type, "rating")
        with connect(self.root / "agent1.sqlite3") as db:
            ingest(db, [article], [], self.retrieved)
            stored = db.execute(
                "SELECT primary_ticker,event_type FROM articles WHERE article_id=?",
                (article.article_id,),
            ).fetchone()
            self.assertEqual(tuple(stored), ("DELL", "rating"))

    def test_primary_ticker_must_be_in_tickers(self):
        record = self.record()
        record["PRIMARY_TICKER"] = "ORCL"
        with self.assertRaisesRegex(DataError, "must also appear"):
            format_jsonl(self.write_jsonl(record), self.root / "news.txt", self.retrieved)

    def test_event_type_is_controlled(self):
        record = self.record()
        record["EVENT_TYPE"] = "analyst-opinion"
        with self.assertRaisesRegex(DataError, "unsupported EVENT_TYPE"):
            format_jsonl(self.write_jsonl(record), self.root / "news.txt", self.retrieved)

    def test_legacy_article_can_gain_but_not_change_classification(self):
        classified_output = self.root / "classified.txt"
        format_jsonl(self.write_jsonl(self.record()), classified_output, self.retrieved)
        classified = parse_news(classified_output, self.retrieved)[0]
        legacy_text = classified_output.read_text(encoding="utf-8")
        legacy_text = "\n".join(
            line for line in legacy_text.splitlines()
            if not line.startswith(("PRIMARY_TICKER:", "EVENT_TYPE:"))
        ) + "\n"
        legacy_output = self.root / "legacy.txt"
        legacy_output.write_text(legacy_text, encoding="utf-8")
        legacy = parse_news(legacy_output, self.retrieved)[0]

        with connect(self.root / "upgrade.sqlite3") as db:
            ingest(db, [legacy], [], self.retrieved)
            ingest(db, [classified], [], self.retrieved)
            stored = db.execute(
                "SELECT primary_ticker,event_type FROM articles WHERE article_id=?",
                (classified.article_id,),
            ).fetchone()
            self.assertEqual(tuple(stored), ("DELL", "rating"))

            changed = self.record()
            changed["PRIMARY_TICKER"] = "HPE"
            changed_output = self.root / "changed.txt"
            format_jsonl(self.write_jsonl(changed), changed_output, self.retrieved)
            with self.assertRaisesRegex(DataError, "changed classification"):
                ingest(db, parse_news(changed_output, self.retrieved), [], self.retrieved)


if __name__ == "__main__":
    unittest.main()
