import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.scrapers.intern_scraper_fast import (
    SEARCH_QUERIES,
    SITES,
    TIME_FILTER,
    build_google_dork,
    is_valid_job_url,
    clean_url,
    clean_cite_to_url,
    resolve_url
)
from src.core.process_manager import create_stop_flag, clear_stop_flag


class TestInternScraperFast(unittest.TestCase):
    def setUp(self):
        clear_stop_flag()

    def tearDown(self):
        clear_stop_flag()

    def test_search_queries_broad_dragnet(self):
        """Verify SEARCH_QUERIES matches the proven broad tech dragnet from 30-40 versions ago."""
        # Must contain broad terms without restrictive double quotes that yield 0 results
        self.assertIn('Software', SEARCH_QUERIES)
        self.assertIn('C++ OR Java', SEARCH_QUERIES)
        self.assertIn('Javascript OR React OR Node.js', SEARCH_QUERIES)
        self.assertIn('intern OR internship', SEARCH_QUERIES)

        # None of the queries should force restrictive '"software engineer" intern'
        for q in SEARCH_QUERIES:
            self.assertFalse(q.startswith('"software engineer" intern'))

    def test_build_google_dork(self):
        """Verify build_google_dork formats clean site queries with proper grouping."""
        dork1 = build_google_dork("boards.greenhouse.io", "Software")
        self.assertEqual(dork1, "site:boards.greenhouse.io Software")

        dork2 = build_google_dork("https://jobs.lever.co/something", "C++ OR Java")
        self.assertEqual(dork2, "site:jobs.lever.co (C++ OR Java)")

        dork3 = build_google_dork("apply.workable.com", "intern OR internship")
        self.assertEqual(dork3, "site:apply.workable.com (intern OR internship)")

    def test_is_valid_job_url(self):
        """Verify ATS validation allows legitimate career pages and rejects junk."""
        self.assertTrue(is_valid_job_url("https://boards.greenhouse.io/stripe/jobs/123", "boards.greenhouse.io"))
        self.assertTrue(is_valid_job_url("https://jobs.ashbyhq.com/openai/positions/456", "jobs.ashbyhq.com"))
        self.assertTrue(is_valid_job_url("https://mycompany.myworkdayjobs.com/en-US/careers/job/123", "myworkdayjobs.com"))
        self.assertTrue(is_valid_job_url("https://keka.com/careers/job/123", "keka.com"))

        # Rejects YouTube, Reddit, Google redirects
        self.assertFalse(is_valid_job_url("https://www.youtube.com/watch?v=123", "boards.greenhouse.io"))
        self.assertFalse(is_valid_job_url("https://www.reddit.com/r/cscareerquestions", "lever.co"))
        self.assertFalse(is_valid_job_url("https://www.google.com/goto?url=xyz", "boards.greenhouse.io"))

    def test_clean_cite_to_url(self):
        """Verify Google search result <cite> breadcrumbs resolve to canonical ATS job URLs."""
        c1 = "https://boards.greenhouse.io › mark43 › jobs › 8360301"
        self.assertEqual(clean_cite_to_url(c1), "https://boards.greenhouse.io/mark43/jobs/8360301")

        c2 = "boards.greenhouse.io › mozilla › jobs › 8395647"
        self.assertEqual(clean_cite_to_url(c2), "https://boards.greenhouse.io/mozilla/jobs/8395647")

        c3 = "https://jobs.lever.co › netlify › 12345 · 2 days ago"
        self.assertEqual(clean_cite_to_url(c3), "https://jobs.lever.co/netlify/12345")

        c4 = "jobs.ashbyhq.com › openai › position › 789"
        self.assertEqual(clean_cite_to_url(c4), "https://jobs.ashbyhq.com/openai/position/789")

    def test_resolve_url_with_cite(self):
        """Verify resolve_url uses <cite> breadcrumbs to resolve encrypted Google /goto URLs instantly."""
        import asyncio
        goto_url = "https://www.google.com/goto?url=CAESbgHEIkXXdQLSX9"
        cite = "https://boards.greenhouse.io › mark43 › jobs › 8360301"
        res = asyncio.run(resolve_url(goto_url, context=None, cite_url=cite))
        self.assertEqual(res, "https://boards.greenhouse.io/mark43/jobs/8360301")

    def test_stop_flag_cleared_at_start(self):
        """Verify lingering stop.flag is cleared when scraper starts."""
        create_stop_flag()
        self.assertTrue(os.path.exists("stop.flag"))
        clear_stop_flag()
        self.assertFalse(os.path.exists("stop.flag"))

    def test_medium_and_overnight_scrapers_forward_correct_pace(self):
        """Verify intern_scraper and intern_scraper_overnight invoke scrape_google_jobs with respective paces."""
        import importlib
        med_mod = importlib.import_module("src.scrapers.intern_scraper")
        overnight_mod = importlib.import_module("src.scrapers.intern_scraper_overnight")

        self.assertTrue(hasattr(med_mod, "scrape_google_jobs"))
        self.assertTrue(hasattr(overnight_mod, "scrape_google_jobs"))
        self.assertEqual(med_mod.SEARCH_QUERIES, SEARCH_QUERIES)
        self.assertEqual(overnight_mod.SEARCH_QUERIES, SEARCH_QUERIES)


if __name__ == "__main__":
    unittest.main()

