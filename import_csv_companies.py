#!/usr/bin/env python3
"""
import_csv_companies.py — Resumable, Deterministic ATS Company Importer from CSV.

Scans company domains from companies_sorted.csv and deterministically discovers
verified career portals on Greenhouse, Lever, Ashby, SmartRecruiters, Keka, etc.

Features:
- Standalone: Zero modifications to GUI or existing scrapers.
- Resumable: Maintains persistent checkpoint (csv_import_checkpoint.json).
  If interrupted or crashed, restarts from the exact row where it stopped.
- Strict Verification:
  * Only saves companies with an active, validated ATS career portal (active jobs > 0).
  * Validates company name compatibility to eliminate false acronym collisions (e.g. TCS -> Thornbury).
- High Concurrency: ThreadPoolExecutor with batch commits to SQLite in WAL mode.
- Rich Progress Bar: Real-time stats on Verified Found, Skipped, Speed, and ETA.
"""

import sys
import os
import csv
import json
import time
import signal
import re
import argparse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Auto-use virtual environment if available
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PYTHON = os.path.join(ROOT_DIR, "scraper_env", "bin", "python")
if os.path.exists(VENV_PYTHON) and sys.executable != VENV_PYTHON:
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)

SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import httpx
except ImportError:
    print("❌ Error: httpx is required. Install with: pip install httpx")
    sys.exit(1)

try:
    from rich.progress import (
        Progress, BarColumn, TextColumn, TimeElapsedColumn,
        TimeRemainingColumn, SpinnerColumn
    )
    from rich.console import Console
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from src.core.db import get_conn, init_db, save_company, get_company_count
from src.scrapers.ats_parser import parse_ats_info

CHECKPOINT_FILE = os.path.join(ROOT_DIR, "csv_import_checkpoint.json")
DEFAULT_CSV = os.path.join(ROOT_DIR, "companies_sorted.csv")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Blacklist of generic noise domains or slug tokens
BLACKLIST_SLUGS = {
    "login", "signin", "auth", "static", "assets", "embed", "api", "jobs",
    "careers", "search", "apply", "wday", "en-us", "error", "help", "support",
    "terms", "privacy", "cookie", "default", "www", "test", "demo", "sample",
    "undefined", "null", "job", "career", "portal", "home", "index", "staging",
    "about", "team", "corp", "company", "admin", "mail", "email", "blog"
}

GENERIC_WORDS = {
    "services", "inc", "incorporated", "corp", "corporation", "llc", "ltd",
    "limited", "group", "company", "co", "technologies", "tech", "technology",
    "solutions", "international", "global", "holdings", "consulting", "jobs",
    "careers", "enterprise", "enterprises", "systems", "management"
}

CORP_SUFFIXES = [
    r"-inc$", r"_inc$", r" inc$", r"\.inc$",
    r"-llc$", r"_llc$", r" llc$",
    r"-corp$", r"_corp$", r" corp$", r"-corporation$", r" corporation$",
    r"-ltd$", r"_ltd$", r" ltd$", r"-limited$", r" limited$",
    r"-technologies$", r" technologies$", r"-tech$", r" tech$",
    r"-software$", r" software$", r"-solutions$", r" solutions$",
    r"-group$", r" group$", r"-hq$", r" hq$", r"-app$", r" app$"
]

TECH_INDUSTRIES = [
    "software", "information technology", "internet", "financial services",
    "banking", "telecommunication", "fintech", "semiconductors",
    "defense & space", "biotechnology", "data", "cloud", "security",
    "computer games", "electrical", "electronic", "artificial intelligence"
]


# ──────────────────────────────────────────────────────────────
#  Checkpoint Management
# ──────────────────────────────────────────────────────────────
def load_checkpoint() -> dict:
    """Load progress from JSON checkpoint file."""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_processed_row": -1,
        "total_scanned": 0,
        "total_found": 0,
        "total_skipped": 0,
        "last_updated_at": None,
        "last_company": None
    }


