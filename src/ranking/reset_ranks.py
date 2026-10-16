"""
reset_ranks.py — Reset ranks of all non-Applied jobs to UNKNOWN for re-evaluation.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.core.db import get_conn, init_db
except ImportError:
    from db import get_conn, init_db


def reset_ranks():
    init_db()
    conn = get_conn()
    cur = conn.execute(
        "UPDATE jobs SET rank='UNKNOWN', reason='Reset for re-evaluation' WHERE status != 'Applied'"
    )
    conn.commit()
    count = cur.rowcount
    conn.close()
    print(f"Successfully reset {count} jobs to UNKNOWN rank in SQLite.")
    return count


if __name__ == "__main__":
    reset_ranks()
