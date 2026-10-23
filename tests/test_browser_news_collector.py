"""Offline browser and model fixtures for the source audit contract."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from tools.browser_news_collector import (
    ArticleFacts, LinkChoice, allowed_url, collect_all, collect_source, load_sources,
)
from tools.collect_agent1_news import run


class FakeBrowser:
    def __init__(self, pages):
        self.pages = pages

    def open(self, url):
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


class FakeReader:
    def choose_links(self, source, page_url, page_text, anchors):
        return LinkChoice(article_indices=[0, 1], next_index=-1)

    def extract(self, source, url, text, candidates):
        if "old" in url:
            return ArticleFacts(title="Older unrelated news", published_at="2026-10-21T10:00:00-04:00",
                                publication_evidence="Oct 21 10:00 EDT", publisher="", publisher_evidence="",
                                content="Older unrelated news in the test.",
                                relevant_tickers=[])
        return ArticleFacts(title="Meta reports synthetic earnings", published_at="2026-10-23T10:00:00-04:00",
                            publication_evidence="Oct 23 10:00 EDT", publisher="", publisher_evidence="",
                            content="Meta reported synthetic earnings in this offline browser test. Nvidia was mentioned in passing.",
                            relevant_tickers=["META"])


SOURCE = {"source": "Example News", "start_url": "https://example.test/news",
          "allowed_hosts": ["example.test"], "max_pages": 2, "max_articles": 5}
CUTOFF = datetime(2026, 10, 22, tzinfo=timezone.utc)


class BrowserCollectorTests(unittest.TestCase):
    def setUp(self):
        self.pages = {
            "https://example.test/news": ("Latest news", [
                {"text": "Meta reports earnings", "href": "https://example.test/meta"},
                {"text": "Older news", "href": "https://example.test/old"},
                {"text": "Outside link", "href": "https://elsewhere.test/story"}]),
            "https://example.test/meta": ("Meta reports synthetic earnings\nOct 23 10:00 EDT Meta reported synthetic earnings. Nvidia was mentioned in passing.", []),
            "https://example.test/old": ("Older unrelated news\nOct 21 10:00 EDT Older unrelated story.", []),
        }
        self.names = {"META": {"meta"}, "NVDA": {"nvidia"}}

    def test_collects_article_and_audits_cutoff(self):
        rows, report = collect_source(SOURCE, FakeBrowser(self.pages), FakeReader(),
                                      self.names, CUTOFF, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["TICKERS"], ["META"])
        self.assertEqual(rows[0]["IS_HEADLINE"], "unknown")
        self.assertTrue(report["cutoff_reached"])
        self.assertEqual(report["articles_opened"], ["https://example.test/meta", "https://example.test/old"])
        self.assertEqual(report["failures"], [])

    def test_failure_is_reported_without_claiming_full_coverage(self):
        pages = dict(self.pages)
        pages["https://example.test/meta"] = RuntimeError("HTTP 429")
        rows, manifest = collect_all([SOURCE], FakeBrowser(pages), FakeReader(), self.names, CUTOFF, 0)
        self.assertEqual(rows, [])
        self.assertEqual(manifest["sources"][0]["coverage_status"], "cutoff_reached_with_gaps")
        self.assertEqual(manifest["sources"][0]["failures"][0]["error"], "HTTP 429")
        self.assertFalse(manifest["coverage_verified"])

    def test_known_article_is_not_relabelled(self):
        rows, report = collect_source(SOURCE, FakeBrowser(self.pages), FakeReader(),
                                      self.names, CUTOFF, 0,
                                      {"https://example.test/meta": datetime(2026, 10, 23, tzinfo=timezone.utc)})
        self.assertEqual(rows, [])
        self.assertEqual(report["articles_skipped_known"], ["https://example.test/meta"])
        self.assertTrue(report["cutoff_reached"])

    def test_external_urls_are_never_opened(self):
        self.assertFalse(allowed_url("http://example.test/news", ["example.test"]))
        self.assertFalse(allowed_url("https://example.test.evil.test/news", ["example.test"]))
        self.assertTrue(allowed_url("https://example.test/news", ["example.test"]))

    def test_model_cannot_invent_link_indices(self):
        class BadReader(FakeReader):
            def choose_links(self, *args):
                return LinkChoice(article_indices=[99], next_index=-1)
        rows, report = collect_source(SOURCE, FakeBrowser(self.pages), BadReader(),
                                      self.names, CUTOFF, 0)
        self.assertEqual(rows, [])
        self.assertEqual(report["stop_reason"], "listing_failure")
        self.assertIn("absent", report["failures"][0]["error"])

    def test_browser_records_pass_through_existing_agent1_ingestion(self):
        rows, manifest = collect_all([SOURCE], FakeBrowser(self.pages), FakeReader(),
                                     self.names, CUTOFF, 0)
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            result = run(rows, datetime.now(timezone.utc), folder / "browser_batch",
                         folder / "agent1.sqlite3", root / "config/sp500_companies.csv",
                         browser_manifest=manifest)
            self.assertEqual(result["articles_in_batch"], 1)
            self.assertEqual(result["browser_collection"]["method"], "llm_assisted_browser")
            self.assertTrue((folder / "browser_batch_news.txt").exists())
            news_text = (folder / "browser_batch_news.txt").read_text(encoding="utf-8")
            self.assertIn("TICKERS: META", news_text)
            self.assertNotIn("TICKERS: META, NVDA", news_text)


if __name__ == "__main__":
    unittest.main()