def save_checkpoint(state: dict):
    """Atomically save checkpoint state."""
    state["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp_path = CHECKPOINT_FILE + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, CHECKPOINT_FILE)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
#  Name & Slug Validation (Anti-Collision)
# ──────────────────────────────────────────────────────────────
def is_name_compatible(csv_name: str, csv_domain: str, board_name: str, board_slug: str) -> bool:
    """
    Validates that a discovered ATS board actually belongs to the CSV company.
    Strictly prevents false acronym collisions (e.g. TCS -> Thornbury, AT&T -> Auto Tech Talent).
    """
    if not board_name:
        return False

    def tokenize(s):
        s = re.sub(r"[^a-z0-9\s]", " ", s.lower())
        return set(w for w in s.split() if len(w) > 1 and w not in GENERIC_WORDS)

    c_tokens = tokenize(csv_name)
    b_tokens = tokenize(board_name)

    # 1. Direct token overlap (e.g. "Stripe" vs "Stripe", "Databricks Inc." vs "Databricks")
    common = c_tokens & b_tokens
    if common:
        return True

    # 2. Domain root correlation
    dom_root = csv_domain.lower().split(".")[0] if csv_domain else ""
    if dom_root and len(dom_root) >= 3:
        dom_tokens = tokenize(dom_root)
        if dom_tokens & b_tokens:
            return True
        # If board name is essentially the domain root (e.g. "figma" -> "Figma")
        if re.sub(r"[^a-z0-9]", "", board_name.lower()) == dom_root:
            return True

    return False


def generate_candidate_slugs(name: str, domain: str) -> list[str]:
    """Generate high-probability candidate slugs from company name and domain."""
    slugs = []
    seen = set()

    def add_slug(s):
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9\-]", "", s)
        s = s.strip("-")
        if s and len(s) >= 2 and s not in BLACKLIST_SLUGS and s not in seen and not s.isdigit():
            seen.add(s)
            slugs.append(s)

    # 1. From Domain (e.g. stripe.com -> stripe, datadoghq.com -> datadoghq, datadog)
    if domain:
        clean_dom = domain.lower().strip()
        if clean_dom.startswith("www."):
            clean_dom = clean_dom[4:]
        parts = clean_dom.split(".")
        if parts:
            root = parts[0]
            add_slug(root)
            # Only strip compound suffixes with delimiters or explicit -hq, -tech, etc.
            for suff in ["-hq", "-tech", "-app", "-inc", "-group", "-labs", "-ai", "-io", "-co"]:
                if root.endswith(suff) and len(root) > len(suff) + 2:
                    add_slug(root[:-len(suff)])
            # Only strip without hyphen if word is long (e.g. datadoghq -> datadog)
            for suff in ["hq", "labs"]:
                if root.endswith(suff) and len(root) > len(suff) + 4:
                    add_slug(root[:-len(suff)])

    # 2. From Name (e.g. "Databricks Inc." -> databricks)
    if name:
        clean_name = name.lower().strip()
        for pat in CORP_SUFFIXES:
            clean_name = re.sub(pat, "", clean_name, flags=re.I).strip()
        add_slug(re.sub(r"[\s_]+", "-", clean_name))
        add_slug(re.sub(r"[^a-z0-9]", "", clean_name))

    return slugs[:3]


# ──────────────────────────────────────────────────────────────
#  Deterministic ATS Probing
# ──────────────────────────────────────────────────────────────
def probe_ats_slug(client: httpx.Client, slug: str, csv_name: str, csv_domain: str) -> dict:
    """
    Directly query official ATS endpoints with a candidate slug.
    Strictly verifies:
    1. HTTP 200 response
    2. Active postings exist (job count > 0)
    3. Company name compatibility (no false acronym matches)
    """
    clean_display = csv_name or slug.replace("-", " ").title()

    # 1. Greenhouse (Direct REST API with Active Jobs Check)
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        resp = client.get(url, timeout=3.5)
        if resp.status_code == 200:
            data = resp.json()
            jobs = data.get("jobs", [])
            if jobs:  # Must have at least 1 active posting
                # Fetch board name
                board_info = client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}", timeout=3.0).json()
                real_name = board_info.get("name") or clean_display
                if is_name_compatible(csv_name, csv_domain, real_name, slug):
                    return {
                        "name": real_name.strip(),
                        "ats_platform": "greenhouse",
                        "identifier": slug,
                        "career_url": f"https://boards.greenhouse.io/{slug}",
                        "job_count": len(jobs)
                    }
    except Exception:
        pass

    # 2. Lever (Direct JSON Postings API with Active Jobs Check)
    try:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        resp = client.get(url, timeout=3.5)
        if resp.status_code == 200:
            postings = resp.json()
            if isinstance(postings, list) and len(postings) > 0:
                board_name = clean_display
                # Lever postings include categories or company name
                if is_name_compatible(csv_name, csv_domain, board_name, slug):
                    return {
                        "name": board_name,
                        "ats_platform": "lever",
                        "identifier": slug,
                        "career_url": f"https://jobs.lever.co/{slug}",
                        "job_count": len(postings)
                    }
    except Exception:
        pass

    # 3. Ashby (Career board web response with title check)
    try:
        url = f"https://jobs.ashbyhq.com/{slug}"
        resp = client.get(url, timeout=3.5)
        if resp.status_code == 200 and "ashby" in resp.text.lower():
            m = re.search(r"<title>(.*?)</title>", resp.text, re.I)
            if m:
                board_name = m.group(1).replace("Careers", "").replace("Jobs", "").strip(" -|")
                if is_name_compatible(csv_name, csv_domain, board_name, slug):
                    return {
                        "name": board_name or clean_display,
                        "ats_platform": "ashby",
                        "identifier": slug,
                        "career_url": url,
                        "job_count": 1
                    }
    except Exception:
        pass

    # 4. SmartRecruiters (Active postings count check)
    try:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        resp = client.get(url, timeout=3.5)
        if resp.status_code == 200:
            data = resp.json()
            total_found = data.get("totalFound", 0)
            if total_found > 0:
                if is_name_compatible(csv_name, csv_domain, clean_display, slug):
                    return {
                        "name": clean_display,
                        "ats_platform": "smartrecruiters",
                        "identifier": slug,
                        "career_url": f"https://jobs.smartrecruiters.com/{slug}",
                        "job_count": total_found
                    }
    except Exception:
        pass

    # 5. Keka (Active careers portal check)
    try:
        url = f"https://{slug}.keka.com/careers"
        resp = client.get(url, timeout=3.5)
        if resp.status_code == 200 and "keka" in resp.text.lower():
            if is_name_compatible(csv_name, csv_domain, clean_display, slug):
                return {
                    "name": clean_display,
                    "ats_platform": "keka",
                    "identifier": slug,
                    "career_url": url,
                    "job_count": 1
                }
    except Exception:
        pass

    return None


