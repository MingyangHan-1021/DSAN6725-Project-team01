"""Tests for merging and balancing unlabelled evaluation candidates."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.prepare_eval_candidates import prepare


class PrepareEvalCandidatesTests(unittest.TestCase):
    def test_deduplicates_normalizes_and_omits_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"ID": "1", "TITLE": "Company reports earnings and revenue", "SOURCE": "Bloomberg Markets",
                 "URL": "https://example.com/1", "PUBLISHED_AT": "2026-10-20T10:00:00+00:00",
                 "RETRIEVED_AT": "2026-10-20T11:00:00+00:00", "CONTENT": "A" * 100},
                {"ID": "1", "TITLE": "Duplicate", "SOURCE": "Bloomberg Technology",
                 "URL": "https://example.com/1", "PUBLISHED_AT": "2026-10-20T10:00:00+00:00",
                 "RETRIEVED_AT": "2026-10-20T11:00:00+00:00", "CONTENT": "B"},
                {"ID": "2", "TITLE": "A product launch", "SOURCE": "CNBC",
                 "URL": "https://example.com/2", "PUBLISHED_AT": "2026-10-19T10:00:00+00:00",
                 "RETRIEVED_AT": "2026-10-20T11:00:00+00:00", "CONTENT": "C"},
            ]
            path = root / "input.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            selected, stats = prepare([path], limit=2, max_per_source=1)
            self.assertEqual(stats, {"raw": 3, "unique": 2, "duplicates_removed": 1})
            self.assertEqual({row["SOURCE"] for row in selected}, {"Bloomberg", "CNBC"})
            self.assertTrue(all("PRIMARY_TICKER" not in row and "EVENT_TYPE" not in row for row in selected))


if __name__ == "__main__":
    unittest.main()
