"""
ats_scraper.py — High-Performance Direct ATS Scraper for Saved Companies.

Scrapes saved company career boards directly (Greenhouse, Lever, Ashby,
SmartRecruiters, Workday, BambooHR, Breezy HR, Recruitee, Workable, Paylocity,
Teamtailor, Pinpoint, Jobvite, iCIMS, Oracle Cloud, Keka, Personio, Rippling, JazzHR)
via public endpoints with zero Google dependency, zero CAPTCHAs, and zero rate limits.

Optimizations:
- Persistent HTTP Keep-Alive connection pooling via httpx.Client.
- High-concurrency worker pool (default 60 concurrent workers).
- Batch SQLite operations with WAL mode (reducing 100k fsync commits to batch transactions).
- Smart incremental scheduling (prioritizes never-scraped companies, skips recently checked).
- Support for Paylocity (6,600+ companies) and Teamtailor (2,400+ companies).
- Clean, non-blocking progress telemetry with live rate (companies/sec) and ETA.
"""
import sys
import os
import json
import re
import time
import argparse
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
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    import urllib.request
    import urllib.parse

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

try:
    from src.ranking.job_filter import clean_location_input
except ImportError:
    try:
        from ranking.job_filter import clean_location_input
    except ImportError:
        try:
            from job_filter import clean_location_input
        except ImportError:
            def clean_location_input(loc):
                return loc or ""

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
COMMON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def is_relevant_role(title: str) -> bool:
    """Filter to ensure we pull software, tech, and internship roles."""
    if not title:
        return False
    title_lower = title.lower()
    return any(kw in title_lower for kw in TECH_KEYWORDS)


# ──────────────────────────────────────────────
#  Platform-Specific ATS Fetchers (HTTPX + Pool)
# ──────────────────────────────────────────────

def fetch_greenhouse_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Greenhouse's public board API with HTML fallback."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for j in data.get("jobs", []):
                    results.append({
                        "title": j.get("title", "Unknown Title").strip(),
                        "url": j.get("absolute_url", ""),
                        "location": j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else ""
                    })
                return results
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
                for j in data.get("jobs", []):
                    results.append({
                        "title": j.get("title", "Unknown Title").strip(),
                        "url": j.get("absolute_url", ""),
                        "location": j.get("location", {}).get("name") if isinstance(j.get("location"), dict) else ""
                    })
                return results
    except Exception:
        pass

    # Fallback to boards.greenhouse.io HTML scrape if API fails
    return scrape_greenhouse_html(identifier, client=client)


