import unittest
from src.gui.gui import JobAppWindow


class TestGUISearch(unittest.TestCase):
    def setUp(self):
        class DummyApp:
            pass

        self.app = DummyApp()
        self.app._job_matches_search = JobAppWindow._job_matches_search.__get__(self.app, DummyApp)

    def test_search_intern_keyword(self):
        job = {
            "title": "Software Engineering Intern",
            "company": "Stripe",
            "ats_platform": "greenhouse",
            "reason": "",
            "details": {"role_type": "Internship"}
        }
        self.assertTrue(self.app._job_matches_search(job, "https://boards.greenhouse.io/stripe/jobs/123", "intern"))
        self.assertTrue(self.app._job_matches_search(job, "https://boards.greenhouse.io/stripe/jobs/123", "internship"))
        self.assertTrue(self.app._job_matches_search(job, "https://boards.greenhouse.io/stripe/jobs/123", "stripe"))

    def test_search_space_separated_and(self):
        job = {
            "title": "Full Stack Engineer Intern",
            "company": "Datadog",
            "ats_platform": "lever",
            "reason": "",
            "details": "Python, React"
        }
        self.assertTrue(self.app._job_matches_search(job, "https://jobs.lever.co/datadog/1", "full stack"))
        self.assertTrue(self.app._job_matches_search(job, "https://jobs.lever.co/datadog/1", "python intern"))
        self.assertFalse(self.app._job_matches_search(job, "https://jobs.lever.co/datadog/1", "golang intern"))

    def test_search_comma_separated_or(self):
        job = {
            "title": "Junior Backend Developer",
            "company": "Shopify",
            "ats_platform": "smartrecruiters",
            "reason": "",
            "details": ""
        }
        self.assertTrue(self.app._job_matches_search(job, "https://job.url", "intern, junior, apprentice"))
        self.assertFalse(self.app._job_matches_search(job, "https://job.url", "intern, apprentice"))

    def test_search_case_insensitivity(self):
        job = {
            "title": "AI RESEARCH INTERN",
            "company": "OpenAI",
            "ats_platform": "greenhouse",
            "reason": "",
            "details": None
        }
        self.assertTrue(self.app._job_matches_search(job, "https://openai.com", "ai research"))
        self.assertTrue(self.app._job_matches_search(job, "https://openai.com", "INTERN"))

    def test_empty_search_matches_all(self):
        job = {"title": "Any Job", "company": "Any Co"}
        self.assertTrue(self.app._job_matches_search(job, "https://any.com", ""))
        self.assertTrue(self.app._job_matches_search(job, "https://any.com", "   "))


if __name__ == "__main__":
    unittest.main()
