"""
Unit tests for deterministic ATS platform detection and career board URL parsing.
"""
import unittest
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from src.scrapers.ats_parser import parse_ats_info, clean_company_name_from_title
except ImportError:
    from scrapers.ats_parser import parse_ats_info, clean_company_name_from_title


class TestATSParser(unittest.TestCase):
    def test_platforms(self):
        cases = [
            ("https://boards.greenhouse.io/figma", "greenhouse", "figma"),
            ("https://jobs.lever.co/netflix", "lever", "netflix"),
            ("https://jobs.ashbyhq.com/openai", "ashby", "openai"),
            ("https://jobs.smartrecruiters.com/Square", "smartrecruiters", "square"),
            ("https://apply.workable.com/revolut/j/123", "workable", "revolut"),
            ("https://team.bamboohr.com/careers/55", "bamboohr", "team"),
            ("https://demo.breezy.hr/p/123", "breezy", "demo"),
            ("https://jobs.jobvite.com/hulu/job/123", "jobvite", "hulu"),
            ("https://internal-vtgdefense.icims.com/jobs/9316/job", "icims", "internal-vtgdefense"),
            ("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210799717", "oraclecloud", "jpmc||cx_1001"),
        ]
        for url, plat, ident in cases:
            res = parse_ats_info(url)
            self.assertIsNotNone(res, f"Failed on {url}")
            self.assertEqual(res["ats_platform"], plat)
            self.assertEqual(res["identifier"].lower(), ident.lower())

    def test_non_ats(self):
        self.assertIsNone(parse_ats_info("https://google.com/search?q=test"))

    def test_title_clean(self):
        cleaned = clean_company_name_from_title("Software Engineer Intern at Stripe - Remote")
        self.assertIn("Stripe", cleaned)


if __name__ == "__main__":
    unittest.main()
