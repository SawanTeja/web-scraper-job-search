"""
Deterministic Job Filter launcher shim delegating to src.ranking.job_filter.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.ranking.job_filter import *

if __name__ == "__main__":
    apply_deterministic_filter_to_db()
