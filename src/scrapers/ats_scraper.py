"""
ats_scraper.py — High-Performance Direct ATS Scraper for Saved Companies.

Scrapes saved company career boards directly (Greenhouse, Lever, Ashby,
SmartRecruiters, Workday, BambooHR, Breezy HR, Recruitee, Workable, etc.)
via public endpoints with zero Google dependency, zero CAPTCHAs, and zero rate limits.
"""
import sys
import os
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
_RANKING_DIR = os.path.join(_SRC_DIR, "ranking")
_CORE_DIR = os.path.join(_SRC_DIR, "core")
for _p in [_SRC_DIR, _ROOT_DIR, _RANKING_DIR, _CORE_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.core.db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls,
        get_all_companies, get_company_count, update_company_scraped,
        extract_companies_from_existing_jobs
    )
    from src.scrapers.ats_parser import parse_ats_info, clean_company_name_from_title
except ImportError:
    from db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls,
        get_all_companies, get_company_count, update_company_scraped,
        extract_companies_from_existing_jobs
    )
    from ats_parser import parse_ats_info, clean_company_name_from_title

# Tech/Engineering/Intern filter keywords (matches typical tech candidate roles)
TECH_KEYWORDS = [
    "intern", "internship", "software", "engineer", "engineering",
    "developer", "backend", "frontend", "full stack", "fullstack",
    "systems", "c++", "python", "node", "react", "golang", "java",
    "machine learning", "ml", "ai", "data", "deep learning",
    "cloud", "devops", "platform", "infrastructure", "mobile",
    "android", "ios", "embedded", "firmware", "security", "researcher"
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def is_relevant_role(title: str) -> bool:
    """Filter to ensure we pull software, tech, and internship roles."""
    if not title:
        return False
    title_lower = title.lower()
    return any(kw in title_lower for kw in TECH_KEYWORDS)


# ──────────────────────────────────────────────
#  Platform-Specific ATS Fetchers
# ──────────────────────────────────────────────

def fetch_greenhouse_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Greenhouse's public board API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for j in data.get("jobs", []):
                results.append({
                    "title": j.get("title", "Unknown Title").strip(),
                    "url": j.get("absolute_url", ""),
                    "location": j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else ""
                })
    except Exception as e:
        # Fallback to boards.greenhouse.io scrape if API fails
        results = scrape_greenhouse_html(identifier)
    return results


def scrape_greenhouse_html(identifier: str) -> list[dict]:
    """Fallback HTML parser for Greenhouse career pages."""
    url = f"https://boards.greenhouse.io/{identifier}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
            # Pattern: <a href="/<identifier>/jobs/<id>">Title</a> or full URL
            matches = re.findall(
                rf'href=["\']((?:https?://(?:boards|job-boards)\.greenhouse\.io)?/{re.escape(identifier)}/jobs/(\d+)[^"\']*)["\'][^>]*>([^<]+)</a>',
                html, re.I
            )
            for href, jid, title in matches:
                full_url = href if href.startswith("http") else f"https://boards.greenhouse.io{href}"
                results.append({"title": title.strip(), "url": full_url.split("?")[0]})
    except Exception:
        pass
    return results


def fetch_lever_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Lever's public posting API."""
    url = f"https://api.lever.co/v0/postings/{identifier}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            postings = json.loads(r.read().decode("utf-8"))
            if isinstance(postings, list):
                for p in postings:
                    results.append({
                        "title": p.get("text", "Unknown Title").strip(),
                        "url": p.get("hostedUrl", "").split("?")[0],
                        "location": p.get("categories", {}).get("location") if isinstance(p.get("categories"), dict) else ""
                    })
    except Exception:
        pass
    return results


def fetch_ashby_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Ashby's public posting API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for j in data.get("jobs", []):
                results.append({
                    "title": j.get("title", "Unknown Title").strip(),
                    "url": j.get("jobUrl", "").split("?")[0],
                    "location": j.get("location", "")
                })
    except Exception:
        pass
    return results


def fetch_smartrecruiters_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via SmartRecruiters' public company postings API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for item in data.get("content", []):
                jid = item.get("id")
                results.append({
                    "title": item.get("name", "Unknown Title").strip(),
                    "url": f"https://jobs.smartrecruiters.com/{identifier}/{jid}",
                    "location": item.get("location", {}).get("city") if isinstance(item.get("location"), dict) else ""
                })
    except Exception:
        pass
    return results


def fetch_workday_jobs(identifier: str) -> list[dict]:
    """
    Fetches jobs via Workday CXS JSON API.
    Identifier format: tenant|server|site
    """
    parts = identifier.split("|")
    if len(parts) < 3:
        return []
    tenant, server, site = parts[0], parts[1], parts[2]
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = json.dumps({"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json"
    })
    results = []
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
            for item in data.get("jobPostings", []):
                ext_path = item.get("externalPath", "")
                full_url = f"https://{tenant}.{server}.myworkdayjobs.com/en-US/{site}{ext_path}"
                results.append({
                    "title": item.get("title", "Unknown Title").strip(),
                    "url": full_url,
                    "location": item.get("locationsText", "")
                })
    except Exception:
        pass
    return results


def fetch_bamboohr_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via BambooHR's careers list endpoint."""
    url = f"https://{identifier}.bamboohr.com/careers/list"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            # Format: {"result": [{"id": ..., "jobOpeningName": ...}]}
            for j in data.get("result", []):
                jid = j.get("id")
                results.append({
                    "title": j.get("jobOpeningName", "Unknown Title").strip(),
                    "url": f"https://{identifier}.bamboohr.com/careers/{jid}"
                })
    except Exception:
        pass
    return results


def fetch_breezy_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Breezy HR's JSON endpoint."""
    url = f"https://{identifier}.breezy.hr/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            items = json.loads(r.read().decode("utf-8"))
            for item in items:
                jid = item.get("id")
                loc_dict = item.get("location") or {}
                loc_name = loc_dict.get("name") or loc_dict.get("city") or ""
                results.append({
                    "title": item.get("name", "Unknown Title").strip(),
                    "url": f"https://{identifier}.breezy.hr/p/{jid}",
                    "location": loc_name or ""
                })
    except Exception:
        pass
    return results


def fetch_recruitee_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Recruitee's public offers API."""
    url = f"https://{identifier}.recruitee.com/api/offers/"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for item in data.get("offers", []):
                loc = item.get("location") or item.get("city") or ""
                results.append({
                    "title": item.get("title", "Unknown Title").strip(),
                    "url": item.get("careers_url", ""),
                    "location": loc or ""
                })
    except Exception:
        pass
    return results


def fetch_workable_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Workable's public API."""
    url = f"https://apply.workable.com/api/v3/accounts/{identifier}/jobs"
    payload = b'{}'
    req = urllib.request.Request(url, data=payload, headers={
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json"
    })
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for item in data.get("results", []):
                shortcode = item.get("shortcode", "")
                loc_dict = item.get("location") or {}
                city = loc_dict.get("city", "") if isinstance(loc_dict, dict) else ""
                country = loc_dict.get("country", "") if isinstance(loc_dict, dict) else ""
                loc = f"{city}, {country}".strip(", ")
                results.append({
                    "title": item.get("title", "Unknown Title").strip(),
                    "url": f"https://apply.workable.com/{identifier}/j/{shortcode}/",
                    "location": loc or ""
                })
    except Exception:
        pass
    return results


def fetch_jobvite_jobs(identifier: str) -> list[dict]:
    """Fetches jobs from Jobvite company career page."""
    url = f"https://jobs.jobvite.com/{identifier}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", errors="ignore")
            matches = re.findall(r'href=[\"\x27](/[^/]+/job/([a-zA-Z0-9]+))[\"\x27][^>]*>([^<]+)</a>', html)
            for path, jid, title in matches:
                clean_t = title.strip()
                if clean_t and not clean_t.lower().startswith("apply") and not clean_t.lower().startswith("view"):
                    results.append({
                        "title": clean_t,
                        "url": f"https://jobs.jobvite.com{path}"
                    })
    except Exception:
        pass
    return results


