"""Offline tests for reproducible model prediction output."""
from pathlib import Path
import tempfile
import unittest

from tools.auto_label_news import EventType, ModelLabel, write_predictions


class _Usage:
    input_tokens = 100
    output_tokens = 12


class _Response:
    output_parsed = ModelLabel(primary_ticker="dell", event_type=EventType.rating)
    usage = _Usage()
    _request_id = "req_test"


class _Responses:
    def parse(self, **kwargs):
        return _Response()


class _Client:
    responses = _Responses()


class AutoLabelNewsTests(unittest.TestCase):
    def test_writes_prediction_without_changing_article(self):
        article = {"ID": "NEWS-1", "TITLE": "Dell falls after downgrade", "SOURCE": "Test",
                   "PUBLISHED_AT": "2026-10-20T10:00:00+00:00", "TICKERS": ["DELL", "HPE"],
                   "CONTENT": "Dell was downgraded."}
        original = dict(article)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "predictions.jsonl"
            count = write_predictions(_Client(), [article], output, "gpt-test")
            self.assertEqual(count, 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn('"TICKERS": ["DELL", "HPE"]', text)
            self.assertIn('"PRIMARY_TICKER": "DELL"', text)
            self.assertIn('"EVENT_TYPE": "rating"', text)
            self.assertEqual(article, original)


if __name__ == "__main__":
    unittest.main()
