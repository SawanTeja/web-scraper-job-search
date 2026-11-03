#!/usr/bin/env python3
"""
prune_csv_ats_companies.py — Remove companies whose jobs have been found via ATS search from CSV.

Scans companies_sorted.csv against verified active ATS companies (job_count > 0)
and scraped jobs in jobs_db.sqlite, filtering out matches with high precision.
"""
import os
import sys
import csv
import re
import time
import sqlite3
import shutil

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(ROOT_DIR, "companies_sorted.csv")
BACKUP_CSV = os.path.join(ROOT_DIR, "companies_sorted.csv.bak")
DB_FILE = os.path.join(ROOT_DIR, "jobs_db.sqlite")

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
    "careers", "enterprise", "enterprises", "systems", "management", "the", "and"
}

TITLE_KEYWORDS = {
    "engineer", "developer", "manager", "physician", "easy apply", "search jobs",
    "specialist", "director", "consultant", "analyst", "coordinator", "technician",
    "officer", "administrator", "associate", "architect", "assistant", "intern",
    "internship", "head of", "vice president", "scientist", "recruiter", "designer",
    "lead", "senior", "junior", "staff", "principal", "entry", "trainee", "fellow"
}

clean_re = re.compile(r"[^a-z0-9\s]")
clean_alphanumeric = re.compile(r"[^a-z0-9]")


def norm(s: str) -> str:
    """Normalize string to lowercase alphanumeric."""
    if not s:
        return ""
    return clean_alphanumeric.sub("", s.lower())


def tokenize(s: str) -> set[str]:
    """Extract significant lowercase word tokens."""
    if not s:
        return set()
    s = clean_re.sub(" ", s.lower())
    return {w for w in s.split() if len(w) > 1 and w not in GENERIC_WORDS}


def is_compatible(csv_name: str, csv_dom: str, board_name: str, board_ident: str) -> bool:
    """
    Validate that a discovered ATS entry belongs to the CSV company.
    Strictly prevents false acronym or sub-token collisions.
    """
    n_csv = norm(csv_name)
    n_board = norm(board_name)
    if n_csv and n_csv == n_board:
        return True

    c_tokens = tokenize(csv_name)
    b_tokens = tokenize(board_name)

    common = c_tokens & b_tokens
    if common:
        if any(len(t) >= 4 for t in common):
            return True
        if len(c_tokens) == 1 and len(b_tokens) == 1 and c_tokens == b_tokens:
            return True

    if csv_dom:
        dom_parts = csv_dom.lower().split(".")
        dom_root = dom_parts[0] if dom_parts else ""
        if len(dom_root) >= 3:
            b_ident_root = board_ident.split("|")[0].lower().strip()
            if norm(dom_root) == norm(b_ident_root):
                if dom_root in n_csv or norm(b_ident_root) in n_csv:
                    return True
                if b_tokens and (tokenize(dom_root) & b_tokens):
                    return True

    return False


