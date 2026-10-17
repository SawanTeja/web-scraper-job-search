"""
Unit tests for deterministic job filtering and 3-Tier Categorization:
  - Tier 1: Early Career, Internships, Junior, Apprentice, Graduate, Associate SWE.
  - Tier 2: General Software / Tech Engineering (Entry-level / general SWE).
  - Tier 3: Deterministic IGNORE (Senior, Lead, Supervisor, Pleno, Non-Tech, Helpdesk, Career Hub, US Location).
Ensures zero false positives on genuine tech roles (like Bitdeer AI infrastructure, Bangalore IN, Internal tools).
"""
import unittest
import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from src.ranking.job_filter import (
        is_us_based, is_canada_based, is_senior_or_lead, is_non_tech, is_career_hub, is_early_career,
        categorize_job_tier, evaluate_deterministic_ignore
    )
    from src.ranking.rank_internships import is_already_ai_ranked
except ImportError:
    from ranking.job_filter import (
        is_us_based, is_canada_based, is_senior_or_lead, is_non_tech, is_career_hub, is_early_career,
        categorize_job_tier, evaluate_deterministic_ignore
    )
    from ranking.rank_internships import is_already_ai_ranked


class TestJobFilter(unittest.TestCase):
    def test_us_location(self):
        us_locs = ["Seattle, WA", "New York, NY", "San Francisco, CA", "Austin, Texas", "United States"]
        for loc in us_locs:
            ignored, reason = is_us_based(location=loc)
            self.assertTrue(ignored, f"Expected US ignore for {loc}")

        # Explicit non-US locations, including India (IN) and Canada (CA) ISO codes
        non_us_locs = [
            "London, UK", "Berlin, Germany", "Bangalore, India", "Toronto, Canada",
            "Norway", "Bangalore, IN", "Noida, IN", "Vadodara, India", "Toronto, CA"
        ]
        for loc in non_us_locs:
            ignored, reason = is_us_based(location=loc)
            self.assertFalse(ignored, f"Unexpected US ignore for {loc}: {reason}")

    def test_senior_roles(self):
        senior_titles = [
            "Senior SDE", "Lead Engineer", "Staff Software Engineer", "Principal Architect",
            "SDE II", "SDE 3", "Supervisor, Integration Subassemblies", "Integration Developer Pleno",
            "Developer SSR", "Software Engineer (3+ years)"
        ]
        for title in senior_titles:
            ignored, reason = is_senior_or_lead(title)
            self.assertTrue(ignored, f"Expected senior ignore for {title}")

        junior_titles = ["Software Engineering Intern", "Junior Developer", "Entry Level SDE", "AI Infrastructure Engineer"]
        for title in junior_titles:
            ignored, _ = is_senior_or_lead(title)
            self.assertFalse(ignored, f"Unexpected senior ignore for {title}")

    def test_non_tech_roles(self):
        non_tech = [
            "Sales Representative", "HR Manager", "Recruiter", "Account Executive",
            "Représentant Support Helpdesk", "Helpdesk Support Specialist", "Assembly Operator",
            "Caissier / Caissière", "Gestionnaire, Comptes fournisseurs", "Building Maintenance Mechanic",
            "Sewage Treatment Plant Operator", "OSAT Sourcing Buyer", "Quotation Specialist"
        ]
        for title in non_tech:
            ignored, reason = is_non_tech(title)
            self.assertTrue(ignored, f"Expected non-tech ignore for {title}")

    def test_canada_location(self):
        can_locs = [
            "Toronto, Canada", "Vancouver, BC", "Montreal, QC", "Calgary, Alberta",
            "Ottawa, ON", "Waterloo, ON, Canada", "Remote - Canada"
        ]
        for loc in can_locs:
            ignored, reason = is_canada_based(location=loc)
            self.assertTrue(ignored, f"Expected Canada ignore for {loc}")

        # Explicit non-Canada locations should NOT be ignored
        non_can_locs = [
            "London, UK", "Berlin, Germany", "Bangalore, India", "Melbourne, Victoria, Australia",
            "Paris, France", "Madrid, Spain"
        ]
        for loc in non_can_locs:
            ignored, reason = is_canada_based(location=loc)
            self.assertFalse(ignored, f"Unexpected Canada ignore for {loc}: {reason}")

    def test_bitdeer_tech_role_preservation(self):
        # Must NOT flag AI infrastructure engineer as non-tech because of mining/hardware company boilerplate
        title = "AI infrastructure Engineer"
        jd = "Bitdeer is a world-leading cryptocurrency mining and high-performance computing provider."
        ignored, reason = is_non_tech(title, jd_text=jd)
        self.assertFalse(ignored, f"Bitdeer role falsely ignored: {reason}")

    def test_career_hub_detection(self):
        hubs = ["Duolingo Careers", "Marriott Careers", "Careers at GitLab", "Join our team", "Open Positions"]
        for title in hubs:
            is_hub, reason = is_career_hub(title)
            self.assertTrue(is_hub, f"Expected career hub for '{title}'")

        real_jobs = ["Backend Engineer - Jobs", "Software Developer", "Junior SDE", "Associate Software Engineer"]
        for title in real_jobs:
            is_hub, _ = is_career_hub(title)
            self.assertFalse(is_hub, f"Unexpected career hub for '{title}'")

    def test_early_career_detection(self):
        early = [
            "Software Engineering Intern", "Junior Software Engineer", "Software Developer Intern @ Visier",
            "Associate Software Engineer", "2025 Graduate Developer", "Apprentice Software Engineer",
            "Werkstudent Software Development"
        ]
        for title in early:
            is_ec, reason = is_early_career(title)
            self.assertTrue(is_ec, f"Expected early career for '{title}'")

        # Word-boundary check: 'internal' must NOT match 'intern'
        non_early = ["Software Engineer - Internal Tools", "Backend Java Developer", "Systems Engineer"]
        for title in non_early:
            is_ec, _ = is_early_career(title)
            self.assertFalse(is_ec, f"Unexpected early career match for '{title}'")

    def test_categorize_job_tier(self):
        # Tier 1: Early Career (Internal Priority 1, UNKNOWN rank until AI evaluates it)
        t1 = categorize_job_tier("Junior Software Engineer", "Bangalore, India")
        self.assertEqual(t1["tier"], 1)
        self.assertEqual(t1["rank"], "UNKNOWN")
        self.assertEqual(t1["status"], "New")

        t1_intern = categorize_job_tier("Software Developer Intern @ Visier", "Berlin, Germany")
        self.assertEqual(t1_intern["tier"], 1)
        self.assertEqual(t1_intern["rank"], "UNKNOWN")

        # Tier 2: General Tech / SWE (Clean tech role for AI evaluation)
        t2 = categorize_job_tier("Java Backend Developer - Vadodara", "Vadodara, India")
        self.assertEqual(t2["tier"], 2)
        self.assertEqual(t2["rank"], "UNKNOWN")
        self.assertEqual(t2["status"], "New")

        # Tier 3: Senior, Supervisor, Pleno
        t3_sr = categorize_job_tier("Supervisor, Integration Subassemblies", "London, UK")
        self.assertEqual(t3_sr["tier"], 3)
        self.assertEqual(t3_sr["rank"], "IGNORE")
        self.assertEqual(t3_sr["status"], "NA")

        t3_pleno = categorize_job_tier("Integration Developer Pleno", "Remote")
        self.assertEqual(t3_pleno["tier"], 3)
        self.assertEqual(t3_pleno["rank"], "IGNORE")

        # Tier 3: Helpdesk / Non-tech
        t3_helpdesk = categorize_job_tier("Représentant Support Helpdesk", "Montreal, Canada")
        self.assertEqual(t3_helpdesk["tier"], 3)
        self.assertEqual(t3_helpdesk["rank"], "IGNORE")

        # Tier 3: Non-tech Intern (Marketing Intern must be filtered out!)
        t3_mkt_intern = categorize_job_tier("Marketing Intern", "Bangalore, India")
        self.assertEqual(t3_mkt_intern["tier"], 3)
        self.assertEqual(t3_mkt_intern["rank"], "IGNORE")

        # Tier 3: Career Hub
        t3_hub = categorize_job_tier("Duolingo Careers", "")
        self.assertEqual(t3_hub["tier"], 3)
        self.assertEqual(t3_hub["rank"], "IGNORE")

        # Tier 3: US Location
        t3_us = categorize_job_tier("Software Engineer", "Seattle, WA")
        self.assertEqual(t3_us["tier"], 3)
        self.assertEqual(t3_us["rank"], "IGNORE")

    def test_ai_ranked_preservation(self):
        # Jobs marked by AI must NEVER be re-ranked by AI
        self.assertTrue(is_already_ai_ranked("[AI Verified] Excellent backend match"))
        self.assertTrue(is_already_ai_ranked("[AI-Evaluated] High priority Python intern"))
        self.assertTrue(is_already_ai_ranked("AI: Software Engineering match"))
        self.assertTrue(is_already_ai_ranked(details={"ai_evaluated": True}))

        # Unranked and deterministic jobs must be eligible for AI evaluation
        self.assertFalse(is_already_ai_ranked("[Tier 1: Early Career] Early-career keyword: 'Intern'"))
        self.assertFalse(is_already_ai_ranked("[Tier 2: Tech / SWE Role]"))
        self.assertFalse(is_already_ai_ranked("Reset for re-evaluation"))
        self.assertFalse(is_already_ai_ranked(""))


if __name__ == "__main__":
    unittest.main()
