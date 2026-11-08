#!/usr/bin/env python3
"""
crawl_and_verify_massive_ats.py — High-Throughput Global Multi-ATS Harvester & Active Verifier.

Aggregates over 45,000 candidates across Greenhouse, Ashby, Lever, Workday, Teamtailor,
Recruitee, Breezy, BambooHR, iCIMS, Personio, and other international ATS platforms.
Actively verifies live HTTP endpoints and uniquely ingests thousands of verified companies into jobs_db.sqlite.
"""

import sys
import os
import re
import csv
import json
import time
import random
import sqlite3
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

import httpx
from src.scrapers.ats_parser import parse_ats_info
from src.scrapers.company_crawler import is_valid_identifier

DB_PATH = os.path.join(ROOT_DIR, "jobs_db.sqlite")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def load_existing_db() -> tuple[set, set]:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    cur.execute("SELECT ats_platform, identifier FROM companies")
    existing_keys = {(row[0].lower(), row[1].lower()) for row in cur.fetchall()}
    cur.execute("SELECT career_url FROM companies")
    existing_urls = {row[0].strip().rstrip("/").lower() for row in cur.fetchall() if row[0]}
    conn.close()
    return existing_keys, existing_urls


def gather_all_candidates(existing_keys: set, existing_urls: set) -> list[dict]:
    print("🌍 [Step 1/3] Pooling candidates across global multi-ATS corpora...")
    candidates = {}

    def add_cand(prov, tok, url, name=None):
        if not prov or not tok or not url:
            return
        prov = prov.lower().strip()
        tok = tok.lower().strip()
        if not is_valid_identifier(prov, tok):
            return
        u_norm = url.strip().rstrip("/").lower()
        key = (prov, tok)
        if key in existing_keys or u_norm in existing_urls or key in candidates:
            return
        display_name = name or tok.split("|")[0].replace("-", " ").replace("_", " ").title()
        candidates[key] = {
            "name": display_name.strip(),
            "ats_platform": prov,
            "identifier": tok,
            "career_url": url.strip()
        }

    # 1. /tmp/all_ats.csv (Greenhouse, Ashby, Lever, Teamtailor, Recruitee, Breezy)
    if os.path.exists("/tmp/all_ats.csv"):
        print("   📂 Ingesting open-ats-feed corpus...")
        with open("/tmp/all_ats.csv", "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                p = row.get("provider")
                t = row.get("token")
                u = row.get("board_url")
                add_cand(p, t, u)

    # 2. Workday companies
    if os.path.exists("/tmp/workday_companies.json"):
        print("   📂 Ingesting Workday enterprise corpus...")
        with open("/tmp/workday_companies.json", "r", encoding="utf-8") as f:
            wd_list = json.load(f)
        for item in wd_list:
            parts = item.split("|")
            if len(parts) >= 3:
                tenant, server, site = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if tenant:
                    u = f"https://{tenant}.{server}.myworkdayjobs.com/{site}" if server else f"https://{tenant}.myworkdayjobs.com/{site}"
                    add_cand("workday", item, u, tenant.title())

    # 3. BambooHR companies
    if os.path.exists("/tmp/bamboohr_companies.json"):
        print("   📂 Ingesting BambooHR corpus...")
        with open("/tmp/bamboohr_companies.json", "r", encoding="utf-8") as f:
            bhr_list = json.load(f)
        for slug in bhr_list:
            slug = slug.strip()
            if slug:
                add_cand("bamboohr", slug, f"https://{slug}.bamboohr.com/careers")

    # 4. iCIMS companies
    if os.path.exists("/tmp/icims_companies.json"):
        print("   📂 Ingesting iCIMS corpus...")
        with open("/tmp/icims_companies.json", "r", encoding="utf-8") as f:
            ic_list = json.load(f)
        for slug in ic_list:
            slug = slug.strip().lstrip("-")
            if slug and len(slug) > 1:
                add_cand("icims", slug, f"https://{slug}.icims.com/jobs/search")

    cand_list = list(candidates.values())
    # Shuffle to interleave platforms evenly for balanced global representation
    random.seed(42)
    random.shuffle(cand_list)

    print(f"\n🔎 Total unique NEW candidates pooled: {len(cand_list)}")
    from collections import Counter
    counts = Counter(c["ats_platform"] for c in cand_list)
    for plat, cnt in counts.most_common():
        print(f"   • {plat.upper():16}: {cnt:5} candidates")

    return cand_list


def verify_ats_endpoint(client: httpx.Client, comp: dict) -> tuple[bool, str, dict]:
    plat = comp["ats_platform"]
    ident = comp["identifier"]
    url = comp["career_url"]
    display_name = comp["name"]

    try:
        if plat == "greenhouse":
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{ident}", timeout=3.5)
            if r.status_code == 200:
                data = r.json()
                real_name = data.get("name")
                return True, (real_name or display_name).strip(), comp
            return False, "", comp

        elif plat == "ashby":
            r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{ident}", timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "lever":
            r = client.get(f"https://api.lever.co/v0/postings/{ident}?mode=json", timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "teamtailor":
            r = client.get(f"https://{ident}.teamtailor.com", timeout=3.5)
            return r.status_code in (200, 301, 302), display_name, comp

        elif plat == "recruitee":
            r = client.get(f"https://{ident}.recruitee.com/api/offers/", timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "breezy":
            r = client.get(f"https://{ident}.breezy.hr/json", timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "workday":
            r = client.get(url, timeout=4.0)
            return r.status_code in (200, 301, 302, 303, 307, 308, 401, 403), display_name, comp

        elif plat == "bamboohr":
            r = client.get(f"https://{ident}.bamboohr.com/careers/list", timeout=3.5)
            if r.status_code == 200:
                return True, display_name, comp
            r2 = client.get(f"https://{ident}.bamboohr.com/careers", timeout=3.5)
            return r2.status_code in (200, 301, 302, 307, 308), display_name, comp

        elif plat == "icims":
            r = client.get(f"https://{ident}.icims.com", timeout=3.5)
            return r.status_code in (200, 301, 302, 307, 308), display_name, comp

        elif plat == "workable":
            r = client.post(f"https://apply.workable.com/api/v3/accounts/{ident}/jobs", json={}, timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "personio":
            for d in ["de", "com"]:
                r = client.get(f"https://{ident}.jobs.personio.{d}/xml", timeout=3.5)
                if r.status_code == 200:
                    return True, display_name, comp
            return False, "", comp

        elif plat == "rippling":
            r = client.get(f"https://ats.rippling.com/api/v1/board/{ident}/jobs", timeout=3.5)
            return r.status_code == 200, display_name, comp

        elif plat == "jazzhr":
            r = client.get(f"https://{ident}.applytojob.com", timeout=3.5)
            return r.status_code in (200, 301, 302), display_name, comp

        else:
            r = client.get(url, timeout=3.5)
            return r.status_code in (200, 301, 302), display_name, comp

    except Exception:
        return False, "", comp


def batch_insert_companies(batch: list[tuple[str, str, str, str]]) -> int:
    if not batch:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for attempt in range(5):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            cur = conn.cursor()
            for name, plat, ident, career_url in batch:
                try:
                    cur.execute("""
                        INSERT OR IGNORE INTO companies (name, ats_platform, identifier, career_url, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (name, plat, ident, career_url, now))
                    if cur.rowcount > 0:
                        inserted += 1
                except Exception:
                    pass
            conn.commit()
            conn.close()
            return inserted
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                time.sleep(0.5 * (attempt + 1))
            else:
                raise e
    return inserted


def main(target_add: int = 5000, max_workers: int = 60):
    print("=" * 68)
    print("🚀 MASSIVE MULTI-ATS VERIFICATION & INGESTION ENGINE")
    print(f"🎯 Target Goal:  +{target_add} NEW Verified Companies")
    print(f"⚙️ Concurrency:  {max_workers} Verification Threads")
    print("=" * 68)

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies")
    initial_total = cur.fetchone()[0]
    conn.close()

    print(f"\n📊 Initial Database Pool: {initial_total} companies")
    existing_keys, existing_urls = load_existing_db()

    candidates = gather_all_candidates(existing_keys, existing_urls)
    if not candidates:
        print("⚠️ No new candidates found.")
        return

    print(f"\n🧪 [Step 2/3] Actively Verifying Live Endpoints & Saving Unique Entries...")
    client = httpx.Client(
        timeout=3.8,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        limits=httpx.Limits(max_connections=max_workers, max_keepalive_connections=30)
    )

    verified_added = 0
    failed_val = 0
    batch = []
    start_t = time.time()
    total_cand = len(candidates)
    processed = 0
    platform_added = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futs = {executor.submit(verify_ats_endpoint, client, c): c for c in candidates}
        for fut in as_completed(futs):
            processed += 1
            try:
                is_valid, vname, comp = fut.result()
            except Exception:
                is_valid = False

            if is_valid:
                plat = comp["ats_platform"]
                ident = comp["identifier"]
                url = comp["career_url"]
                name = vname or comp["name"] or ident.split("|")[0].title()
                batch.append((name, plat, ident, url))
                platform_added[plat] = platform_added.get(plat, 0) + 1

                if len(batch) >= 50:
                    added = batch_insert_companies(batch)
                    verified_added += added
                    batch = []

                if verified_added % 250 == 0 and verified_added > 0:
                    elapsed = time.time() - start_t
                    rate = processed / elapsed if elapsed > 0 else 0
                    print(f"   ⭐ +{verified_added:5} Verified Added | {processed:5}/{total_cand} Scanned | {rate:4.1f} req/s")

                if verified_added >= target_add:
                    print(f"\n🎯 Target reached: +{verified_added} new verified companies added!")
                    try:
                        executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    break
            else:
                failed_val += 1

    if batch:
        verified_added += batch_insert_companies(batch)

    client.close()

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM companies")
    final_total = cur.fetchone()[0]
    cur.execute("SELECT ats_platform, COUNT(*) FROM companies GROUP BY ats_platform ORDER BY COUNT(*) DESC")
    final_breakdown = cur.fetchall()
    conn.close()

    elapsed = time.time() - start_t
    print("\n" + "=" * 68)
    print("✨ MASSIVE MULTI-ATS HARVEST COMPLETE")
    print(f"⏱️ Total Time Elapsed:    {elapsed:.1f} seconds")
    print(f"🔍 Candidates Scanned:   {processed}")
    print(f"❌ Inactive / 404:        {failed_val}")
    print(f"🎉 NEW Verified Added:   +{verified_added} companies")
    print(f"🏢 Total in Database:    {final_total} companies (was {initial_total})")
    print("\n📊 Final Database Breakdown by ATS Platform:")
    for plat, cnt in final_breakdown:
        print(f"   • {plat.upper():16}: {cnt:6} companies")
    print("=" * 68)


if __name__ == "__main__":
    t = 5000
    if len(sys.argv) > 1:
        try:
            t = int(sys.argv[1])
        except ValueError:
            pass
    main(target_add=t)

