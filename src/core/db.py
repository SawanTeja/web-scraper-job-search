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

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_FILE = os.environ.get("JOBS_DB_PATH", os.path.join(ROOT_DIR, "jobs_db.sqlite"))

# ──────────────────────────────────────────────
#  Connection helper
# ──────────────────────────────────────────────
def get_conn(db_path=None):
    """Open (and return) a SQLite connection with WAL mode for concurrency."""
    target_db = db_path or DB_FILE
    conn = sqlite3.connect(target_db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allows concurrent reads while writing
    conn.execute("PRAGMA synchronous=NORMAL") # safe but faster than FULL
    conn.execute("PRAGMA cache_size=-8000")   # 8 MB page cache
    return conn


# ──────────────────────────────────────────────
#  Schema
# ──────────────────────────────────────────────
#  Schema
# ──────────────────────────────────────────────
def init_db(conn=None):
    """Create the jobs and companies tables if they don't exist, and add new columns."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            url          TEXT PRIMARY KEY,
            title        TEXT,
            status       TEXT DEFAULT 'New',
            rank         TEXT DEFAULT 'UNKNOWN',
            reason       TEXT DEFAULT '',
            added_at     TEXT,
            details      TEXT DEFAULT NULL,   -- JSON blob for AI-extracted fields
            company      TEXT DEFAULT NULL,
            ats_platform TEXT DEFAULT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rank   ON jobs(rank)")

    # Ensure optional columns exist if upgrading an older DB
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN company TEXT DEFAULT NULL")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN ats_platform TEXT DEFAULT NULL")
    except Exception:
        pass

    # Companies table for direct ATS scraping
    conn.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT,
            ats_platform    TEXT NOT NULL,
            identifier      TEXT NOT NULL,
            career_url      TEXT NOT NULL,
            last_scraped_at TEXT DEFAULT NULL,
            job_count       INTEGER DEFAULT 0,
            created_at      TEXT,
            UNIQUE(ats_platform, identifier)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_companies_ats ON companies(ats_platform)")

    # Deleted URLs table: remembers deleted/ignored job URLs permanently so scrapers never re-add them
    conn.execute("""
        CREATE TABLE IF NOT EXISTS deleted_urls (
            url        TEXT PRIMARY KEY,
            deleted_at TEXT,
            reason     TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_urls ON deleted_urls(url)")

    conn.commit()
    if close_after:
        conn.close()


# ──────────────────────────────────────────────
#  Read helpers
# ──────────────────────────────────────────────
def job_exists(url, conn):
    """Return True if a job URL already exists in the DB or was previously deleted."""
    row = conn.execute("SELECT 1 FROM jobs WHERE url=? UNION SELECT 1 FROM deleted_urls WHERE url=?", (url, url)).fetchone()
    return row is not None


def get_all_urls(conn):
    """Return a set of all URLs currently in the DB or previously deleted/ignored (so scrapers skip them)."""
    rows = conn.execute("SELECT url FROM jobs UNION SELECT url FROM deleted_urls").fetchall()
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
#  Company helpers
# ──────────────────────────────────────────────
def save_company(name, ats_platform, identifier, career_url, conn=None):
    """
    Insert a company and its ATS platform.
    Strictly prevents duplicates: if (ats_platform, identifier) or career_url already exists,
    it will never insert a duplicate company entry.
    """
    close_after = conn is None
    if conn is None:
        conn = get_conn()

    identifier_norm = identifier.lower().strip()
    career_url_norm = career_url.strip().rstrip("/")

    # Check if already exists by (ats_platform, identifier) OR career_url
    row = conn.execute(
        "SELECT id, name FROM companies WHERE (ats_platform=? AND identifier=?) OR career_url=?",
        (ats_platform, identifier_norm, career_url_norm)
    ).fetchone()

    if row:
        # Already exists — update name if new name is cleaner, but never duplicate
        if name and (not row["name"] or row["name"] == identifier_norm.title()):
            conn.execute("UPDATE companies SET name=? WHERE id=?", (name, row["id"]))
            conn.commit()
        if close_after:
            conn.close()
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO companies (name, ats_platform, identifier, career_url, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name or identifier_norm.title(), ats_platform, identifier_norm, career_url_norm, now))
    conn.commit()
    if close_after:
        conn.close()
    return True


def get_all_companies(conn=None):
    """Return all saved companies as a list of dicts."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    rows = conn.execute("SELECT * FROM companies ORDER BY name ASC").fetchall()
    result = [dict(r) for r in rows]
    if close_after:
        conn.close()
    return result


def get_company_count(conn=None):
    """Return the total number of saved companies."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    row = conn.execute("SELECT count(*) as cnt FROM companies").fetchone()
    count = row["cnt"] if row else 0
    if close_after:
        conn.close()
    return count


def delete_company(company_id, conn=None):
    """Delete a company by its ID from the companies table."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    cur = conn.execute("DELETE FROM companies WHERE id=?", (company_id,))
    conn.commit()
    deleted = cur.rowcount
    if close_after:
        conn.close()
    return deleted


def get_company_stats(conn=None):
    """Return summary statistics of saved companies and platforms."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    total_companies = conn.execute("SELECT count(*) FROM companies").fetchone()[0]
    total_jobs = conn.execute("SELECT sum(job_count) FROM companies").fetchone()[0] or 0
    platform_rows = conn.execute("SELECT ats_platform, count(*) FROM companies GROUP BY ats_platform ORDER BY count(*) DESC").fetchall()
    platforms = {r[0]: r[1] for r in platform_rows}
    if close_after:
        conn.close()
    return {
        "total_companies": total_companies,
        "total_jobs": total_jobs,
        "platforms": platforms
    }


def update_company_scraped(ats_platform, identifier, job_count, conn=None):
    """Update last_scraped_at timestamp and job count for a company."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        UPDATE companies
        SET last_scraped_at = ?, job_count = ?
        WHERE ats_platform = ? AND identifier = ?
    """, (now, job_count, ats_platform, identifier))
    conn.commit()
    if close_after:
        conn.close()


