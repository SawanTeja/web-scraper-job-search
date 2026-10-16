"""
Database compatibility shim delegating to src.core.db.
"""
import sys
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from src.core.db import *

if __name__ == "__main__":
    init_db()
    stats = get_company_stats()
    print(f"Database initialized. Stats: {stats}")
