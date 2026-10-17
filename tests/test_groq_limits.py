"""
Unit tests for Groq limits and quota checking module.
"""
import unittest
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.ranking.groq_limits import get_all_groq_keys, check_all_groq_limits


class TestGroqLimits(unittest.TestCase):
    def test_get_all_groq_keys(self):
        keys = get_all_groq_keys()
        self.assertIsInstance(keys, list)
        self.assertGreater(len(keys), 0, "Expected at least 1 Groq API key in .env")
        for k in keys:
            self.assertTrue(k.startswith("gsk_"), f"Groq key should start with gsk_: {k[:8]}")

    def test_check_limits_structure(self):
        # Check limits data format
        data = check_all_groq_limits()
        self.assertIn("total_keys", data)
        self.assertIn("keys", data)
        self.assertIn("total_remaining_requests", data)
        self.assertIn("total_limit_requests", data)
        self.assertIn("estimated_jobs_today", data)
        self.assertIn("daily_reset_in", data)
        self.assertGreater(data["total_keys"], 0)
        self.assertGreater(data["total_limit_requests"], 0)


if __name__ == "__main__":
    unittest.main()