def load_ats_knowledge_base(db_path: str = DB_FILE):
    """Load active ATS companies and jobs from SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Companies with job_count > 0
    comp_rows = conn.execute("SELECT * FROM companies WHERE job_count > 0").fetchall()

    ident_to_comp = {}
    ident_set = set()
    for c in comp_rows:
        ident = c["identifier"]
        if not ident:
            continue
        ident_root = ident.split("|")[0].lower().strip()
        n_ident = norm(ident_root)
        if n_ident and len(n_ident) >= 2:
            ident_set.add(n_ident)
            ident_to_comp.setdefault(n_ident, []).append({
                "name": c["name"],
                "identifier": c["identifier"],
                "platform": c["ats_platform"],
                "job_count": c["job_count"]
            })

    name_to_comp = {}
    name_set = set()
    for c in comp_rows:
        nm = c["name"]
        if nm and len(nm) >= 3 and not any(kw in nm.lower() for kw in TITLE_KEYWORDS):
            n_nm = norm(nm)
            if len(n_nm) >= 3:
                name_set.add(n_nm)
                name_to_comp.setdefault(n_nm, []).append({
                    "name": c["name"],
                    "identifier": c["identifier"],
                    "platform": c["ats_platform"],
                    "job_count": c["job_count"]
                })

    # 2. Distinct clean company names from jobs table
    job_rows = conn.execute(
        "SELECT DISTINCT company FROM jobs WHERE ats_platform IS NOT NULL AND company IS NOT NULL"
    ).fetchall()
    for r in job_rows:
        nm = r[0]
        if nm and len(nm) >= 3 and not any(kw in nm.lower() for kw in TITLE_KEYWORDS):
            n_nm = norm(nm)
            if len(n_nm) >= 3:
                name_set.add(n_nm)
                name_to_comp.setdefault(n_nm, []).append({
                    "name": nm,
                    "identifier": "",
                    "platform": "ats_job",
                    "job_count": 1
                })

    conn.close()
    return ident_to_comp, ident_set, name_to_comp, name_set


def prune_csv(
    csv_file: str = DEFAULT_CSV,
    db_file: str = DB_FILE,
    create_backup: bool = True
):
    """Stream-process CSV and prune rows whose ATS jobs were found."""
    if not os.path.exists(csv_file):
        print(f"❌ Error: CSV file not found: {csv_file}")
        sys.exit(1)

    print("=" * 68)
    print("🧹 ATS JOB SEARCH CSV ROW PRUNER")
    print("=" * 68)
    print(f"📁 Target CSV:     {csv_file}")
    print(f"🗄️ Database:       {db_file}")

    print("\n⏳ Loading active ATS companies and jobs from database...")
    t0 = time.time()
    ident_to_comp, ident_set, name_to_comp, name_set = load_ats_knowledge_base(db_file)
    print(f"✅ Loaded {len(ident_set):,} active ATS identifiers & {len(name_set):,} company names in {time.time() - t0:.2f}s")

    tmp_file = csv_file + ".tmp"

    total_scanned = 0
    total_deleted = 0
    total_kept = 0
    sample_deleted = []

    print(f"\n🚀 Scanning and pruning {os.path.basename(csv_file)}...")
    t_start = time.time()

    with open(csv_file, "r", encoding="utf-8", errors="ignore") as in_f, \
         open(tmp_file, "w", encoding="utf-8", newline="") as out_f:

        # Preserve header row
        header_line = in_f.readline()
        out_f.write(header_line)

        for line in in_f:
            total_scanned += 1
            if total_scanned % 500000 == 0:
                elapsed = time.time() - t_start
                rate = total_scanned / elapsed if elapsed > 0 else 0
                print(f"  • {total_scanned:,} rows scanned | {total_deleted:,} deleted | {total_kept:,} kept ({rate:,.0f} rows/s)")

            # Fast row parsing
            try:
                # Format: ,name,domain,...
                # Standard line starts with optional index then comma
                parts = next(csv.reader([line]))
                if len(parts) < 3:
                    out_f.write(line)
                    total_kept += 1
                    continue

                c_name = parts[1]
                domain = parts[2].lower().strip()
                if domain.startswith("www."):
                    domain = domain[4:]
                dom_root = domain.split(".")[0] if domain else ""

                n_name = norm(c_name)
                n_dom = norm(dom_root)

                matched = False
                matched_info = None

                # 1. Company name check
                if n_name in name_set:
                    for cand in name_to_comp[n_name]:
                        if is_compatible(c_name, domain, cand["name"], cand["identifier"]):
                            matched = True
                            matched_info = (cand["name"], cand["identifier"])
                            break

                # 2. Domain root identifier check
                if not matched and n_dom in ident_set:
                    for cand in ident_to_comp[n_dom]:
                        if is_compatible(c_name, domain, cand["name"], cand["identifier"]):
                            matched = True
                            matched_info = (cand["name"], cand["identifier"])
                            break

                # 3. Sub-domain suffixes check (e.g. datadoghq -> datadog)
                if not matched:
                    for suff in ("hq", "tech", "labs", "app", "group"):
                        if dom_root.endswith(suff) and len(dom_root) > len(suff) + 3:
                            sub_root = dom_root[:-len(suff)].rstrip("-_")
                            n_sub = norm(sub_root)
                            if n_sub in ident_set:
                                for cand in ident_to_comp[n_sub]:
                                    if is_compatible(c_name, domain, cand["name"], cand["identifier"]):
                                        matched = True
                                        matched_info = (cand["name"], cand["identifier"])
                                        break
                            if matched:
                                break

                if matched:
                    total_deleted += 1
                    if len(sample_deleted) < 20:
                        sample_deleted.append((total_scanned, c_name, domain, matched_info))
                else:
                    out_f.write(line)
                    total_kept += 1

            except Exception:
                out_f.write(line)
                total_kept += 1

    elapsed_total = time.time() - t_start
    print(f"\n🏁 Finished scanning all {total_scanned:,} rows in {elapsed_total:.2f}s!")
    print(f"🗑️ Total Rows Deleted: {total_deleted:,}")
    print(f"💾 Total Rows Kept:    {total_kept:,}")

    # Validate output file before replacing
    if not os.path.exists(tmp_file) or os.path.getsize(tmp_file) < 1000000:
        print("❌ Error: Output temporary file is missing or suspiciously small. Aborting replace.")
        sys.exit(1)

    if create_backup:
        print(f"\n📦 Creating backup at: {BACKUP_CSV}...")
        # If backup doesn't exist yet, copy original to backup
        if not os.path.exists(BACKUP_CSV):
            shutil.copy2(csv_file, BACKUP_CSV)
            print(f"✅ Backup created ({os.path.getsize(BACKUP_CSV):,} bytes)")
        else:
            print("ℹ️ Existing backup found, keeping previous backup.")

    print(f"🔄 Replacing {csv_file} with pruned data...")
    os.replace(tmp_file, csv_file)
    print(f"✅ Replacement complete! New file size: {os.path.getsize(csv_file):,} bytes")

    print("\n" + "=" * 68)
    print("📋 SAMPLE DELETED COMPANIES (JOBS FOUND VIA ATS SEARCH)")
    print("=" * 68)
    for row_idx, c_name, dom, (b_name, b_ident) in sample_deleted:
        ident_disp = f" [slug: {b_ident}]" if b_ident else ""
        print(f"  • Row {row_idx:,}: {c_name} ({dom}) -> Matched ATS: {b_name}{ident_disp}")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    prune_csv()
