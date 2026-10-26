"""
Unit tests for core database functionality and persistent tombstone tables.
"""
import unittest
import sqlite3
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from src.core.db import (
        init_db, insert_or_ignore_job, save_company,
        get_all_companies, get_company_stats, delete_company,
        delete_jobs_by_rank, get_all_urls
    )
except ImportError:
    from core.db import (
        init_db, insert_or_ignore_job, save_company,
        get_all_companies, get_company_stats, delete_company,
        delete_jobs_by_rank, get_all_urls
    )


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_company_lifecycle(self):
        added = save_company("Stripe", "greenhouse", "stripe", "https://boards.greenhouse.io/stripe", conn=self.conn)
        self.assertTrue(added)

        dup = save_company("Stripe Inc", "greenhouse", "stripe", "https://boards.greenhouse.io/stripe", conn=self.conn)
        self.assertFalse(dup)

        comps = get_all_companies(conn=self.conn)
        self.assertEqual(len(comps), 1)
        self.assertEqual(comps[0]["name"], "Stripe Inc")

        stats = get_company_stats(conn=self.conn)
        self.assertEqual(stats["total_companies"], 1)

        deleted = delete_company(comps[0]["id"], conn=self.conn)
        self.assertEqual(deleted, 1)
        self.assertEqual(len(get_all_companies(conn=self.conn)), 0)

    def test_job_tombstone_archiving(self):
        url = "https://boards.greenhouse.io/stripe/jobs/101"
        inserted = insert_or_ignore_job(
            url=url,
            title="Software Engineer Intern",
            status="New",
            rank="UNKNOWN",
            conn=self.conn
        )
        self.assertTrue(inserted)
        self.assertIn(url, get_all_urls(conn=self.conn))

        self.conn.execute("UPDATE jobs SET rank='IGNORE' WHERE url=?", (url,))
        self.conn.commit()
        deleted = delete_jobs_by_rank("IGNORE", conn=self.conn)
        self.assertEqual(deleted, 1)

        row = self.conn.execute("SELECT * FROM jobs WHERE url=?", (url,)).fetchone()
        self.assertIsNone(row)

        tombstone = self.conn.execute("SELECT * FROM deleted_urls WHERE url=?", (url,)).fetchone()
        self.assertIsNotNone(tombstone)
        self.assertIn(url, get_all_urls(conn=self.conn))

    def test_scraper_checkpoints(self):
        from src.core.db import save_scraper_checkpoint, get_scraper_checkpoint, clear_scraper_checkpoint
        save_scraper_checkpoint("test_bot", "bamboohr.com", "Software", 30, conn=self.conn)
        cp = get_scraper_checkpoint("test_bot", conn=self.conn)
        self.assertIsNotNone(cp)
        self.assertEqual(cp["site"], "bamboohr.com")
        self.assertEqual(cp["query_term"], "Software")
        self.assertEqual(cp["page_start"], 30)

        clear_scraper_checkpoint("test_bot", conn=self.conn)
        self.assertIsNone(get_scraper_checkpoint("test_bot", conn=self.conn))

    def test_reset_failed_extraction_jobs(self):
        from src.ranking.rank_internships import reset_failed_extraction_jobs
        self.conn.execute("INSERT INTO jobs (url, title, status, rank, reason) VALUES (?, ?, ?, ?, ?)",
                          ("https://example.com/job1", "Intern", "NA", "ERROR", "Failed to extract meaningful text."))
        self.conn.execute("INSERT INTO jobs (url, title, status, rank, reason) VALUES (?, ?, ?, ?, ?)",
                          ("https://example.com/job2", "Dev", "NA", "ERROR", "Timeout while processing job page."))
        self.conn.execute("INSERT INTO jobs (url, title, status, rank, reason) VALUES (?, ?, ?, ?, ?)",
                          ("https://example.com/job3", "Senior", "NA", "IGNORE", "[Deep JD Filter] Senior"))
        self.conn.commit()

        reset_count = reset_failed_extraction_jobs(conn=self.conn)
        self.assertEqual(reset_count, 2)

        row1 = self.conn.execute("SELECT status, rank, reason FROM jobs WHERE url='https://example.com/job1'").fetchone()
        self.assertEqual(row1["status"], "New")
        self.assertEqual(row1["rank"], "UNKNOWN")
        self.assertEqual(row1["reason"], "")

        row3 = self.conn.execute("SELECT status, rank, reason FROM jobs WHERE url='https://example.com/job3'").fetchone()
        self.assertEqual(row3["status"], "NA")
        self.assertEqual(row3["rank"], "IGNORE")


if __name__ == "__main__":
    unittest.main()