def extract_companies_from_existing_jobs(conn=None, max_workers=25, limit=None):
    """
    Backfill utility: inspects jobs already in SQLite, resolves redirect URLs,
    extracts company & ATS info, and populates the companies table.
    """
    from concurrent.futures import ThreadPoolExecutor
    try:
        from src.scrapers.ats_parser import parse_ats_info
    except ImportError:
        try:
            from scrapers.ats_parser import parse_ats_info
        except ImportError:
            from ats_parser import parse_ats_info

    close_after = conn is None
    if conn is None:
        conn = get_conn()

    query = "SELECT url, title, details FROM jobs"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()

    def process_row(r):
        url, title, det_raw = r["url"], r["title"], r["details"]
        human_name = None
        if det_raw:
            try:
                det = json.loads(det_raw)
                human_name = det.get("company")
            except Exception:
                pass

        info = parse_ats_info(url, title=title or human_name, resolve_google=True)
        if info:
            if human_name:
                info["company_name"] = human_name
            return url, info
        return url, None

    print(f"🔄 Scanning {len(rows)} jobs to extract saved companies...")
    saved_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for orig_url, info in executor.map(process_row, rows):
            if info:
                save_company(
                    name=info["company_name"],
                    ats_platform=info["ats_platform"],
                    identifier=info["identifier"],
                    career_url=info["career_url"],
                    conn=conn
                )
                # Also tag the job row if not already tagged
                conn.execute(
                    "UPDATE jobs SET company=?, ats_platform=? WHERE url=?",
                    (info["company_name"], info["ats_platform"], orig_url)
                )
                saved_count += 1

    conn.commit()
    total_companies = get_company_count(conn)
    print(f"✅ Extracted companies from {saved_count} jobs. Total unique companies saved: {total_companies}")
    if close_after:
        conn.close()
    return total_companies


# ──────────────────────────────────────────────
#  Write helpers
# ──────────────────────────────────────────────
def insert_or_ignore_job(url, title, conn, status="New", rank="UNKNOWN",
                          reason="", added_at=None, company=None, ats_platform=None, details=None):
    """
    Insert a new job row. Does nothing if the URL already exists.
    Returns True if a new row was inserted, False otherwise.
    """
    if added_at is None:
        added_at = datetime.now(timezone.utc).isoformat()
    details_str = json.dumps(details) if isinstance(details, dict) else details
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO jobs (url, title, status, rank, reason, added_at, details, company, ats_platform)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (url, title, status, rank, reason, added_at, details_str, company, ats_platform)
    )
    return cur.rowcount > 0


def update_job(url, conn, **kwargs):
    """
    Update one or more columns for an existing job row.
    Accepted kwargs: title, status, rank, reason, added_at, details, company, ats_platform.
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
                (url, title, status, rank, reason, added_at, details, company, ats_platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url,
                data.get("title", ""),
                data.get("status", "New"),
                data.get("rank", "UNKNOWN"),
                data.get("reason", ""),
                data.get("added_at", datetime.now(timezone.utc).isoformat()),
                details,
                data.get("company"),
                data.get("ats_platform"),
            )
        )
    conn.commit()
    if close_after:
        conn.close()


def delete_jobs_by_rank(rank, conn=None):
    """Delete all jobs matching the given rank. Archives URLs to deleted_urls so scrapers never re-add them."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR IGNORE INTO deleted_urls (url, deleted_at, reason)
        SELECT url, ?, 'deleted_' || rank FROM jobs WHERE rank=?
    """, (now_iso, rank))
    cur = conn.execute("DELETE FROM jobs WHERE rank=?", (rank,))
    conn.commit()
    if close_after:
        conn.close()
    return cur.rowcount


def delete_jobs_by_status(status, conn=None):
    """Delete all jobs matching the given status. Archives URLs to deleted_urls so scrapers never re-add them."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR IGNORE INTO deleted_urls (url, deleted_at, reason)
        SELECT url, ?, 'deleted_' || status FROM jobs WHERE status=?
    """, (now_iso, status))
    cur = conn.execute("DELETE FROM jobs WHERE status=?", (status,))
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


def clear_all_companies(conn=None):
    """Delete ALL companies from the companies table. Returns count of deleted companies."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    cur = conn.execute("DELETE FROM companies")
    conn.commit()
    deleted = cur.rowcount
    if close_after:
        conn.close()
    return deleted


def clear_everything(conn=None):
    """Delete all rows from jobs, companies, and deleted_urls tables. Returns dict with counts."""
    close_after = conn is None
    if conn is None:
        conn = get_conn()
    c_jobs = conn.execute("DELETE FROM jobs").rowcount
    c_companies = conn.execute("DELETE FROM companies").rowcount
    c_urls = conn.execute("DELETE FROM deleted_urls").rowcount
    conn.commit()
    if close_after:
        conn.close()
    return {
        "jobs": c_jobs,
        "companies": c_companies,
        "deleted_urls": c_urls
    }


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
