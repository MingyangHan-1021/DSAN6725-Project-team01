"""Focused contract and pipeline tests using only synthetic evidence."""
import json
from datetime import timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from stock_agent.ingestion import parse_news, parse_market
from stock_agent.memory import HashEmbedding, QdrantMemory
from stock_agent.models import DataError, timestamp
from stock_agent.scoring import news_features, rank
from stock_agent.storage import connect, ingest, articles_as_of, bars_as_of

ROOT = Path(__file__).resolve().parents[1]
NEWS = ROOT / "data/samples/news.txt"
MARKET = ROOT / "data/samples/market_data.csv"
CONFIG = json.loads((ROOT / "config/scoring.json").read_text())
RETRIEVED = timestamp("2026-10-08T08:00:00-04:00", "retrieved")
AS_OF = timestamp("2026-10-08T09:00:00-04:00", "as_of")

class Agent1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name) / "agent1.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def populate(self):
        return ingest(self.db, parse_news(NEWS, RETRIEVED), parse_market(MARKET), RETRIEVED)

    def test_ingestion_idempotent_and_rank_stable(self):
        first = self.populate()
        second = self.populate()
        self.assertEqual(first, second)
        one = rank(self.db, AS_OF, CONFIG)
        two = rank(self.db, AS_OF, CONFIG)
        self.assertEqual(one, two)
        self.assertEqual([item["ticker"] for item in one["ranked"]], ["ALDR", "BRCH"])
        self.assertEqual(one["ranked"][0]["evidence_ids"], ["SYN-001"])
        self.assertEqual(one["ranked"][0]["main_signals"]["verified_headlines"], 1)

    def test_earliest_availability_controls_historical_queries(self):
        self.populate()
        before = RETRIEVED - timedelta(seconds=1)
        self.assertEqual(articles_as_of(self.db, before - timedelta(days=7), before), [])
        self.assertEqual(bars_as_of(self.db, before, "1Day", "regular"), [])
        self.assertEqual(rank(self.db, before, CONFIG)["ranked"], [])

    def test_seven_day_boundary_and_old_history(self):
        self.populate()
        start = AS_OF - timedelta(days=7)
        # Inject a synthetic article exactly on the inclusive boundary, preserving provenance.
        self.db.execute("""INSERT INTO articles (
            article_id,title,source,url,published_at,retrieved_at,available_at,is_headline,
            content,raw_text,content_hash,canonical_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("BOUNDARY", "Synthetic boundary", "Synthetic Wire", "https://example.invalid/boundary",
             start.isoformat(), RETRIEVED.astimezone(start.tzinfo).isoformat(),
             RETRIEVED.astimezone(start.tzinfo).isoformat(), "false", "Synthetic", "Synthetic",
             "boundary-hash", "boundary-key"))
        self.db.execute("INSERT INTO article_tickers VALUES (?,?)", ("BOUNDARY", "ALDR"))
        self.db.commit()
        rows = articles_as_of(self.db, start, AS_OF)
        ids = {row["article_id"] for row in rows}
        self.assertIn("BOUNDARY", ids)
        self.assertNotIn("SYN-004", ids)
        self.assertEqual(self.db.execute("SELECT count(*) FROM articles").fetchone()[0], 5)

    def test_parser_rejects_bad_market_bar_and_naive_time(self):
        self.assertRaises(DataError, timestamp, "2026-10-08T09:00:00", "as_of")
        path = Path(self.temp.name) / "bad.csv"
        path.write_text("ticker,timestamp,timeframe,session,open,high,low,close,volume\n"
                        "ALDR,2026-10-07T16:00:00-04:00,1Day,regular,10,9,8,11,100\n")
        with self.assertRaisesRegex(DataError, "line 2"):
            parse_market(path)

    def test_dedup_keeps_source_breadth(self):
        self.populate()
        data = news_features(articles_as_of(self.db, AS_OF - timedelta(days=7), AS_OF))["ALDR"]
        self.assertEqual(data["unique_articles"], 1)
        self.assertEqual(data["source_breadth"], 2)
        self.assertIsNone(data["unique_events"])

    def test_memory_filter_includes_availability(self):
        class FakeMemory(QdrantMemory):
            def _request(self, method, path, data=None):
                self.last_request = data
                return {"result": {"points": []}}
        memory = FakeMemory("http://unused", "test", HashEmbedding())
        self.assertEqual(memory.search_memory("Alder", ["ALDR"], published_before=AS_OF), [])
        filters = memory.last_request["filter"]["must"]
        self.assertTrue(any(item.get("key") == "available_at" for item in filters))
        self.assertTrue(any(item.get("key") == "published_at" for item in filters))

if __name__ == "__main__":
    unittest.main()
