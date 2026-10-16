"""
Unit tests for deterministic job filtering (US location, Senior role, Non-tech role).
Ensures zero false positives on genuine tech roles (like Bitdeer AI infrastructure).
"""
import unittest
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.core.job_filter import (
    is_us_based, is_senior_or_lead, is_non_tech,
    evaluate_deterministic_ignore
)


class TestJobFilter(unittest.TestCase):
    def test_us_location(self):
        us_locs = ["Seattle, WA", "New York, NY", "San Francisco, CA", "Austin, Texas", "United States"]
        for loc in us_locs:
            ignored, reason = is_us_based(location=loc)
            self.assertTrue(ignored, f"Expected US ignore for {loc}")

        non_us_locs = ["London, UK", "Berlin, Germany", "Bangalore, India", "Toronto, Canada", "Norway"]
        for loc in non_us_locs:
            ignored, _ = is_us_based(location=loc)
            self.assertFalse(ignored, f"Unexpected US ignore for {loc}")

    def test_senior_roles(self):
        senior_titles = ["Senior SDE", "Lead Engineer", "Staff Software Engineer", "Principal Architect", "SDE II", "SDE 3"]
        for title in senior_titles:
            ignored, reason = is_senior_or_lead(title)
            self.assertTrue(ignored, f"Expected senior ignore for {title}")

        junior_titles = ["Software Engineering Intern", "Junior Developer", "Entry Level SDE", "AI Infrastructure Engineer"]
        for title in junior_titles:
            ignored, _ = is_senior_or_lead(title)
            self.assertFalse(ignored, f"Unexpected senior ignore for {title}")

    def test_non_tech_roles(self):
        non_tech = ["Sales Representative", "HR Manager", "Recruiter", "Account Executive"]
        for title in non_tech:
            ignored, reason = is_non_tech(title)
            self.assertTrue(ignored, f"Expected non-tech ignore for {title}")

    def test_bitdeer_tech_role_preservation(self):
        # Must NOT flag AI infrastructure engineer as non-tech because of mining/hardware company boilerplate
        title = "AI infrastructure Engineer"
        jd = "Bitdeer is a world-leading cryptocurrency mining and high-performance computing provider."
        ignored, reason = is_non_tech(title, jd_text=jd)
        self.assertFalse(ignored, f"Bitdeer role falsely ignored: {reason}")


if __name__ == "__main__":
    unittest.main()
