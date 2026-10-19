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

        # Multi-location list input handling (regression test for AttributeError: 'list' object has no attribute 'strip')
        t_list_valid = categorize_job_tier("Software Engineer", ["Lisbon, Portugal", "Madrid, Spain"])
        self.assertEqual(t_list_valid["tier"], 2)
        self.assertEqual(t_list_valid["rank"], "UNKNOWN")

        t_list_us = categorize_job_tier("Software Engineer", ["Chicago, IL", "Remote"])
        self.assertEqual(t_list_us["tier"], 3)
        self.assertEqual(t_list_us["rank"], "IGNORE")

        # Dict location input handling
        t_dict = categorize_job_tier("Junior Frontend Developer", {"city": "Munich", "country": "Germany"})
        self.assertEqual(t_dict["tier"], 1)
        self.assertEqual(t_dict["rank"], "UNKNOWN")

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

    def test_experience_exceeded(self):
        from src.ranking.job_filter import is_experience_exceeded
        # Roles requiring 1 or more years must be filtered out
        disqualify_cases = [
            "1+ year", "1+ years", "1-2 years", "1 - 3 years", "2+ years",
            "3+ years", "5 years", "Minimum 1 year", "at least 1 year",
            "1 year of experience", "1 year", "1 yr", "Requires 1 year", "2 years"
        ]
        for exp in disqualify_cases:
            exceeded, reason = is_experience_exceeded(exp)
            self.assertTrue(exceeded, f"Expected experience exceeded for: '{exp}'")

        # Roles for freshers, 0-experience, 0-1 year, or entry-level must NOT be filtered out
        allow_cases = [
            "0-1 year", "0-1 years", "0 to 1 year", "None", "null", "no", "0 years",
            "Fresher", "fresh graduate", "No experience required", "Internship", "Student"
        ]
        for exp in allow_cases:
            exceeded, reason = is_experience_exceeded(exp)
            self.assertFalse(exceeded, f"Unexpected experience exceeded for: '{exp}' (reason: {reason})")

    def test_evaluate_deep_jd_filter(self):
        from src.ranking.job_filter import evaluate_deep_jd_filter

        # 1. Experience >= 1 year in details
        drop, reason = evaluate_deep_jd_filter("Software Engineer", details={"experience_required": "1+ year"})
        self.assertTrue(drop)
        self.assertIn("1+ year", reason)

        drop, reason = evaluate_deep_jd_filter("Junior Developer", details={"experience_required": "1-3 years"})
        self.assertTrue(drop)
        self.assertIn("1-3 years", reason)

        # 2. Experience >= 1 year in raw JD text
        drop, reason = evaluate_deep_jd_filter("Software Engineer", jd_text="Requirements: Must have at least 2+ years of experience in React.")
        self.assertTrue(drop)
        self.assertIn("experience", reason.lower())

        # 3. Location in details: US or Canada
        drop, reason = evaluate_deep_jd_filter("Software Engineer", details={"location": "Toronto, ON, Canada"})
        self.assertTrue(drop)
        self.assertIn("Canad", reason)

        drop, reason = evaluate_deep_jd_filter("Software Engineer", details={"location": "Austin, Texas, United States"})
        self.assertTrue(drop)
        self.assertIn("US", reason)

        # 4. Security clearance in JD
        drop, reason = evaluate_deep_jd_filter("Software Engineer", jd_text="Active Secret Security Clearance required for this role.")
        self.assertTrue(drop)
        self.assertIn("clearance", reason.lower())

        # 5. Clean candidates: must pass!
        drop, reason = evaluate_deep_jd_filter("Software Engineering Intern", details={"experience_required": "0-1 year", "location": "Bangalore, India"})
        self.assertFalse(drop, f"Clean candidate was unexpectedly dropped: {reason}")

        drop, reason = evaluate_deep_jd_filter("Junior Developer", details={"experience_required": "None", "location": "Berlin, Germany"})
        self.assertFalse(drop, f"Clean candidate was unexpectedly dropped: {reason}")

    def test_has_structured_jd(self):
        from src.ranking.rank_internships import has_structured_jd

        # Google search scraped minimal location metadata (NOT extracted yet)
        self.assertFalse(has_structured_jd(None))
        self.assertFalse(has_structured_jd({}))
        self.assertFalse(has_structured_jd({"location": "Chennai, India"}))
        self.assertFalse(has_structured_jd('{"location": "London, UK"}'))

        # Fully extracted schema from Groq LLM
        full_details = {
            "job_name": "Junior SDE",
            "company": "Acme",
            "location": "Berlin",
            "experience_required": "0-1 year",
            "skills_required": ["Python", "C++"]
        }
        self.assertTrue(has_structured_jd(full_details))
        import json
        self.assertTrue(has_structured_jd(json.dumps(full_details)))

    def test_filter_never_touches_na_or_applied_jobs(self):
        import sqlite3
        from src.core.db import init_db
        from src.ranking.job_filter import apply_deterministic_filter_to_db, apply_deep_jd_filter_to_db

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_db(conn)

        # Insert jobs: one New, one NA with rank UNKNOWN, one Applied with rank UNKNOWN
        conn.execute("INSERT INTO jobs (url, title, status, rank) VALUES ('https://test.com/new', 'Software Engineer Intern', 'New', 'UNKNOWN')")
        conn.execute("INSERT INTO jobs (url, title, status, rank) VALUES ('https://test.com/na', 'Software Engineer', 'NA', 'UNKNOWN')")
        conn.execute("INSERT INTO jobs (url, title, status, rank) VALUES ('https://test.com/applied', 'Junior Developer', 'Applied', 'UNKNOWN')")
        conn.commit()

        # Run Smart Tier filter
        stats = apply_deterministic_filter_to_db(conn=conn, sync_locations=False)
        self.assertEqual(stats["scanned"], 1)  # Only the 'New' job scanned!

        # Verify NA and Applied jobs were never altered
        na_row = conn.execute("SELECT status, rank FROM jobs WHERE url='https://test.com/na'").fetchone()
        self.assertEqual(na_row["status"], "NA")
        self.assertEqual(na_row["rank"], "UNKNOWN")

        app_row = conn.execute("SELECT status, rank FROM jobs WHERE url='https://test.com/applied'").fetchone()
        self.assertEqual(app_row["status"], "Applied")
        self.assertEqual(app_row["rank"], "UNKNOWN")

        # Run Deep JD filter
        deep_stats = apply_deep_jd_filter_to_db(conn=conn)
        self.assertEqual(deep_stats["scanned"], 1)  # Only the 'New' job scanned!


if __name__ == "__main__":
    unittest.main()
