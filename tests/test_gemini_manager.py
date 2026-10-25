import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.ranking.gemini_manager import GeminiManager, get_all_gemini_keys


class TestGeminiManager(unittest.TestCase):
    @patch.dict(os.environ, {
        "GEMINI_API_KEY_1": "test_key_1",
        "GEMINI_API_KEY_2": "test_key_2",
    }, clear=False)
    def test_key_discovery(self):
        keys = get_all_gemini_keys()
        self.assertIn("test_key_1", keys)
        self.assertIn("test_key_2", keys)

    @patch("src.ranking.gemini_manager.genai.Client")
    @patch.dict(os.environ, {
        "GEMINI_API_KEY_1": "mock_key_1",
        "GEMINI_API_KEY_2": "mock_key_2",
    }, clear=False)
    def test_rotation_and_exhaustion(self, mock_client_cls):
        gm = GeminiManager()
        self.assertTrue(gm.is_available())
        self.assertEqual(len(gm.clients), 2)
        self.assertEqual(gm.active_model, "gemini-3.5-flash-lite")

        # Test key rotation
        gm.rotate_key("test")
        self.assertEqual(gm.current_key_idx, 1)

        # Test marking keys exhausted
        gm.mark_current_key_exhausted()
        self.assertEqual(len(gm.daily_exhausted_keys), 1)

        # Mark second key exhausted -> should advance to fallback model
        gm.mark_current_key_exhausted()
        self.assertEqual(gm.active_model, "gemini-3.1-flash-lite")
        self.assertEqual(len(gm.daily_exhausted_keys), 0)

    @patch("src.ranking.gemini_manager.genai.Client")
    @patch.dict(os.environ, {
        "GEMINI_API_KEY_1": "mock_key_1",
        "GEMINI_API_KEY_2": "mock_key_2",
    }, clear=False)
    def test_circular_key_rotation(self, mock_client_cls):
        gm = GeminiManager()
        self.assertEqual(len(gm.clients), 2)

        # Call 1: Key #1
        self.assertEqual(gm.get_current_key_num(), 1)
        gm.advance_key()

        # Call 2: Key #2
        self.assertEqual(gm.get_current_key_num(), 2)
        gm.advance_key()

        # Call 3: Key #1 (circular wrap)
        self.assertEqual(gm.get_current_key_num(), 1)
        gm.advance_key()

        # Call 4: Key #2
        self.assertEqual(gm.get_current_key_num(), 2)


if __name__ == "__main__":
    unittest.main()
