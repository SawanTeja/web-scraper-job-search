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
    from src.scrapers.ats_parser import parse_ats_info, clean_company_name_from_title, build_google_dork
except ImportError:
    from scrapers.ats_parser import parse_ats_info, clean_company_name_from_title, build_google_dork


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
            ("https://auracloud.keka.com/careers/jobdetails/144210", "keka", "auracloud"),
            ("https://pramana.keka.com/careers", "keka", "pramana"),
            ("https://personio.jobs.personio.de/job/12345", "personio", "personio"),
            ("https://spotify.teamtailor.com/jobs/54321", "teamtailor", "spotify"),
            ("https://ats.rippling.com/superhuman/jobs", "rippling", "superhuman"),
            ("https://acme.pinpointhq.com/en/jobs/999", "pinpoint", "acme"),
            ("https://cloudtech.applytojob.com/apply/abc123xyz", "jazzhr", "cloudtech"),
            ("https://www.comeet.com/jobs/fiverr/456", "comeet", "fiverr"),
            ("https://emp.jobylon.com/companies/klarna/", "jobylon", "klarna"),
            ("https://boeing.taleo.net/careersection/2/jobsearch.ftl", "taleo", "boeing|2"),
            ("https://toyota.csod.com/ux/ats/careersite/1/home", "csod", "toyota|1"),
            ("https://tcs.zohorecruit.com/jobs/Careers", "zohorecruit", "tcs"),
            ("https://freshworks.freshteam.com/jobs/dev-123", "freshteam", "freshworks"),
            ("https://micron.eightfold.ai/careers", "eightfold", "micron"),
            ("https://paypal.eightfold.ai/careers/job/274922336686", "eightfold", "paypal"),
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

    def test_build_google_dork(self):
        # 1. Clean query keyword
        dork1 = build_google_dork("boards.greenhouse.io", "Software")
        self.assertEqual(dork1, 'site:boards.greenhouse.io Software')

        # 2. Already quoted keyword preserved cleanly
        dork2 = build_google_dork("boards.greenhouse.io", '"Software"')
        self.assertEqual(dork2, 'site:boards.greenhouse.io "Software"')

        # 3. Boolean OR grouped in parentheses without repeating site:
        dork3 = build_google_dork("boards.greenhouse.io", "C++ OR Java")
        self.assertEqual(dork3, 'site:boards.greenhouse.io (C++ OR Java)')

        # 4. Multi-term OR with existing quotes
        dork4 = build_google_dork("lever.co", '"Javascript" OR "React" OR "Node.js"')
        self.assertEqual(dork4, 'site:lever.co ("Javascript" OR "React" OR "Node.js")')

        # 5. Site with path or scheme is sanitized
        dork5 = build_google_dork("https://keka.com/careers", "Software")
        self.assertEqual(dork5, 'site:keka.com Software')


if __name__ == "__main__":
    unittest.main()
