"""
GUI package for Linux (GTK4) desktop interface.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.gui.gui import JobApp, JobAppWindow

__all__ = ["JobApp", "JobAppWindow"]