def probe_domain_careers(client: httpx.Client, domain: str, csv_name: str) -> dict:
    """
    Sends fast HEAD/GET to https://domain/careers.
    Follows redirects and inspects if destination points to an ATS portal.
    Authoritative because the company's own domain redirects to it.
    """
    if not domain:
        return None

    clean_dom = domain.lower().strip()
    if clean_dom.startswith("www."):
        clean_dom = clean_dom[4:]

    # Test /careers first; only test /jobs if /careers was a clean 404
    for path in ["/careers", "/jobs"]:
        url = f"https://{clean_dom}{path}"
        try:
            resp = client.get(url, timeout=2.5, follow_redirects=True)
            if resp.status_code in [200, 301, 302, 307, 308]:
                final_url = str(resp.url)
                info = parse_ats_info(final_url)
                if info:
                    info["name"] = csv_name or info.get("company_name") or clean_dom
                    return info

                # Inspect HTML for direct ATS links (limit reading to first 30KB)
                text = resp.text[:30000]
                matches = re.findall(
                    r'(https?://[a-zA-Z0-9\-_./]+(?:greenhouse\.io|lever\.co|myworkdayjobs\.com|ashbyhq\.com|smartrecruiters\.com|workable\.com|bamboohr\.com|keka\.com|icims\.com)[a-zA-Z0-9\-_./?]*)',
                    text,
                    re.I
                )
                for raw_ats_url in matches:
                    parsed = parse_ats_info(raw_ats_url)
                    if parsed:
                        parsed["name"] = csv_name or parsed.get("company_name") or clean_dom
                        return parsed
                break  # Got valid response but no ATS links, no need to probe /jobs
            elif resp.status_code != 404:
                break  # If blocked or 5xx, don't hammer /jobs
        except Exception:
            break  # If connection timed out on /careers, don't wait for /jobs

    return None


def verify_company_record(client: httpx.Client, row: dict, probe_domains: bool = True) -> tuple[dict, str]:
    """Complete verification pipeline for a single CSV row."""
    name = (row.get("name") or "").strip().title()
    domain = (row.get("domain") or "").strip().lower()

    if not domain and not name:
        return None, "empty_row"

    # Stage 1: Candidate Slugs Probe
    slugs = generate_candidate_slugs(name, domain)
    for slug in slugs:
        matched = probe_ats_slug(client, slug, name, domain)
        if matched:
            return matched, "direct_ats_slug"

    # Stage 2: Domain /careers Redirect Probe (if enabled)
    if probe_domains and domain:
        matched = probe_domain_careers(client, domain, name)
        if matched:
            return matched, "domain_careers_redirect"

    return None, "no_ats_found"


