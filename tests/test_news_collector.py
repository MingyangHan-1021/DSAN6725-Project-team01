"""Offline tests for the unlabelled RSS/Atom news collector."""
from datetime import datetime, timezone
import unittest

from tools.news_collector import canonical_url, collect, parse_feed


RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Company &amp; supplier announce a product</title>
    <link>https://example.com/story?utm_source=test&amp;ref=feed</link>
    <pubDate>Mon, 19 Oct 2026 14:30:00 -0400</pubDate>
    <description><![CDATA[<p>A factual <b>summary</b>.</p>]]></description>
    <source>Original Publisher</source>
  </item>
  <item><title>Incomplete record</title></item>
</channel></rss>"""


class NewsCollectorTests(unittest.TestCase):
    def test_parse_feed_produces_unlabelled_record(self):
        retrieved = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        records = parse_feed(RSS, "Example Publisher", retrieved)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["SOURCE"], "Original Publisher")
        self.assertEqual(record["URL"], "https://example.com/story?ref=feed")
        self.assertEqual(record["PUBLISHED_AT"], "2026-10-19T14:30:00-04:00")
        self.assertEqual(record["CONTENT"], "A factual summary .")
        self.assertNotIn("PRIMARY_TICKER", record)
        self.assertNotIn("EVENT_TYPE", record)

    def test_canonical_url_removes_tracking_and_fragment(self):
        url = "HTTPS://Example.COM/a?gclid=x&keep=1#section"
        self.assertEqual(canonical_url(url), "https://example.com/a?keep=1")

    def test_collect_skips_unavailable_feed(self):
        records = collect(
            [{"source": "Unavailable", "url": "invalid://feed"}],
            datetime(2026, 10, 20, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