def scrape_greenhouse_html(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fallback HTML parser for Greenhouse career pages."""
    url = f"https://boards.greenhouse.io/{identifier}"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
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


def fetch_lever_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Lever's public posting API."""
    url = f"https://api.lever.co/v0/postings/{identifier}"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                postings = resp.json()
                if isinstance(postings, list):
                    for p in postings:
                        results.append({
                            "title": p.get("text", "Unknown Title").strip(),
                            "url": p.get("hostedUrl", "").split("?")[0],
                            "location": p.get("categories", {}).get("location") if isinstance(p.get("categories"), dict) else ""
                        })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_ashby_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Ashby's public posting API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{identifier}"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for j in data.get("jobs", []):
                    results.append({
                        "title": j.get("title", "Unknown Title").strip(),
                        "url": j.get("jobUrl", "").split("?")[0],
                        "location": j.get("location", "")
                    })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_smartrecruiters_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via SmartRecruiters' public company postings API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("content", []):
                    jid = item.get("id")
                    results.append({
                        "title": item.get("name", "Unknown Title").strip(),
                        "url": f"https://jobs.smartrecruiters.com/{identifier}/{jid}",
                        "location": item.get("location", {}).get("city") if isinstance(item.get("location"), dict) else ""
                    })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_workday_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """
    Fetches jobs via Workday CXS JSON API.
    Identifier format: tenant|server|site
    """
    parts = identifier.split("|")
    if len(parts) < 3:
        return []
    tenant, server, site = parts[0], parts[1], parts[2]
    base_host = f"{tenant}.{server}.myworkdayjobs.com" if server else f"{tenant}.myworkdayjobs.com"
    url = f"https://{base_host}/wday/cxs/{tenant}/{site}/jobs"
    payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    results = []
    try:
        if client:
            resp = client.post(url, json=payload, headers={"Accept": "application/json"}, timeout=6.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("jobPostings", []):
                    ext_path = item.get("externalPath", "")
                    full_url = f"https://{base_host}/en-US/{site}{ext_path}"
                    results.append({
                        "title": item.get("title", "Unknown Title").strip(),
                        "url": full_url,
                        "location": item.get("locationsText", "")
                    })
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read().decode("utf-8"))
                for item in data.get("jobPostings", []):
                    ext_path = item.get("externalPath", "")
                    full_url = f"https://{base_host}/en-US/{site}{ext_path}"
                    results.append({
                        "title": item.get("title", "Unknown Title").strip(),
                        "url": full_url,
                        "location": item.get("locationsText", "")
                    })
    except Exception:
        pass
    return results


def fetch_bamboohr_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via BambooHR's careers list endpoint."""
    url = f"https://{identifier}.bamboohr.com/careers/list"
    results = []
    try:
        if client:
            resp = client.get(url, headers={"Accept": "application/json"}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for j in data.get("result", []):
                    jid = j.get("id")
                    results.append({
                        "title": j.get("jobOpeningName", "Unknown Title").strip(),
                        "url": f"https://{identifier}.bamboohr.com/careers/{jid}"
                    })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
                for j in data.get("result", []):
                    jid = j.get("id")
                    results.append({
                        "title": j.get("jobOpeningName", "Unknown Title").strip(),
                        "url": f"https://{identifier}.bamboohr.com/careers/{jid}"
                    })
    except Exception:
        pass
    return results


def fetch_breezy_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Breezy HR's JSON endpoint."""
    url = f"https://{identifier}.breezy.hr/json"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                items = resp.json()
                for item in items:
                    jid = item.get("id")
                    loc_dict = item.get("location") or {}
                    loc_name = loc_dict.get("name") or loc_dict.get("city") or ""
                    results.append({
                        "title": item.get("name", "Unknown Title").strip(),
                        "url": f"https://{identifier}.breezy.hr/p/{jid}",
                        "location": loc_name or ""
                    })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_recruitee_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Recruitee's public offers API."""
    url = f"https://{identifier}.recruitee.com/api/offers/"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("offers", []):
                    loc = item.get("location") or item.get("city") or ""
                    results.append({
                        "title": item.get("title", "Unknown Title").strip(),
                        "url": item.get("careers_url", ""),
                        "location": loc or ""
                    })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_workable_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Workable's public API."""
    url = f"https://apply.workable.com/api/v3/accounts/{identifier}/jobs"
    results = []
    try:
        if client:
            resp = client.post(url, json={}, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
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
        else:
            req = urllib.request.Request(url, data=b'{}', headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
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


def fetch_paylocity_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs from Paylocity career portal."""
    url = f"https://recruiting.paylocity.com/recruiting/jobs/All/{identifier}"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
            m = re.search(r'window\.pageData\s*=\s*({.*?});\s*</script>', html, re.DOTALL)
            if m:
                data = json.loads(m.group(1))
                for j in data.get("Jobs", []):
                    jid = j.get("JobId")
                    title = j.get("JobTitle", "Unknown Title").strip()
                    loc = j.get("LocationName", "")
                    if jid:
                        results.append({
                            "title": title,
                            "url": f"https://recruiting.paylocity.com/recruiting/jobs/Details/{jid}",
                            "location": loc or ""
                        })
    except Exception:
        pass
    return results


def fetch_teamtailor_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs from Teamtailor career portal."""
    url = f"https://{identifier}.teamtailor.com/jobs"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
            matches = re.findall(
                r'<a[^>]+href=[\"\'](https?://[^\s\"\']+/jobs/\d+-[^\s\"\']+)[\"\'][^>]*>(.*?)</a>',
                html, re.DOTALL
            )
            for raw_url, body in matches:
                clean_url = raw_url.split("?")[0]
                clean_title = re.sub(r'<[^>]+>', ' ', body).strip()
                clean_title = re.sub(r'\s+', ' ', clean_title)
                if not clean_title or len(clean_title) < 2:
                    slug = raw_url.split("/jobs/")[-1].split("?")[0]
                    clean_title = re.sub(r'^\d+-', '', slug).replace("-", " ").title()
                results.append({
                    "title": clean_title,
                    "url": clean_url
                })
    except Exception:
        pass
    return results


def fetch_pinpoint_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Pinpoint's public postings API."""
    url = f"https://{identifier}.pinpointhq.com/postings.json"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("data", []):
                    title = item.get("title", "Unknown Title").strip()
                    job_url = item.get("url", "")
                    loc = item.get("location", {}).get("name", "") if isinstance(item.get("location"), dict) else ""
                    if job_url:
                        results.append({
                            "title": title,
                            "url": job_url.split("?")[0],
                            "location": loc or ""
                        })
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))
                for item in data.get("data", []):
                    title = item.get("title", "Unknown Title").strip()
                    job_url = item.get("url", "")
                    loc = item.get("location", {}).get("name", "") if isinstance(item.get("location"), dict) else ""
                    if job_url:
                        results.append({
                            "title": title,
                            "url": job_url.split("?")[0],
                            "location": loc or ""
                        })
    except Exception:
        pass
    return results


def fetch_jobvite_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs from Jobvite company career page."""
    url = f"https://jobs.jobvite.com/{identifier}"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
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


def fetch_icims_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via iCIMS company career page iframe."""
    url = f"https://{identifier}.icims.com/jobs/search?pr=0&in_iframe=1"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=6.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
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


def fetch_oraclecloud_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Oracle Cloud HCM Candidate Experience REST API."""
    parts = identifier.split("|")
    if len(parts) < 3:
        return []
    tenant, region, site = parts[0], parts[1], parts[2]
    host = f"{tenant}.fa.{region}.oraclecloud.com" if region else f"{tenant}.fa.oraclecloud.com"
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site}"
    results = []
    try:
        if client:
            resp = client.get(url, headers={"Accept": "application/json"}, timeout=6.0)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    for req_item in item.get("requisitionList", []):
                        jid = req_item.get("Id")
                        title = req_item.get("Title", "Unknown Title").strip()
                        loc = req_item.get("PrimaryLocation", "")
                        full_url = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
                        results.append({"title": title, "url": full_url, "location": loc})
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
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


def fetch_keka_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Keka Hire career portal public JSON API."""
    career_url = f"https://{identifier}.keka.com/careers"
    results = []
    try:
        if client:
            resp = client.get(career_url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(career_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        guids = re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', html)
        if not guids:
            return results
        guid = guids[0]
        api_url = f"https://{identifier}.keka.com/careers/api/embedjobs/default/active/{guid}"

        if client:
            resp2 = client.get(api_url, timeout=5.0)
            data = resp2.json() if resp2.status_code == 200 else []
        else:
            req2 = urllib.request.Request(api_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req2, timeout=6) as r2:
                data = json.loads(r2.read().decode("utf-8"))

        for j in data:
            jid = j.get("id")
            if not jid:
                continue
            title = j.get("title", "Unknown Title").strip()
            locs = j.get("jobLocations", [])
            loc_str = ", ".join([l.get("city", "") for l in locs if isinstance(l, dict) and l.get("city")])
            full_url = f"https://{identifier}.keka.com/careers/jobdetails/{jid}"
            results.append({"title": title, "url": full_url, "location": loc_str})
    except Exception:
        pass
    return results


def fetch_personio_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Personio XML/HTML career board."""
    import xml.etree.ElementTree as ET
    results = []
    for domain in ["de", "com"]:
        url = f"https://{identifier}.jobs.personio.{domain}/xml"
        try:
            if client:
                resp = client.get(url, timeout=5.0)
                xml_data = resp.content if resp.status_code == 200 else None
            else:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=6) as r:
                    xml_data = r.read()

            if xml_data:
                root = ET.fromstring(xml_data)
                for pos in root.findall(".//position"):
                    jid = pos.findtext("id")
                    title = pos.findtext("name") or "Unknown Title"
                    office = pos.findtext("office") or ""
                    job_url = f"https://{identifier}.jobs.personio.{domain}/job/{jid}" if jid else f"https://{identifier}.jobs.personio.{domain}"
                    results.append({"title": title.strip(), "url": job_url, "location": office})
                if results:
                    return results
        except Exception:
            pass
    return results


def fetch_rippling_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via Rippling public board API."""
    url = f"https://ats.rippling.com/api/v1/board/{identifier}/jobs"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            data = resp.json() if resp.status_code == 200 else []
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = json.loads(r.read().decode("utf-8"))

        for j in data:
            jid = j.get("id") or j.get("uuid")
            title = j.get("name") or j.get("title") or "Unknown Title"
            loc = j.get("location") or ""
            job_url = f"https://ats.rippling.com/{identifier}/jobs/{jid}" if jid else f"https://ats.rippling.com/{identifier}/jobs"
            results.append({"title": title.strip(), "url": job_url, "location": str(loc)})
    except Exception:
        pass
    return results


def fetch_jazzhr_jobs(identifier: str, client: "httpx.Client" = None) -> list[dict]:
    """Fetches jobs via JazzHR company board."""
    url = f"https://{identifier}.applytojob.com"
    results = []
    try:
        if client:
            resp = client.get(url, timeout=5.0)
            html = resp.text if resp.status_code == 200 else ""
        else:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read().decode("utf-8", errors="ignore")

        if html:
            matches = re.findall(r'href=[\"\x27](https?://[^\s\"\x27]*applytojob\.com/apply/([a-zA-Z0-9]+)/[^\s\"\x27]*)[\"\x27][^>]*>([^<]+)</a>', html)
            for raw_url, jid, title in matches:
                results.append({"title": title.strip(), "url": raw_url.split("?")[0]})
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
    "paylocity": fetch_paylocity_jobs,
    "teamtailor": fetch_teamtailor_jobs,
    "pinpoint": fetch_pinpoint_jobs,
    "jobvite": fetch_jobvite_jobs,
    "icims": fetch_icims_jobs,
    "oraclecloud": fetch_oraclecloud_jobs,
    "keka": fetch_keka_jobs,
    "personio": fetch_personio_jobs,
    "rippling": fetch_rippling_jobs,
    "jazzhr": fetch_jazzhr_jobs,
}


def scrape_company_ats(company: dict, filter_tech: bool = False, client: "httpx.Client" = None) -> tuple[dict, list[dict]]:
    """
    Scrapes a single company's ATS board using the appropriate platform fetcher.
    Returns (company_dict, list_of_jobs).
    """
    platform = company.get("ats_platform", "").lower()
    identifier = company.get("identifier", "")
    fetcher = PLATFORM_FETCHERS.get(platform)

    if not fetcher:
        return company, []

    try:
        raw_jobs = fetcher(identifier, client=client)
    except Exception:
        raw_jobs = []

    # Filter for tech roles if explicitly requested; otherwise keep all unique jobs
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
#  Main High-Performance ATS Scraper Orchestrator
# ──────────────────────────────────────────────

def run_ats_scraper(filter_tech: bool = False,
                    max_workers: int = None,
                    max_age_hours: float = 12.0,
                    platform_filter: str = None,
                    limit: int = None,
                    force_all: bool = False) -> int:
    """
    Main orchestration function for direct ATS scraping.
    1. Loads target companies from SQLite with smart scheduling.
    2. Uses HTTP keep-alive connection pooling across concurrent workers.
    3. Flushes inserts and last_scraped timestamps in batched transactions.
    4. Provides clean telemetry with real-time speed (req/s) and ETA.
    """
    if max_workers is None:
        try:
            max_workers = int(os.environ.get("ATS_WORKERS", 60))
        except (ValueError, TypeError):
            max_workers = 60

    conn = get_conn()
    init_db(conn)

    # Deterministically resolve unparsed URLs in database (0 AI tokens)
    try:
        from ats_parser import batch_test_and_extract_companies
        batch_test_and_extract_companies(conn=conn, limit=200)
    except Exception as e:
        print(f"URL pre-parse note: {e}")

    effective_age = None if force_all else max_age_hours

    # Fetch companies sorted: never-scraped FIRST, then oldest-scraped
    companies = get_all_companies(
        conn=conn,
        max_age_hours=effective_age,
        ats_platform=platform_filter,
        limit=limit,
        order_by="CASE WHEN last_scraped_at IS NULL THEN 0 ELSE 1 END, last_scraped_at ASC, name ASC"
    )

    total_in_pool = get_company_count(conn)

    if not companies:
        print(f"✅ All {total_in_pool:,} saved companies are already up to date (scraped within last {max_age_hours}h).")
        print("💡 Tip: Pass --all or --hours 0 to force a full re-scrape of every company.")
        conn.close()
        return 0

    known_urls = get_all_urls(conn)

    print("\n" + "=" * 62)
    print(f"🏢 DIRECT ATS SCRAPER: Targeting {len(companies):,} companies (Pool: {total_in_pool:,})")
    print(f"⚡ Concurrency: {max_workers} workers | HTTP Keep-Alive: {'Enabled (HTTPX)' if HAS_HTTPX else 'urllib'}")
    if effective_age:
        print(f"🕒 Age Filter: Skipping companies scraped in last {effective_age}h (use --all to force)")
    if platform_filter:
        print(f"🎯 Platform Filter: {platform_filter.upper()}")
    print(f"📦 Existing URLs in database: {len(known_urls):,} (duplicates skipped)")
    print("=" * 62 + "\n")
    sys.stdout.flush()

    new_jobs_added = 0
    companies_processed = 0
    start_time = time.time()
    last_heartbeat_time = start_time

    # Batch buffers for high-speed writes
    BATCH_SIZE = 100
    job_buffer = []
    company_updates = []

    def flush_batch():
        nonlocal job_buffer, company_updates
        if job_buffer:
            conn.executemany("""
                INSERT OR IGNORE INTO jobs (url, title, status, rank, reason, added_at, details, company, ats_platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, job_buffer)
            job_buffer.clear()
        if company_updates:
            conn.executemany("""
                UPDATE companies
                SET last_scraped_at = ?, job_count = ?
                WHERE ats_platform = ? AND identifier = ?
            """, company_updates)
            company_updates.clear()
        conn.commit()

    # Create pooled HTTPX client if available
    http_client = None
    if HAS_HTTPX:
        http_client = httpx.Client(
            headers=COMMON_HEADERS,
            timeout=httpx.Timeout(timeout=5.0, connect=3.0, pool=5.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=max_workers * 2,
                max_keepalive_connections=max_workers,
                keepalive_expiry=30.0
            )
        )

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_company = {
                executor.submit(scrape_company_ats, comp, filter_tech, http_client): comp
                for comp in companies
            }

            for future in as_completed(future_to_company):
                comp = future_to_company[future]
                comp_name = comp.get("name") or comp.get("identifier")
                platform = comp.get("ats_platform")
                ident = comp.get("identifier")
                companies_processed += 1

                try:
                    _, jobs = future.result()
                except Exception:
                    jobs = []

                new_for_company = 0
                now_iso = datetime.now(timezone.utc).isoformat()

                for job in jobs:
                    url = (job.get("url") or "").strip()
                    title = (job.get("title") or "Unknown Title").strip()

                    if not url or url in known_urls:
                        continue

                    known_urls.add(url)
                    location = job.get("location", "")
                    clean_loc = clean_location_input(location)

                    initial_rank = "UNKNOWN"
                    initial_status = "New"
                    initial_reason = ""
                    details_blob = json.dumps({"location": clean_loc}) if clean_loc else None
                    job_title = f"{comp_name} - {title}" if comp_name not in title else title

                    job_buffer.append((
                        url,
                        job_title,
                        initial_status,
                        initial_rank,
                        initial_reason,
                        now_iso,
                        details_blob,
                        comp_name,
                        platform
                    ))
                    new_for_company += 1
                    new_jobs_added += 1

                company_updates.append((now_iso, len(jobs), platform, ident))

                # Flush to SQLite in high-speed batches
                if len(company_updates) >= BATCH_SIZE or len(job_buffer) >= BATCH_SIZE:
                    flush_batch()

                # Telemetry: show immediate log if company yielded new jobs
                if new_for_company > 0:
                    print(f"[{companies_processed:,}/{len(companies):,}] 🎯 {comp_name} ({platform}): {len(jobs)} jobs -> 💾 +{new_for_company} NEW jobs added!")
                    sys.stdout.flush()

                # Telemetry: periodic heartbeat every 200 companies or 5 seconds
                curr_t = time.time()
                if companies_processed % 200 == 0 or (curr_t - last_heartbeat_time) >= 5.0:
                    elapsed = curr_t - start_time
                    rate = companies_processed / elapsed if elapsed > 0 else 0.0
                    rem_companies = len(companies) - companies_processed
                    eta_sec = rem_companies / rate if rate > 0 else 0
                    eta_str = f"{int(eta_sec // 60)}m {int(eta_sec % 60):02d}s" if eta_sec >= 60 else f"{int(eta_sec)}s"
                    pct = (companies_processed / len(companies)) * 100.0

                    print(f"⚡ [PROGRESS] {companies_processed:,}/{len(companies):,} ({pct:.1f}%) | 💾 +{new_jobs_added:,} NEW | {rate:.1f} comp/s | ETA: {eta_str}")
                    sys.stdout.flush()
                    last_heartbeat_time = curr_t

        # Final commit for remaining buffered records
        flush_batch()

    finally:
        flush_batch()
        if http_client:
            http_client.close()
        conn.close()

    total_time = time.time() - start_time
    m, s = divmod(int(total_time), 60)
    avg_speed = companies_processed / total_time if total_time > 0 else 0.0

    print("\n" + "=" * 62)
    print(f"✨ ATS SCRAPE COMPLETE in {m}m {s:02d}s ({avg_speed:.1f} companies/sec)!")
    print(f"   📊 Companies scanned:  {companies_processed:,} / {len(companies):,}")
    print(f"   💾 New jobs added:     +{new_jobs_added:,}")
    print("=" * 62 + "\n")
    sys.stdout.flush()

    return new_jobs_added


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Direct ATS Scraper for Saved Companies")
    parser.add_argument("-w", "--workers", type=int, default=None, help="Number of concurrent worker threads (default: 60)")
    parser.add_argument("--hours", type=float, default=12.0, help="Skip companies scraped in the last N hours (default: 12.0)")
    parser.add_argument("--all", "--force", dest="force_all", action="store_true", help="Scrape all companies, ignoring last scraped timestamp")
    parser.add_argument("-n", "--limit", type=int, default=None, help="Limit maximum number of companies to scrape")
    parser.add_argument("-p", "--platform", type=str, default=None, help="Only scrape companies of a specific ATS platform")
    parser.add_argument("--filter-tech", action="store_true", help="Only keep tech/engineering/intern titles")
    parser.add_argument("--backfill", action="store_true", help="Extract companies from existing jobs in SQLite")

    args = parser.parse_args()

    if args.backfill:
        print("🔧 Running company backfill from existing database...")
        cnt = extract_companies_from_existing_jobs(max_workers=25)
        print(f"✅ Total companies saved: {cnt}")
    else:
        new_count = run_ats_scraper(
            filter_tech=args.filter_tech,
            max_workers=args.workers,
            max_age_hours=args.hours,
            platform_filter=args.platform,
            limit=args.limit,
            force_all=args.force_all
        )
        print(f"🎯 Total new jobs inserted: {new_count}")
