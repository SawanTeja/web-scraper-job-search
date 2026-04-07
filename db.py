"""
db.py — Shared SQLite helper for the job scraper project.

Provides a thin compatibility layer so intern_scraper_fast.py, gui.py,
and rank_internships.py all work with the same SQLite database without
ever loading the entire dataset into RAM at once.
"""
import sqlite3
import json
import os
from datetime import datetime, timezone

DB_FILE = "jobs_db.sqlite"

# ──────────────────────────────────────────────
#  Connection helper
# ──────────────────────────────────────────────
def get_conn():
    """Open (and return) a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allows concurrent reads while writing
    conn.execute("PRAGMA synchronous=NORMAL") # safe but faster than FULL
    conn.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
    return conn


# ──────────────────────────────────────────────
#  Schema
# ──────────────────────────────────────────────
def init_db(conn=None):
    """Create the jobs table if it doesn't exist."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            url        TEXT PRIMARY KEY,
            title      TEXT,
            status     TEXT DEFAULT 'New',
            rank       TEXT DEFAULT 'UNKNOWN',
            reason     TEXT DEFAULT '',
            added_at   TEXT,
            details    TEXT DEFAULT NULL   -- JSON blob for AI-extracted fields
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rank   ON jobs(rank)")
    conn.commit()
    if close_after:
        conn.close()


# ──────────────────────────────────────────────
#  Read helpers
# ──────────────────────────────────────────────
def job_exists(url, conn):
    """Return True if a job URL already exists in the DB."""
    row = conn.execute("SELECT 1 FROM jobs WHERE url=?", (url,)).fetchone()
    return row is not None


def get_all_urls(conn):
    """Return a set of all URLs currently in the DB (cheap — only fetches URLs)."""
    rows = conn.execute("SELECT url FROM jobs").fetchall()
    return {row["url"] for row in rows}


def load_db(conn=None):
    """
    Load the entire DB as a dict[url -> data_dict].
    Kept for backwards-compatibility with gui.py / rank_internships.py.
    ⚠️  Avoid calling this on very large databases; prefer per-row queries.
    """
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    result = {}
    for row in rows:
        data = dict(row)
        url = data.pop("url")
        # Deserialise the JSON details blob back to a dict
        if data.get("details"):
            try:
                data["details"] = json.loads(data["details"])
            except Exception:
                data["details"] = {}
        result[url] = data
    if close_after:
        conn.close()
    return result


# ──────────────────────────────────────────────
#  Write helpers
# ──────────────────────────────────────────────
def insert_or_ignore_job(url, title, conn, status="New", rank="UNKNOWN",
                          reason="", added_at=None):
    """
    Insert a new job row. Does nothing if the URL already exists.
    Returns True if a new row was inserted, False otherwise.
    """
    if added_at is None:
        added_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO jobs (url, title, status, rank, reason, added_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (url, title, status, rank, reason, added_at)
    )
    return cur.rowcount > 0


def update_job(url, conn, **kwargs):
    """
    Update one or more columns for an existing job row.
    Accepted kwargs: title, status, rank, reason, added_at, details.
    'details' may be a dict (will be JSON-serialised) or a string.
    """
    if not kwargs:
        return
    if "details" in kwargs and isinstance(kwargs["details"], dict):
        kwargs["details"] = json.dumps(kwargs["details"])
    cols = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [url]
    conn.execute(f"UPDATE jobs SET {cols} WHERE url=?", vals)


def save_db(db_dict, conn=None):
    """
    Persist an in-memory dict (old-style) back to SQLite.
    Used by gui.py which still maintains jobs_db as a dict.
    Only updates fields that actually changed (uses INSERT OR REPLACE).
    """
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    for url, data in db_dict.items():
        details = data.get("details")
        if isinstance(details, dict):
            details = json.dumps(details)
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs
                (url, title, status, rank, reason, added_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                data.get("title", ""),
                data.get("status", "New"),
                data.get("rank", "UNKNOWN"),
                data.get("reason", ""),
                data.get("added_at", datetime.now(timezone.utc).isoformat()),
                details,
            )
        )
    conn.commit()
    if close_after:
        conn.close()


def delete_jobs_by_rank(rank, conn=None):
    """Delete all jobs matching the given rank. Returns count of deleted rows."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    cur = conn.execute("DELETE FROM jobs WHERE rank=?", (rank,))
    conn.commit()
    if close_after:
        conn.close()
    return cur.rowcount


def clear_all_jobs(conn=None):
    """Delete ALL jobs from the database. Returns count of deleted rows."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    cur = conn.execute("DELETE FROM jobs")
    conn.commit()
    if close_after:
        conn.close()
    return cur.rowcount


# ──────────────────────────────────────────────
#  Migration: JSON → SQLite (run once)
# ──────────────────────────────────────────────
def migrate_from_json(json_path="jobs_db.json"):
    """
    One-time migration: reads jobs_db.json and inserts all rows into SQLite.
    Safe to re-run — uses INSERT OR IGNORE so existing rows aren't overwritten.
    """
    if not os.path.exists(json_path):
        print(f"⚠️  {json_path} not found, skipping migration.")
        return

    import json as _json
    with open(json_path, "r", encoding="utf-8") as f:
        old = _json.load(f)

    conn = get_conn()
    init_db(conn)
    inserted = 0
    for url, data in old.items():
        details = data.get("details")
        if isinstance(details, dict):
            details = _json.dumps(details)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
                (url, title, status, rank, reason, added_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                data.get("title", ""),
                data.get("status", "New"),
                data.get("rank", "UNKNOWN"),
                data.get("reason", ""),
                data.get("added_at", datetime.now(timezone.utc).isoformat()),
                details,
            )
        )
        inserted += cur.rowcount
    conn.commit()
    conn.close()
    print(f"✅ Migration complete: {inserted} new rows inserted from {json_path}")


# Auto-init the DB on import so callers don't have to remember to call init_db()
init_db()
