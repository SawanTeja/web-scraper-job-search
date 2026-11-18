"""
test_process_manager.py — Unit tests for process lifecycle, cleanup, and stop flag functionality.
"""
import os
import sys
import unittest
import subprocess
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.core.process_manager import (
    register_child_process,
    unregister_child_process,
    get_active_child_processes,
    create_stop_flag,
    clear_stop_flag,
    cleanup_all_child_processes,
    cleanup_orphaned_scrapers
)


class TestProcessManager(unittest.TestCase):
    def setUp(self):
        clear_stop_flag("test_stop.flag")

    def tearDown(self):
        clear_stop_flag("test_stop.flag")
        clear_stop_flag("stop.flag")

    def test_stop_flag_lifecycle(self):
        flag_path = "test_stop.flag"
        self.assertFalse(os.path.exists(flag_path))
        create_stop_flag(flag_path)
        self.assertTrue(os.path.exists(flag_path))
        with open(flag_path, "r") as f:
            self.assertEqual(f.read().strip(), "stop")
        clear_stop_flag(flag_path)
        self.assertFalse(os.path.exists(flag_path))

    def test_register_and_cleanup_processes(self):
        # Spawn a dummy sleep process
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
        try:
            register_child_process(proc)
            self.assertIn(proc, get_active_child_processes())

            # Cleanup
            cleanup_all_child_processes()
            self.assertIsNotNone(proc.poll())
            self.assertEqual(len(get_active_child_processes()), 0)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_unregister_child_process(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.1)"])
        try:
            register_child_process(proc)
            self.assertIn(proc, get_active_child_processes())
            unregister_child_process(proc)
            self.assertNotIn(proc, get_active_child_processes())
        finally:
            proc.wait()


if __name__ == "__main__":
    unittest.main()

