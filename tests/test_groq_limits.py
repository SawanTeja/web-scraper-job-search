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

    def test_fallback_triggers_only_when_all_keys_daily_exhausted(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.ranking.rank_internships import GroqManager
        import groq

        with patch("asyncio.sleep", return_value=None):
            mgr = GroqManager()
            mock_c1 = MagicMock()
            mock_c2 = MagicMock()
            mock_c3 = MagicMock()
            mgr.clients = [mock_c1, mock_c2, mock_c3]
            mgr.total_keys = 3
            mgr.daily_exhausted_keys = set()
            mgr.active_model = mgr.primary_model

            tpm_error = groq.RateLimitError(
                "Rate limit reached for model on tokens per minute (TPM): Limit 8000, Used 8000. Please try again in 4s.",
                response=MagicMock(headers={}),
                body={}
            )
            daily_error = groq.RateLimitError(
                "Rate limit reached for model on tokens per day (TPD): Limit 200000, Used 200000. Please try again in 4h.",
                response=MagicMock(headers={}),
                body={}
            )
            success_resp = MagicMock()

            # 1. TPM error on Key 1 -> Key 2 succeeds -> Must NOT mark daily, must NOT switch model
            mock_c1.chat.completions.create = AsyncMock(side_effect=tpm_error)
            mock_c2.chat.completions.create = AsyncMock(return_value=success_resp)
            mgr.current_idx = 0
            resp = asyncio.run(mgr.chat_completion(messages=[{"role": "user", "content": "test"}]))
            self.assertEqual(resp, success_resp)
            self.assertEqual(mgr.active_model, "qwen/qwen3.8-27b")
            self.assertEqual(len(mgr.daily_exhausted_keys), 0, "TPM must never mark key as daily exhausted")

            # 2. Key 1 hits daily limit -> Key 2 succeeds -> Must NOT switch model
            mock_c1.chat.completions.create = AsyncMock(side_effect=daily_error)
            mgr.current_idx = 0
            resp = asyncio.run(mgr.chat_completion(messages=[{"role": "user", "content": "test"}]))
            self.assertEqual(resp, success_resp)
            self.assertIn(1, mgr.daily_exhausted_keys)
            self.assertEqual(mgr.active_model, "qwen/qwen3.8-27b", "Must NOT switch model if only 1 key exhausted daily")

            # 3. Key 2 also hits daily limit -> Key 3 succeeds -> Must NOT switch model
            mock_c2.chat.completions.create = AsyncMock(side_effect=daily_error)
            mock_c3.chat.completions.create = AsyncMock(return_value=success_resp)
            mgr.current_idx = 0
            resp = asyncio.run(mgr.chat_completion(messages=[{"role": "user", "content": "test"}]))
            self.assertEqual(resp, success_resp)
            self.assertIn(2, mgr.daily_exhausted_keys)
            self.assertEqual(mgr.active_model, "qwen/qwen3.8-27b", "Must NOT switch model if 2/3 keys exhausted daily")

            # 4. Key 3 also hits daily limit -> 100% of keys hit daily limit -> NOW switches to fallback model openai/gpt-oss-20b
            mock_c3.chat.completions.create = AsyncMock(side_effect=daily_error)
            fallback_resp = MagicMock()
            mock_c1.chat.completions.create = AsyncMock(return_value=fallback_resp)
            mgr.current_idx = 2
            resp = asyncio.run(mgr.chat_completion(messages=[{"role": "user", "content": "test"}]))
            self.assertEqual(resp, fallback_resp)
            self.assertEqual(mgr.active_model, "openai/gpt-oss-20b", "MUST switch to fallback model when 100% of keys hit daily limit")

    def test_tpm_cooldown_sleeps_60s_when_all_keys_rate_limited(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.ranking.rank_internships import GroqManager
        import groq

        with patch("asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            mgr = GroqManager()
            mock_c1 = MagicMock()
            mock_c2 = MagicMock()
            mgr.clients = [mock_c1, mock_c2]
            mgr.total_keys = 2
            mgr.daily_exhausted_keys = set()
            mgr.active_model = mgr.primary_model

            tpm_error = groq.RateLimitError(
                "Rate limit reached for model on tokens per minute (TPM): Limit 8000, Used 8000. Please try again in 4s.",
                response=MagicMock(headers={}),
                body={}
            )
            success_resp = MagicMock()

            # Attempt 0: Key 1 fails TPM -> rotate to Key 2, sleep(1)
            # Attempt 1: Key 2 fails TPM -> all 2 keys tried -> sleep(60)
            # Attempt 2: Key 1 succeeds after cooldown
            mock_c1.chat.completions.create = AsyncMock(side_effect=[tpm_error, success_resp])
            mock_c2.chat.completions.create = AsyncMock(side_effect=tpm_error)

            mgr.current_idx = 0
            resp = asyncio.run(mgr.chat_completion(messages=[{"role": "user", "content": "test"}]))
            self.assertEqual(resp, success_resp)
            mock_sleep.assert_any_call(60)


if __name__ == "__main__":
    unittest.main()
