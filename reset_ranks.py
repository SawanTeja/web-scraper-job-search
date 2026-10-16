"""
reset_ranks.py — Reset ranks of all non-Applied jobs to UNKNOWN for re-evaluation.
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

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
