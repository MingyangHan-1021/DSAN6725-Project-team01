"""Focused contract and pipeline tests using only synthetic evidence."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
import sqlite3
import tempfile
import unittest
from stock_agent.ingestion import parse_news, parse_market
from stock_agent.memory import HashEmbedding, QdrantMemory
from stock_agent.models import DataError, timestamp
from stock_agent.scoring import news_features, percentile, rank
from stock_agent.storage import connect, ingest, articles_as_of, bars_as_of
from main import main

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
        self.assertEqual(one["ranked"][0]["evidence_ids"], ["SYN-001", "SYN-002"])
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
        self.assertEqual(data["article_mentions"], 2)
        self.assertEqual(data["unique_articles"], 1)
        self.assertEqual(data["source_breadth"], 2)
        self.assertEqual(data["verified_headlines"], 1)
        self.assertEqual(data["evidence_ids"], ["SYN-001", "SYN-002"])
        self.assertIsNone(data["unique_events"])

    def test_repeated_article_same_source_counts_once_for_breadth(self):
        self.populate()
        self.db.execute("UPDATE articles SET source='Synthetic Wire' WHERE article_id='SYN-002'")
        self.db.commit()
        data = news_features(articles_as_of(self.db, AS_OF - timedelta(days=7), AS_OF))["ALDR"]
        self.assertEqual(data["article_mentions"], 2)
        self.assertEqual(data["unique_articles"], 1)
        self.assertEqual(data["source_breadth"], 1)

    def test_constant_zero_headlines_do_not_add_news_score(self):
        self.assertEqual(percentile({"ALDR": 0, "BRCH": 0}), {"ALDR": 0.0, "BRCH": 0.0})
        self.assertEqual(percentile({"ALDR": 2, "BRCH": 2}), {"ALDR": 0.0, "BRCH": 0.0})
        self.populate()
        self.db.execute("UPDATE articles SET is_headline='unknown'")
        self.db.commit()
        output = rank(self.db, AS_OF, CONFIG)
        self.assertTrue(all(row["main_signals"]["verified_headlines"] == 0 for row in output["ranked"]))
        self.assertEqual(output["ranked"][0]["news_score"], 2 / 3)
        self.assertEqual(output["ranked"][1]["news_score"], 0.0)

    def test_news_and_market_have_independent_availability(self):
        market_retrieved = timestamp("2026-10-08T07:00:00-04:00", "market")
        between = timestamp("2026-10-08T07:30:00-04:00", "cutoff")
        ingest(self.db, parse_news(NEWS, RETRIEVED), parse_market(MARKET), market_retrieved)
        self.assertEqual(articles_as_of(self.db, between - timedelta(days=7), between), [])
        self.assertEqual(len(bars_as_of(self.db, between, "1Day", "regular")), 12)
        result = rank(self.db, between, CONFIG)
        self.assertEqual(len(result["ranked"]), 2)
        self.assertTrue(all(row["news_score"] == 0 for row in result["ranked"]))

    def test_article_id_rejects_changed_provenance(self):
        self.populate()
        original = parse_news(NEWS, RETRIEVED)[0]
        for changed in (replace(original, source="Another source"),
                        replace(original, is_headline="false")):
            with self.assertRaisesRegex(DataError, "conflicts with stored evidence"):
                ingest(self.db, [changed], [], RETRIEVED)
        self.assertEqual(self.db.execute("SELECT count(*) FROM articles").fetchone()[0], 4)

    def test_equal_bar_with_different_decimal_format_is_idempotent(self):
        self.populate()
        original = parse_market(MARKET)[0]
        changed = replace(original, open=Decimal("10.0"))
        self.assertEqual(ingest(self.db, [], [changed], RETRIEVED)["bars_total"], 12)

    def test_cli_accepts_separate_retrieval_times(self):
        db_path = Path(self.temp.name) / "separate.sqlite3"
        with redirect_stdout(StringIO()) as output:
            status = main(["--db", str(db_path), "ingest", "--news", str(NEWS),
                           "--market", str(MARKET),
                           "--news-retrieved-at", "2026-10-08T08:00:00-04:00",
                           "--market-retrieved-at", "2026-10-08T07:00:00-04:00"])
        self.assertEqual(status, 0)
        self.assertNotEqual(json.loads(output.getvalue())["news_retrieved_at"],
                            json.loads(output.getvalue())["market_retrieved_at"])
        with closing(sqlite3.connect(db_path)) as db:
            news_time = db.execute("SELECT available_at FROM articles LIMIT 1").fetchone()[0]
            market_time = db.execute("SELECT available_at FROM bars LIMIT 1").fetchone()[0]
        self.assertEqual(news_time, "2026-10-08T12:00:00+00:00")
        self.assertEqual(market_time, "2026-10-08T11:00:00+00:00")

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
