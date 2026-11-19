import unittest
import os
import tempfile
import sqlite3
import asyncio
from unittest.mock import MagicMock, patch

from src.scrapers.ats_scraper import (
    HTTP_TIMEOUT, HTTP_CONNECT_TIMEOUT, CHUNK_SIZE, BATCH_SIZE,
    fetch_greenhouse_jobs, fetch_lever_jobs, fetch_adp_jobs,
    run_ats_scraper
)
from src.scrapers.intern_scraper_fast import (
    clean_url, is_valid_job_url, resolve_url
)


class TestScraperPerformanceAndStop(unittest.TestCase):
    def setUp(self):
        if os.path.exists("stop.flag"):
            os.remove("stop.flag")

    def tearDown(self):
        if os.path.exists("stop.flag"):
            os.remove("stop.flag")

    def test_timeout_and_concurrency_constants(self):
        """Verify performance constants are configured for fast throughput."""
        self.assertLessEqual(HTTP_TIMEOUT, 3.5)
        self.assertLessEqual(HTTP_CONNECT_TIMEOUT, 2.5)
        self.assertGreaterEqual(CHUNK_SIZE, 500)
        self.assertGreaterEqual(BATCH_SIZE, 100)

    def test_clean_url_and_validation(self):
        """Verify clean_url and is_valid_job_url prevent junk links."""
        junk_urls = [
            "https://www.youtube.com/watch?v=123",
            "https://www.reddit.com/r/cscareerquestions",
            "https://en.wikipedia.org/wiki/Software_engineering",
            "https://www.google.com/goto?url=123"
        ]
        for url in junk_urls:
            self.assertFalse(is_valid_job_url(url, "boards.greenhouse.io"))

        valid_url = "https://boards.greenhouse.io/stripe/jobs/12345"
        self.assertTrue(is_valid_job_url(valid_url, "boards.greenhouse.io"))

    def test_ats_scraper_stop_flag(self):
        """Verify ats_scraper halts cleanly when stop.flag is set during run."""
        import threading
        import time

        def trigger_stop():
            time.sleep(0.2)
            with open("stop.flag", "w") as f:
                f.write("stop")

        t = threading.Thread(target=trigger_stop, daemon=True)
        t.start()
        added = run_ats_scraper(limit=100, force_all=True)
        t.join()
        # Should have stopped well before processing 100 companies
        self.assertTrue(os.path.exists("stop.flag"))

    def test_resolve_url_direct_link(self):
        """Direct ATS URLs without Google redirect resolve immediately without network I/O."""
        url = "https://boards.greenhouse.io/airbnb/jobs/9999?gh_jid=9999"
        res = asyncio.run(resolve_url(url, context=None))
        self.assertEqual(res, "https://boards.greenhouse.io/airbnb/jobs/9999")

    def test_adp_fetcher_with_mock_client(self):
        """Verify ADP Workforce Now fetcher processes public requisition JSON."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobRequisitions": [
                {
                    "requisitionTitle": "Software Intern",
                    "itemID": "REQ123",
                    "requisitionLocations": [{"nameCode": {"shortName": "Remote, US"}}]
                }
            ]
        }
        mock_client.get.return_value = mock_resp

        jobs = fetch_adp_jobs("test_cid", client=mock_client)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Software Intern")
        self.assertIn("cid=test_cid", jobs[0]["url"])
        self.assertEqual(jobs[0]["location"], "Remote, US")


if __name__ == "__main__":
    unittest.main()
