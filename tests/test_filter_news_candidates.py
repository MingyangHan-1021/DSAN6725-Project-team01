"""Tests for deterministic S&P 500 ticker attachment and content filtering."""
import unittest

from tools.filter_news_candidates import content_repeats_title, filter_records, find_tickers


class FilterNewsCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.names = {
            "DELL": {"dell technologies", "dell"},
            "HPE": {"hewlett packard enterprise"},
            "NFLX": {"netflix"},
            "ORCL": {"oracle"},
        }

    def test_finds_single_company(self):
        row = {"TITLE": "The Takeaways From Netflix's Earnings Report", "CONTENT": "Netflix beat estimates."}
        self.assertEqual(find_tickers(row, self.names), ["NFLX"])

    def test_finds_multiple_named_companies(self):
        row = {
            "TITLE": "Dell shares fall",
            "CONTENT": "Dell and Hewlett Packard Enterprise fell, while Oracle also declined.",
        }
        self.assertEqual(find_tickers(row, self.names), ["DELL", "HPE", "ORCL"])

    def test_does_not_match_company_inside_another_word(self):
        row = {"TITLE": "An ornate design", "CONTENT": "The story is not about Oracle."}
        self.assertEqual(find_tickers(row, {"T": {"at"}}), [])

    def test_detects_title_only_rss_description(self):
        title = "Netflix reports third-quarter earnings"
        self.assertTrue(content_repeats_title(title, title + " - Example News"))
        self.assertFalse(content_repeats_title(title, "Revenue beat estimates, but management forecast slower growth next quarter."))

    def test_filters_and_attaches_tickers(self):
        rows = [
            {"ID": "1", "TITLE": "Netflix earnings", "CONTENT": "Netflix revenue beat expectations."},
            {"ID": "2", "TITLE": "Unrelated story", "CONTENT": "No listed company is named here."},
            {"ID": "3", "TITLE": "Dell shares fall", "CONTENT": "Dell shares fall - News"},
        ]
        kept, stats = filter_records(rows, self.names)
        self.assertEqual(kept[0]["TICKERS"], ["NFLX"])
        self.assertEqual(stats, {
            "input": 3, "no_sp500_ticker": 1, "title_only_content": 1, "eligible": 1,
        })


if __name__ == "__main__":
    unittest.main()
