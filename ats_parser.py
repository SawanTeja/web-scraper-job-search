"""
ATS Parser compatibility shim delegating to src.scrapers.ats_parser.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.scrapers.ats_parser import *

if __name__ == "__main__":
    import unittest
    # Quick sanity check
    res = parse_ats_info("https://boards.greenhouse.io/stripe")
    print(f"ATS Parser sanity check: {res}")