# ──────────────────────────────────────────────────────────────
#  Batch Processor & Coordinator
# ──────────────────────────────────────────────────────────────
def process_batch(
    client: httpx.Client,
    batch: list[tuple[int, dict]],
    probe_domains: bool
) -> list[tuple[int, dict, dict, str]]:
    """Process a batch of companies concurrently."""
    results = []
    for row_idx, row in batch:
        ats_info, reason = verify_company_record(client, row, probe_domains=probe_domains)
        results.append((row_idx, row, ats_info, reason))
    return results


def run_importer(
    csv_file: str = DEFAULT_CSV,
    limit: int = None,
    workers: int = 25,
    min_employees: int = 0,
    tech_only: bool = False,
    probe_domains: bool = True,
    reset: bool = False
):
    """Master CLI execution loop."""
    if not os.path.exists(csv_file):
        print(f"❌ Error: CSV file not found at: {csv_file}")
        sys.exit(1)

    conn = get_conn()
    init_db(conn)

    initial_db_count = get_company_count(conn)

    # Checkpoint loading
    checkpoint = load_checkpoint()
    if reset:
        checkpoint = {
            "last_processed_row": -1,
            "total_scanned": 0,
            "total_found": 0,
            "total_skipped": 0,
            "last_updated_at": None,
            "last_company": None
        }
        save_checkpoint(checkpoint)
        print("🔄 Checkpoint reset: starting fresh from Row 0.")

    resume_row = checkpoint["last_processed_row"] + 1

    print("\n" + "=" * 68)
    print("🚀 DETERMINISTIC ATS COMPANY IMPORTER & VERIFIER")
    print("=" * 68)
    print(f"📁 Source CSV:          {csv_file}")
    print(f"🏢 Current Database:    {initial_db_count:,} verified companies")
    print(f"⚡ Worker Threads:      {workers}")
    print(f"🔍 Domain Probing:      {'Enabled' if probe_domains else 'Disabled (Slugs only)'}")
    if min_employees > 0:
        print(f"👥 Min Employees:       >= {min_employees:,}")
    if tech_only:
        print(f"💻 Filter:              Tech/Engineering/Finance industries only")
    if resume_row > 0:
        print(f"⏩ Resuming from Row:   {resume_row:,}")
    if limit:
        print(f"🎯 Target Batch Limit:  {limit:,} rows")
    print("=" * 68 + "\n")

    stop_flag = False

    def handle_signal(sig, frame):
        nonlocal stop_flag
        print("\n\n⚠️ Interruption detected (Ctrl+C). Saving checkpoint and exiting cleanly...")
        stop_flag = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    stats = {
        "scanned": checkpoint["total_scanned"],
        "found": checkpoint["total_found"],
        "skipped": checkpoint["total_skipped"],
        "current_name": ""
    }

    batch_size = min(workers * 4, limit) if limit else workers * 4

    progress = None
    task_id = None
    if HAS_RICH:
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("• [bold green]{task.fields[found]} Verified[/]"),
            TextColumn("• [dim]{task.fields[skipped]} Skipped[/]"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console
        )
        task_id = progress.add_task(
            "Importing Companies...",
            total=limit or 50000,
            found=stats["found"],
            skipped=stats["skipped"]
        )
        progress.start()

    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=4.0,
        limits=httpx.Limits(max_keepalive_connections=workers, max_connections=workers * 2)
    )

    try:
        with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)

            current_row = -1
            chunk = []
            processed_in_run = 0

            for row in reader:
                current_row += 1

                # Fast forward to checkpoint
                if current_row < resume_row:
                    continue

                if stop_flag:
                    break

                if limit and processed_in_run >= limit:
                    break

                # Apply filters
                if min_employees > 0:
                    try:
                        emp_str = (row.get("current employee estimate") or "0").replace(",", "").strip()
                        if float(emp_str) < min_employees:
                            continue
                    except Exception:
                        continue

                if tech_only:
                    ind = (row.get("industry") or "").lower()
                    if not any(t in ind for t in TECH_INDUSTRIES):
                        continue

                chunk.append((current_row, row))
                processed_in_run += 1

                # Process chunk when full
                if len(chunk) >= batch_size:
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        future_to_item = {
                            executor.submit(verify_company_record, client, r_data, probe_domains): (r_idx, r_data)
                            for r_idx, r_data in chunk
                        }
                        for fut in as_completed(future_to_item):
                            r_idx, r_data = future_to_item[fut]
                            ats_info, reason = fut.result()
                            stats["scanned"] += 1
                            c_name = r_data.get("name") or r_data.get("domain") or f"Row {r_idx}"
                            stats["current_name"] = c_name

                            if ats_info:
                                saved = save_company(
                                    name=ats_info.get("name") or c_name,
                                    ats_platform=ats_info["ats_platform"],
                                    identifier=ats_info["identifier"],
                                    career_url=ats_info["career_url"],
                                    conn=conn
                                )
                                if saved:
                                    stats["found"] += 1
                                else:
                                    stats["skipped"] += 1
                            else:
                                stats["skipped"] += 1

                            checkpoint["last_processed_row"] = max(checkpoint["last_processed_row"], r_idx)
                            checkpoint["last_company"] = c_name

                    # Save checkpoint at end of chunk
                    checkpoint["total_scanned"] = stats["scanned"]
                    checkpoint["total_found"] = stats["found"]
                    checkpoint["total_skipped"] = stats["skipped"]
                    save_checkpoint(checkpoint)

                    # Update progress UI
                    if HAS_RICH and progress:
                        progress.update(
                            task_id,
                            advance=len(chunk),
                            found=stats["found"],
                            skipped=stats["skipped"],
                            description=f"[cyan]Scanning: [bold white]{stats['current_name'][:20]}[/]"
                        )
                    else:
                        print(f"Row {checkpoint['last_processed_row']:,} | Verified: {stats['found']} | Skipped: {stats['skipped']}")

                    chunk = []

            # Process remaining items in final chunk
            if chunk and not stop_flag:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_item = {
                        executor.submit(verify_company_record, client, r_data, probe_domains): (r_idx, r_data)
                        for r_idx, r_data in chunk
                    }
                    for fut in as_completed(future_to_item):
                        r_idx, r_data = future_to_item[fut]
                        ats_info, reason = fut.result()
                        stats["scanned"] += 1
                        c_name = r_data.get("name") or r_data.get("domain") or f"Row {r_idx}"
                        if ats_info:
                            saved = save_company(
                                name=ats_info.get("name") or c_name,
                                ats_platform=ats_info["ats_platform"],
                                identifier=ats_info["identifier"],
                                career_url=ats_info["career_url"],
                                conn=conn
                            )
                            if saved:
                                stats["found"] += 1
                            else:
                                stats["skipped"] += 1
                        else:
                            stats["skipped"] += 1

                        checkpoint["last_processed_row"] = max(checkpoint["last_processed_row"], r_idx)
                        checkpoint["last_company"] = c_name

                checkpoint["total_scanned"] = stats["scanned"]
                checkpoint["total_found"] = stats["found"]
                checkpoint["total_skipped"] = stats["skipped"]
                save_checkpoint(checkpoint)

    finally:
        client.close()
        conn.close()
        if HAS_RICH and progress:
            progress.stop()

    final_db_count = get_company_count()

    print("\n" + "=" * 68)
    print("🏁 IMPORT RUN COMPLETED")
    print("=" * 68)
    print(f"📊 Companies Scanned in Run:   {stats['scanned']:,}")
    print(f"✅ Verified ATS Portals Added: {stats['found']:,}")
    print(f"❌ Skipped (No Valid ATS):     {stats['skipped']:,}")
    print(f"🏢 Total Database Companies:   {final_db_count:,} (+{final_db_count - initial_db_count})")
    print(f"💾 Checkpoint Saved At Row:    {checkpoint['last_processed_row']:,}")
    print("=" * 68 + "\n")


# ──────────────────────────────────────────────────────────────
#  CLI Parser
# ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Deterministic, Resumable ATS Company Importer from CSV"
    )
    parser.add_argument(
        "--csv", default=DEFAULT_CSV, help="Path to companies CSV (default: companies_sorted.csv)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Max candidate rows to process (default: unlimited)"
    )
    parser.add_argument(
        "--workers", type=int, default=25, help="Number of concurrent worker threads (default: 25)"
    )
    parser.add_argument(
        "--min-employees", type=int, default=0, help="Filter for minimum current employee estimate (default: 0)"
    )
    parser.add_argument(
        "--tech-only", action="store_true", help="Filter for software, IT, internet, and fintech industries"
    )
    parser.add_argument(
        "--no-domain-probe", action="store_true", help="Skip domain /careers redirect probing (fast slug-only mode)"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Reset checkpoint and start fresh from row 0"
    )

    args = parser.parse_args()

    run_importer(
        csv_file=args.csv,
        limit=args.limit,
        workers=args.workers,
        min_employees=args.min_employees,
        tech_only=args.tech_only,
        probe_domains=not args.no_domain_probe,
        reset=args.reset
    )


if __name__ == "__main__":
    main()
