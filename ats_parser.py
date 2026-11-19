"""
Forwarding alias for ats_parser.py to src/scrapers/ats_parser.py.
"""
import sys
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
for _p in [_ROOT, _SRC]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.scrapers.ats_parser import *

