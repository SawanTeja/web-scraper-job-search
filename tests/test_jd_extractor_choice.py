import unittest
from unittest.mock import patch, AsyncMock, MagicMock
import os
import sys
import asyncio

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.ranking.rank_internships import (
    extract_job_details_with_llm,
    extract_job_details_with_groq
)


class TestJdExtractorChoice(unittest.TestCase):
    @patch("src.ranking.rank_internships.extract_job_details_with_groq")
    @patch("src.ranking.rank_internships.gemini_manager")
    def test_extractor_groq_mode_bypasses_gemini(self, mock_gemini, mock_groq):
        """When extractor='groq', Gemini must be completely bypassed."""
        mock_gemini.is_available.return_value = True
        mock_gemini.extract_job_details = AsyncMock(return_value={"job_name": "Gemini Job"})
        mock_groq.return_value = {"job_name": "Groq Job"}

        res = asyncio.run(extract_job_details_with_llm("Software Intern", "Description", extractor="groq"))
        self.assertEqual(res, {"job_name": "Groq Job"})
        mock_gemini.extract_job_details.assert_not_called()
        mock_groq.assert_called_once_with("Software Intern", "Description")

    @patch("src.ranking.rank_internships.extract_job_details_with_groq")
    @patch("src.ranking.rank_internships.gemini_manager")
    def test_extractor_gemini_mode_no_fallback(self, mock_gemini, mock_groq):
        """When extractor='gemini', if Gemini fails/exhausted, it must NOT fall back to Groq."""
        mock_gemini.is_available.return_value = True
        mock_gemini.extract_job_details = AsyncMock(return_value=None)
        mock_groq.return_value = {"job_name": "Groq Job"}

        res = asyncio.run(extract_job_details_with_llm("Software Intern", "Description", extractor="gemini"))
        self.assertIsNone(res)
        mock_gemini.extract_job_details.assert_called_once()
        mock_groq.assert_not_called()

    @patch("src.ranking.rank_internships.extract_job_details_with_groq")
    @patch("src.ranking.rank_internships.gemini_manager")
    def test_extractor_auto_mode_falls_back_to_groq(self, mock_gemini, mock_groq):
        """When extractor='auto', if Gemini fails, it must fall back to Groq."""
        mock_gemini.is_available.return_value = True
        mock_gemini.extract_job_details = AsyncMock(return_value=None)
        mock_groq.return_value = {"job_name": "Fallback Groq Job"}

        res = asyncio.run(extract_job_details_with_llm("Software Intern", "Description", extractor="auto"))
        self.assertEqual(res, {"job_name": "Fallback Groq Job"})
        mock_gemini.extract_job_details.assert_called_once()
        mock_groq.assert_called_once_with("Software Intern", "Description")

    @patch.dict(os.environ, {"SCRAPER_EXTRACTOR": "groq"})
    @patch("src.ranking.rank_internships.extract_job_details_with_groq")
    @patch("src.ranking.rank_internships.gemini_manager")
    def test_extractor_env_var_override(self, mock_gemini, mock_groq):
        """SCRAPER_EXTRACTOR environment variable must control extraction mode when not explicitly passed."""
        mock_gemini.is_available.return_value = True
        mock_gemini.extract_job_details = AsyncMock(return_value={"job_name": "Gemini Job"})
        mock_groq.return_value = {"job_name": "Env Groq Job"}

        res = asyncio.run(extract_job_details_with_llm("Software Intern", "Description"))
        self.assertEqual(res, {"job_name": "Env Groq Job"})
        mock_gemini.extract_job_details.assert_not_called()
        mock_groq.assert_called_once()


if __name__ == "__main__":
    unittest.main()

