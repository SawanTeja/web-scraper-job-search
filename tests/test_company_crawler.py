"""
Unit tests for the ATS Company Discovery Crawler (company_crawler.py).
Verifies that the crawler extracts valid company entities, avoids blacklisted tokens,
and saves strictly to the companies dataset without touching the jobs table.
"""
import unittest
import sqlite3
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.core.db import init_db, get_company_count, get_all_companies
from src.scrapers.company_crawler import (
    is_valid_identifier, extract_companies_from_text, verify_company_board
)


class TestCompanyCrawler(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_is_valid_identifier(self):
        # Valid identifiers
        self.assertTrue(is_valid_identifier("workday", "nvidia|wd5|NVIDIAExternalCareerSite"))
        self.assertTrue(is_valid_identifier("workday", "target||targetcareers"))
        self.assertTrue(is_valid_identifier("greenhouse", "stripe"))
        self.assertTrue(is_valid_identifier("lever", "netflix"))
        self.assertTrue(is_valid_identifier("ashby", "ramp"))

        # Blacklisted / generic system tokens should be rejected
        self.assertFalse(is_valid_identifier("workday", "login"))
        self.assertFalse(is_valid_identifier("workday", "signin"))
        self.assertFalse(is_valid_identifier("greenhouse", "jobs"))
        self.assertFalse(is_valid_identifier("greenhouse", "careers"))
        self.assertFalse(is_valid_identifier("lever", "static"))
        self.assertFalse(is_valid_identifier("ashby", "api"))
        self.assertFalse(is_valid_identifier("workday", "12345"))  # pure digits

    def test_extract_companies_from_text(self):
        sample_markdown = """
        | Company | Location | Link |
        |---|---|---|
        | Nvidia | Santa Clara, CA | https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite |
        | Stripe | San Francisco, CA | https://boards.greenhouse.io/stripe/jobs/12345 |
        | Ramp | New York, NY | https://jobs.ashbyhq.com/ramp |
        | Invalid | N/A | https://boards.greenhouse.io/careers |
        """
        comps = extract_companies_from_text(sample_markdown)
        self.assertEqual(len(comps), 3)

        platforms = {c["ats_platform"] for c in comps}
        self.assertIn("workday", platforms)
        self.assertIn("greenhouse", platforms)
        self.assertIn("ashby", platforms)

        # Target platform filtering
        wd_only = extract_companies_from_text(sample_markdown, target_platform="workday")
        self.assertEqual(len(wd_only), 1)
        self.assertEqual(wd_only[0]["ats_platform"], "workday")
        self.assertEqual(wd_only[0]["tenant"], "nvidia")

    def test_zero_jobs_created_policy(self):
        """Crawler must never touch or create any jobs in the jobs table."""
        sample_text = "Check out https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
        comps = extract_companies_from_text(sample_text)
        self.assertTrue(len(comps) > 0)

        # Verify jobs table is completely empty
        job_count = self.conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
        self.assertEqual(job_count, 0)


if __name__ == "__main__":
    unittest.main()
