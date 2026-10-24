#!/usr/bin/env python3
"""
keka_scraper.py — Dedicated High-Yield Keka & Indian Startup Scraper.

Standalone discovery and scraping engine for Keka Hire career portals and
top Indian tech startups. Discovers company URLs across passive DNS, search
engines, and curated seed banks, validates active endpoints, saves them into
the SQLite companies database, and ingests active job postings.

Can be run independently:
    python src/scrapers/keka_scraper.py
    python src/scrapers/keka_scraper.py --discover-only
    python src/scrapers/keka_scraper.py --scrape-jobs --workers 30
"""

import sys
import os
import re
import json
import time
import argparse
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.core.db import (
        get_conn, init_db, save_company, get_company_count,
        insert_or_ignore_job, get_all_urls, update_company_scraped
    )
    from src.ranking.job_filter import categorize_job_tier, clean_location_input
    from src.scrapers.ats_scraper import fetch_keka_jobs, PLATFORM_FETCHERS
except ImportError:
    from db import (
        get_conn, init_db, save_company, get_company_count,
        insert_or_ignore_job, get_all_urls, update_company_scraped
    )
    from job_filter import categorize_job_tier, clean_location_input
    from ats_scraper import fetch_keka_jobs, PLATFORM_FETCHERS

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────────────────
# Curated Indian Startups & High-Volume Tech Companies on Major ATS
# ─────────────────────────────────────────────────────────────────────────────
CURATED_INDIAN_STARTUPS = [
    # Top Indian Product Companies on Lever
    ("Paytm", "lever", "paytm", "https://jobs.lever.co/paytm"),
    ("Meesho", "lever", "meesho", "https://jobs.lever.co/meesho"),
    ("CRED", "lever", "cred", "https://jobs.lever.co/cred"),
    ("Porter", "lever", "porter", "https://jobs.lever.co/porter"),
    ("Pocket FM", "lever", "pocketfm", "https://jobs.lever.co/pocketfm"),

    # Top Indian Product Companies on Greenhouse
    ("InMobi", "greenhouse", "inmobi", "https://boards.greenhouse.io/inmobi"),
    ("Zenoti", "greenhouse", "zenoti", "https://boards.greenhouse.io/zenoti"),
    ("DevRev", "greenhouse", "devrev", "https://boards.greenhouse.io/devrev"),
    ("Druva", "greenhouse", "druva", "https://boards.greenhouse.io/druva"),
    ("Slice", "greenhouse", "slice", "https://boards.greenhouse.io/slice"),
    ("Groww", "greenhouse", "groww", "https://boards.greenhouse.io/groww"),

    # Top Indian Product Companies on Ashby
    ("SigNoz", "ashby", "signoz", "https://jobs.ashbyhq.com/signoz"),
    ("Atlan", "ashby", "atlan", "https://jobs.ashbyhq.com/atlan"),
]

# Curated High-Value Keka subdomains (Indian tech firms, startups & IT leaders)
CURATED_KEKA_CANDIDATES = [
    "stayvista", "furlenco", "systango", "adda247", "10decoders", "tohands",
    "lumel", "heaptrace", "workindia", "auracloud", "pramana", "cloudsufi",
    "comprinno", "ketto", "vedam", "acciojob", "accredian", "adlift", "aecor",
    "achiralabs", "acuitilabs", "adaan", "adit", "admaren", "adscholars",
    "aeroqube", "aesc", "100", "12thwonder", "1to1help", "3scsolution", "7edge",
    "8views", "a1apps", "abitainnovation", "academian", "acaindia", "acenzo",
    "bridgestone1", "byld", "coditude", "enterpriseminds", "evolve", "express",
    "fynd", "idream", "ihl", "in2itebs", "neuralgo", "qodeadvisors", "saarthee",
    "thewholetruthfoods", "unitedgroup", "codingninjas", "afreespace",
    "ahamhousing", "ahinternational", "aidenai", "aipl", "tecocraftinfusion",
    "nalashaasolutions", "marut", "accurio", "nreach", "zanskar", "pointo",
    "yellowclass", "geekyants", "hashnode", "appinventiv", "mindfire",
    "rentomojo", "cureya", "invictus", "bizongo", "peppercontent", "talent500",
    "cutshort", "cuemath", "scaler", "interviewbit", "boardinfinity", "almabetter",
    "kodo", "velocity", "jaro", "upgrad", "greatlearning", "intellipaat",
    "kraftshala", "growthschool", "infurnia", "nobroker", "colive", "stanza",
    "edureka", "masaischool", "newtonschool", "internshala", "stoa"
]