def fetch_icims_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via iCIMS company career page iframe."""
    url = f"https://{identifier}.icims.com/jobs/search?pr=0&in_iframe=1"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read().decode("utf-8", errors="ignore")
            matches = re.findall(
                r'href=[\"\x27](https?://[^\s\"\x27]+/jobs/(\d+)/[^\s\"\x27]+)[\"\x27][^>]*title=[\"\x27]([^\"]+)[\"\x27]',
                html
            )
            for raw_url, jid, title in matches:
                clean_url = raw_url.split("?")[0]
                clean_title = re.sub(r'^\d+\s*[-–]\s*', '', title).strip()
                results.append({
                    "title": clean_title,
                    "url": clean_url
                })
    except Exception:
        pass
    return results


def fetch_oraclecloud_jobs(identifier: str) -> list[dict]:
    """Fetches jobs via Oracle Cloud HCM Candidate Experience REST API."""
    parts = identifier.split("|")
    if len(parts) < 3:
        return []
    tenant, region, site = parts[0], parts[1], parts[2]
    host = f"{tenant}.fa.{region}.oraclecloud.com" if region else f"{tenant}.fa.oraclecloud.com"
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
            for item in data.get("items", []):
                for req_item in item.get("requisitionList", []):
                    jid = req_item.get("Id")
                    title = req_item.get("Title", "Unknown Title").strip()
                    loc = req_item.get("PrimaryLocation", "")
                    full_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
                    results.append({"title": title, "url": full_url, "location": loc})
    except Exception:
        pass
    return results


# Mapping of ATS platform name to fetch function
PLATFORM_FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
    "ashby": fetch_ashby_jobs,
    "smartrecruiters": fetch_smartrecruiters_jobs,
    "workday": fetch_workday_jobs,
    "bamboohr": fetch_bamboohr_jobs,
    "breezy": fetch_breezy_jobs,
    "recruitee": fetch_recruitee_jobs,
    "workable": fetch_workable_jobs,
    "jobvite": fetch_jobvite_jobs,
    "icims": fetch_icims_jobs,
    "oraclecloud": fetch_oraclecloud_jobs,
}


def scrape_company_ats(company: dict, filter_tech: bool = True) -> tuple[dict, list[dict]]:
    """
    Scrapes a single company's ATS board using the appropriate platform fetcher.
    Returns (company_dict, list_of_jobs).
    """
    platform = company.get("ats_platform", "").lower()
    identifier = company.get("identifier", "")
    fetcher = PLATFORM_FETCHERS.get(platform)

    if not fetcher:
        return company, []

    raw_jobs = fetcher(identifier)

    # Filter for tech / engineering / intern roles
    filtered_jobs = []
    for j in raw_jobs:
        title = j.get("title", "")
        url = j.get("url", "")
        if not url:
            continue
        if not filter_tech or is_relevant_role(title):
            filtered_jobs.append(j)

    return company, filtered_jobs


# ──────────────────────────────────────────────
#  Main ATS Scraper Runner
# ──────────────────────────────────────────────

def run_ats_scraper(filter_tech: bool = True, max_workers: int = 15) -> int:
    """
    Main orchestration function for direct ATS scraping.
    1. Reads saved companies from SQLite.
    2. Auto-seeds companies from existing DB if empty.
    3. Scrapes all saved companies concurrently via public ATS endpoints.
    4. Inserts new jobs into jobs_db.sqlite with status='New', rank='UNKNOWN'.
    5. Returns count of new jobs added.
    """
    conn = get_conn()
    init_db(conn)

    # Deterministically test and resolve unparsed URLs in database (0 AI tokens)
    try:
        from ats_parser import batch_test_and_extract_companies
        batch_test_and_extract_companies(conn=conn, limit=200)
    except Exception as e:
        print(f"URL pre-parse note: {e}")

    total_companies = get_company_count(conn)
    companies = get_all_companies(conn)
    if not companies:
        print("⚠️ No companies available to scrape.")
        conn.close()
        return 0

    known_urls = get_all_urls(conn)
    print(f"\n" + "="*55)
    print(f"🏢 DIRECT ATS SCRAPER: Targeting {len(companies)} saved companies")
    print(f"📦 {len(known_urls)} existing jobs in database (duplicates will be skipped)")
    print(f"="*55 + "\n")

    new_jobs_added = 0
    companies_processed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_company = {
            executor.submit(scrape_company_ats, comp, filter_tech): comp
            for comp in companies
        }

        for future in as_completed(future_to_company):
            comp = future_to_company[future]
            comp_name = comp.get("name") or comp.get("identifier")
            platform = comp.get("ats_platform")
            companies_processed += 1

            try:
                _, jobs = future.result()
            except Exception as e:
                print(f"[{companies_processed}/{len(companies)}] ⚠️ {comp_name} ({platform}): Error: {e}")
                continue

            new_for_company = 0
            now_iso = datetime.now(timezone.utc).isoformat()

            for job in jobs:
                url = job.get("url", "").strip()
                title = job.get("title", "Unknown Title").strip()

                if not url or url in known_urls:
                    continue

                known_urls.add(url)
                location = job.get("location", "")
                
                # Smart 3-tier deterministic categorization (Tier 1 HIGH, Tier 2 SWE, Tier 3 IGNORE)
                try:
                    from src.ranking.job_filter import categorize_job_tier, clean_location_input
                except ImportError:
                    try:
                        from ranking.job_filter import categorize_job_tier, clean_location_input
                    except ImportError:
                        from job_filter import categorize_job_tier, clean_location_input
                clean_loc = clean_location_input(location)
                tier_res = categorize_job_tier(title=title, location=clean_loc)
                initial_rank = tier_res["rank"]
                initial_status = tier_res["status"]
                initial_reason = tier_res["reason"]
                details_blob = {"location": clean_loc} if clean_loc else None

                inserted = insert_or_ignore_job(
                    url=url,
                    title=f"{comp_name} - {title}" if comp_name not in title else title,
                    conn=conn,
                    status=initial_status,
                    rank=initial_rank,
                    reason=initial_reason,
                    added_at=now_iso,
                    company=comp_name,
                    ats_platform=platform,
                    details=details_blob
                )
                if inserted:
                    new_for_company += 1
                    new_jobs_added += 1

            # Update company last scraped info
            update_company_scraped(platform, comp.get("identifier"), len(jobs), conn=conn)
            conn.commit()

            if new_for_company > 0:
                print(f"[{companies_processed}/{len(companies)}] 🎯 {comp_name} ({platform}): {len(jobs)} active jobs -> 💾 +{new_for_company} NEW jobs added!")
            else:
                print(f"[{companies_processed}/{len(companies)}] ℹ️ {comp_name} ({platform}): {len(jobs)} active jobs (0 new).")

    conn.close()
    print("\n" + "="*55)
    print(f"✨ ATS SCRAPE COMPLETE: {new_jobs_added} new jobs added across {companies_processed} companies!")
    print("="*55 + "\n")
    return new_jobs_added


if __name__ == "__main__":
    if "--backfill" in sys.argv:
        print("🔧 Running company backfill from existing database...")
        cnt = extract_companies_from_existing_jobs(max_workers=25)
        print(f"✅ Total companies saved: {cnt}")
    else:
        new_count = run_ats_scraper()
        print(f"🎯 Total new jobs inserted: {new_count}")