KEKA_DISCOVERY_QUERIES = [
    "site:keka.com/careers",
    "site:keka.com/careers intern",
    "site:keka.com/careers software",
    "site:keka.com/careers developer",
    "site:keka.com/careers engineer",
    "site:keka.com/careers bangalore",
    "site:keka.com/careers hyderabad",
    "site:keka.com/careers pune",
    "site:keka.com/careers mumbai",
    "site:keka.com/careers gurgaon",
    "site:keka.com/careers noida",
    "site:keka.com/careers chennai",
    "site:keka.com/careers fresher",
    "site:keka.com/careers internship",
    "site:keka.com/careers technologies",
    "site:keka.com/careers solutions",
    "site:keka.com/careers private limited",
    "site:keka.com/careers labs",
    "site:keka.com/careers fintech",
    "site:keka.com/careers ai"
]

IGNORE_SUBDOMAINS = {
    "www", "mail", "app", "cdn", "signup", "login", "staging", "staging4",
    "demo", "c", "qc", "diagnose", "referral", "apidocs", "status",
    "developers", "explore", "help", "kafe", "go", "launchpad", "a",
    "attendance", "blog", "payroll"
}


# ─────────────────────────────────────────────────────────────────────────────
# Discovery Sources
# ─────────────────────────────────────────────────────────────────────────────
def fetch_from_hackertarget() -> set[str]:
    """Fetch subdomains from HackerTarget hostsearch API."""
    subs = set()
    url = "https://api.hackertarget.com/hostsearch/?q=keka.com"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            for line in resp.read().decode("utf-8", errors="ignore").splitlines():
                host = line.split(",")[0].strip().lower()
                if host.endswith(".keka.com"):
                    sub = host.replace(".keka.com", "").split(".")[-1]
                    if sub and sub not in IGNORE_SUBDOMAINS and len(sub) > 2:
                        subs.add(sub)
    except Exception as e:
        print(f"   [Recon] HackerTarget notice: {e}")
    return subs


def fetch_from_subdomain_center() -> set[str]:
    """Fetch subdomains from Subdomain.center API."""
    subs = set()
    url = "https://api.subdomain.center/?domain=keka.com"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            for item in json.loads(resp.read().decode("utf-8", errors="ignore")):
                host = item.strip().lower()
                if host.endswith(".keka.com"):
                    sub = host.replace(".keka.com", "").split(".")[-1]
                    if sub and sub not in IGNORE_SUBDOMAINS and len(sub) > 2:
                        subs.add(sub)
    except Exception as e:
        print(f"   [Recon] Subdomain.center notice: {e}")
    return subs


def fetch_from_rapiddns() -> set[str]:
    """Fetch subdomains from RapidDNS."""
    subs = set()
    url = "https://rapiddns.io/subdomain/keka.com?full=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            for m in re.findall(r"<td>([a-zA-Z0-9\-]+\.keka\.com)</td>", resp.read().decode("utf-8", errors="ignore")):
                sub = m.lower().replace(".keka.com", "").split(".")[-1]
                if sub and sub not in IGNORE_SUBDOMAINS and len(sub) > 2:
                    subs.add(sub)
    except Exception as e:
        print(f"   [Recon] RapidDNS notice: {e}")
    return subs


def fetch_from_duckduckgo(max_queries: int = 15) -> set[str]:
    """Fetch Keka subdomains using DuckDuckGo search queries."""
    subs = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
    }
    for q in KEKA_DISCOVERY_QUERIES[:max_queries]:
        url = "https://html.duckduckgo.com/html/"
        data = urllib.parse.urlencode({"q": q}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(r"https?://([a-zA-Z0-9\-]+)\.keka\.com", html)
                for m in matches:
                    s = m.lower().strip()
                    if s not in IGNORE_SUBDOMAINS and len(s) > 2:
                        subs.add(s)
        except Exception:
            pass
        time.sleep(1.0)
    return subs


# ─────────────────────────────────────────────────────────────────────────────
# Live Validation & Name Extraction
# ─────────────────────────────────────────────────────────────────────────────
def clean_company_name(raw_title: str, fallback_ident: str) -> str:
    """Normalize extracted HTML page title into a clean company name."""
    if not raw_title:
        return fallback_ident.title()
    title = re.sub(r"(?i)careers\s+at\s+careers\s+at\s+", "", raw_title)
    title = re.sub(r"(?i)careers\s+at\s+", "", title)
    title = re.sub(r"(?i)\s*\|\s*careers.*", "", title)
    title = re.sub(r"(?i)\s*-\s*careers.*", "", title)
    title = re.sub(r"(?i)\bcareers\b", "", title)
    title = re.sub(r"(?i)\bprivate limited\b", "", title)
    title = re.sub(r"(?i)\bpvt\.?\s*ltd\.?\b", "", title)
    title = title.strip(" -|:,\t\n")
    if not title or len(title) < 2 or any(b in title.lower() for b in ["404", "error", "not found", "forbidden", "tenant"]):
        return fallback_ident.title()
    return title


def validate_keka_portal(subdomain: str) -> tuple[str, bool, str, str]:
    """
    Test if a Keka career portal is active and extract company name and GUID.
    Returns (subdomain, is_valid, company_name, guid).
    """
    url = f"https://{subdomain}.keka.com/careers"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                html = resp.read().decode("utf-8", errors="ignore")
                guids = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", html)
                title_m = re.search(r"<title>(.*?)</title>", html, re.I)
                raw_title = title_m.group(1) if title_m else ""
                clean_name = clean_company_name(raw_title, subdomain)
                # Ensure it's not a dummy error page
                if guids and not any(bad in clean_name.lower() for bad in ["invalid", "forbidden", "error", "404"]):
                    return subdomain, True, clean_name, guids[0]
    except Exception:
        pass
    return subdomain, False, "", ""


# ─────────────────────────────────────────────────────────────────────────────
# Main Scraper Execution
# ─────────────────────────────────────────────────────────────────────────────
def run_keka_discovery_and_scrape(
    discover_only: bool = False,
    max_workers: int = 25,
    scrape_jobs: bool = True
) -> dict:
    """
    Main entry point:
    1. Collects candidates across multiple sources and seed banks.
    2. Concurrently validates active Keka portals.
    3. Saves discovered companies into SQLite jobs_db.sqlite.
    4. Concurrently scrapes active job postings for all Indian tech companies.
    """
    print("\n" + "=" * 65)
    print("🇮🇳 KEKA & INDIAN TECH COMPANY DISCOVERY ENGINE")
    print("=" * 65)

    conn = get_conn()
    init_db(conn)

    # 1. Gather all candidate subdomains
    candidates = set(CURATED_KEKA_CANDIDATES)
    print(f"\n📡 Step 1/4: Gathering candidate subdomains from online reconnaissance...")

    print("   🔍 Checking HackerTarget passive DNS...")
    ht_subs = fetch_from_hackertarget()
    candidates.update(ht_subs)
    print(f"      + Found {len(ht_subs)} subdomains from HackerTarget")

    print("   🔍 Checking Subdomain.center...")
    sc_subs = fetch_from_subdomain_center()
    candidates.update(sc_subs)
    print(f"      + Found {len(sc_subs)} subdomains from Subdomain.center")

    print("   🔍 Checking RapidDNS...")
    rd_subs = fetch_from_rapiddns()
    candidates.update(rd_subs)
    print(f"      + Found {len(rd_subs)} subdomains from RapidDNS")

    print("   🔍 Querying DuckDuckGo dragnet...")
    ddg_subs = fetch_from_duckduckgo(max_queries=12)
    candidates.update(ddg_subs)
    print(f"      + Found {len(ddg_subs)} subdomains from DuckDuckGo")

    valid_candidates = sorted(list({s for s in candidates if s not in IGNORE_SUBDOMAINS and len(s) > 2}))
    print(f"\n📊 Total unique candidate portals to validate: {len(valid_candidates)}")

    # 2. Concurrently validate active portals
    print(f"\n⚡ Step 2/4: Concurrently validating active Keka portals ({max_workers} threads)...")
    active_keka = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sub = {executor.submit(validate_keka_portal, sub): sub for sub in valid_candidates}
        for future in as_completed(future_to_sub):
            sub, is_ok, name, guid = future.result()
            if is_ok:
                active_keka.append((name, "keka", sub, f"https://{sub}.keka.com/careers", guid))

    print(f"✅ Found {len(active_keka)} verified, active Keka company career portals!")

    # 3. Add to Database
    print(f"\n💾 Step 3/4: Registering companies into SQLite companies pool...")
    companies_to_register = list(active_keka)

    # Also register top Indian tech companies on Lever/Greenhouse/Ashby
    for name, plat, ident, url in CURATED_INDIAN_STARTUPS:
        companies_to_register.append((name, plat, ident, url, None))

    new_companies_added = 0
    for name, plat, ident, url, _ in companies_to_register:
        if save_company(name=name, ats_platform=plat, identifier=ident, career_url=url, conn=conn):
            new_companies_added += 1

    total_companies = get_company_count(conn)
    print(f"✨ Registered +{new_companies_added} NEW Indian companies in SQLite! Total pool: {total_companies} companies.")

    if discover_only or not scrape_jobs:
        conn.close()
        print("\n🏁 Discovery phase complete! (Skipping job scraping as requested).")
        return {
            "new_companies": new_companies_added,
            "total_companies": total_companies,
            "new_jobs": 0
        }

    # 4. Scrape active jobs from discovered companies
    print(f"\n🚀 Step 4/4: Ingesting active jobs from discovered Indian tech companies...")
    known_urls = get_all_urls(conn)
    print(f"📦 Existing jobs in DB: {len(known_urls)} (duplicates skipped automatically)")

    all_target_companies = []
    # Query all active Keka companies + Indian startups
    rows = conn.execute("""
        SELECT id, name, ats_platform, identifier, career_url
        FROM companies
        WHERE ats_platform = 'keka'
           OR identifier IN ('paytm', 'meesho', 'cred', 'porter', 'pocketfm', 'inmobi', 'zenoti', 'devrev', 'druva', 'slice', 'groww', 'signoz', 'atlan')
        ORDER BY name ASC
    """).fetchall()
    all_target_companies = [dict(r) for r in rows]

    print(f"🎯 Target set: {len(all_target_companies)} Indian companies to scrape")

    def scrape_single_company(comp):
        plat = comp.get("ats_platform")
        ident = comp.get("identifier")
        fetcher = PLATFORM_FETCHERS.get(plat)
        if not fetcher:
            return comp, []
        try:
            jobs = fetcher(ident)
            return comp, jobs
        except Exception:
            return comp, []

    new_jobs_added = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    processed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_comp = {executor.submit(scrape_single_company, c): c for c in all_target_companies}
        for future in as_completed(future_to_comp):
            processed_count += 1
            comp, jobs = future.result()
            comp_name = comp.get("name") or comp.get("identifier")
            plat = comp.get("ats_platform")

            new_for_company = 0
            for j in jobs:
                url = j.get("url")
                title = j.get("title", "Untitled Role").strip()
                if not url or url in known_urls:
                    continue
                known_urls.add(url)

                loc = clean_location_input(j.get("location", ""))
                tier_res = categorize_job_tier(title=title, location=loc)

                tier_mode = os.environ.get("SCRAPER_TIER_MODE", "all").lower()
                if ("--tier1-only" in sys.argv or tier_mode in ("tier1", "tier1_only")) and tier_res.get("tier") != 1:
                    continue

                details = {"location": loc} if loc else None

                inserted = insert_or_ignore_job(
                    url=url,
                    title=f"{comp_name} - {title}" if comp_name not in title else title,
                    conn=conn,
                    status=tier_res["status"],
                    rank=tier_res["rank"],
                    reason=tier_res["reason"],
                    added_at=now_iso,
                    company=comp_name,
                    ats_platform=plat,
                    details=details
                )
                if inserted:
                    new_for_company += 1
                    new_jobs_added += 1

            update_company_scraped(plat, comp.get("identifier"), len(jobs), conn=conn)
            conn.commit()

            if new_for_company > 0:
                print(f"[{processed_count}/{len(all_target_companies)}] 🎯 {comp_name} ({plat}): {len(jobs)} active jobs -> 💾 +{new_for_company} NEW tech jobs added!")
            elif len(jobs) > 0:
                print(f"[{processed_count}/{len(all_target_companies)}] ℹ️ {comp_name} ({plat}): {len(jobs)} jobs (0 new).")

    conn.close()
    print("\n" + "=" * 65)
    print(f"🎉 SCRAPE COMPLETE: +{new_companies_added} companies registered | +{new_jobs_added} NEW jobs ingested!")
    print(f"🏢 Total company pool in database: {total_companies}")
    print("=" * 65 + "\n")

    return {
        "new_companies": new_companies_added,
        "total_companies": total_companies,
        "new_jobs": new_jobs_added
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dedicated Keka & Indian Startup Scraper")
    parser.add_argument("--discover-only", action="store_true", help="Only discover and save companies, don't scrape jobs")
    parser.add_argument("--no-jobs", action="store_true", help="Do not scrape jobs after discovery")
    parser.add_argument("--tier1-only", action="store_true", help="Only ingest Tier 1 (Early Career / Intern / High Priority) jobs")
    parser.add_argument("--workers", type=int, default=25, help="Worker threads concurrency (default 25)")
    args = parser.parse_args()

    if args.tier1_only:
        os.environ["SCRAPER_TIER_MODE"] = "tier1"

    run_keka_discovery_and_scrape(
        discover_only=args.discover_only or args.no_jobs,
        max_workers=args.workers,
        scrape_jobs=not (args.discover_only or args.no_jobs)
    )
