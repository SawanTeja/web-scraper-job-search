"""
ats_parser.py — ATS Platform detection, URL resolution, and company parsing.
Handles major ATS platforms: Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, Workable, BambooHR, Breezy HR, Recruitee, Jobvite, etc.
"""
import sys
import os
import re
import urllib.parse
import urllib.request

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Supported ATS platform definitions and patterns
ATS_PATTERNS = [
    # Greenhouse: boards.greenhouse.io/<company> or job-boards.greenhouse.io/<company>
    (
        "greenhouse",
        re.compile(r"https?://(?:boards|job-boards|boards\.eu)\.greenhouse\.io/([^/?#]+)(?:/jobs/(\d+))?", re.I),
        lambda m: {
            "ats_platform": "greenhouse",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://boards.greenhouse.io/{m.group(1).lower()}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Lever: jobs.lever.co/<company>/<job_id>
    (
        "lever",
        re.compile(r"https?://jobs\.lever\.co/([^/?#]+)(?:/([a-f0-9\-]+))?", re.I),
        lambda m: {
            "ats_platform": "lever",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://jobs.lever.co/{m.group(1).lower()}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Ashby: jobs.ashbyhq.com/<company>/<job_id>
    (
        "ashby",
        re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)(?:/([a-f0-9\-]+))?", re.I),
        lambda m: {
            "ats_platform": "ashby",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://jobs.ashbyhq.com/{m.group(1).lower()}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # SmartRecruiters: jobs.smartrecruiters.com/<company>/<job_id>
    (
        "smartrecruiters",
        re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)(?:/([a-zA-Z0-9\-]+))?", re.I),
        lambda m: {
            "ats_platform": "smartrecruiters",
            "identifier": m.group(1),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://jobs.smartrecruiters.com/{m.group(1)}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Workday: <tenant>.<server>.myworkdayjobs.com/(?:en-US/)?<site>...
    (
        "workday",
        re.compile(r"https?://([^.]+)\.([^.]+)\.myworkdayjobs\.com/(?:[a-zA-Z\-]+/)?([^/?#]+)", re.I),
        lambda m: {
            "ats_platform": "workday",
            "identifier": f"{m.group(1)}|{m.group(2)}|{m.group(3)}",
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com/{m.group(3)}",
            "tenant": m.group(1),
            "server": m.group(2),
            "site": m.group(3)
        }
    ),
    # BambooHR: <company>.bamboohr.com/careers/<job_id>
    (
        "bamboohr",
        re.compile(r"https?://([^.]+)\.bamboohr\.com/(?:careers|jobs)(?:/(\d+))?", re.I),
        lambda m: {
            "ats_platform": "bamboohr",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://{m.group(1).lower()}.bamboohr.com/careers",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Breezy HR: <company>.breezy.hr/p/<job_id>
    (
        "breezy",
        re.compile(r"https?://([^.]+)\.breezy\.hr(?:/p/([a-zA-Z0-9]+))?", re.I),
        lambda m: {
            "ats_platform": "breezy",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://{m.group(1).lower()}.breezy.hr",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Recruitee: <company>.recruitee.com/o/<job_id>
    (
        "recruitee",
        re.compile(r"https?://([^.]+)\.recruitee\.com(?:/o/([a-zA-Z0-9\-]+))?", re.I),
        lambda m: {
            "ats_platform": "recruitee",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://{m.group(1).lower()}.recruitee.com",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Jobvite: jobs.jobvite.com/<company>/job/<job_id>
    (
        "jobvite",
        re.compile(r"https?://jobs\.jobvite\.com/([^/?#]+)(?:/job/([a-zA-Z0-9]+))?", re.I),
        lambda m: {
            "ats_platform": "jobvite",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://jobs.jobvite.com/{m.group(1).lower()}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
    # Workable: apply.workable.com/<company>/j/<job_id>
    (
        "workable",
        re.compile(r"https?://apply\.workable\.com/([^/?#]+)(?:/j/([a-zA-Z0-9]+))?", re.I),
        lambda m: {
            "ats_platform": "workable",
            "identifier": m.group(1).lower(),
            "company_name": m.group(1).replace("-", " ").replace("_", " ").title(),
            "career_url": f"https://apply.workable.com/{m.group(1).lower()}",
            "job_id": m.group(2) if m.lastindex >= 2 else None,
        }
    ),
]


class _NoFollowRedirect(urllib.request.HTTPRedirectHandler):
    """Captures the Location header on redirect without downloading the destination body."""
    def __init__(self):
        super().__init__()
        self.location = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.location = newurl
        return None


def resolve_redirect_url(url: str, timeout: float = 3.0) -> str:
    """
    If url is a Google redirect (google.com/goto or google.com/url),
    resolves the real destination URL quickly via HTTP redirect.
    """
    if not url:
        return ""
    if "google.com/url" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "q" in params:
            return params["q"][0]
        if "url" in params:
            return params["url"][0]

    if "google.com/goto" in url:
        handler = _NoFollowRedirect()
        opener = urllib.request.build_opener(handler)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        try:
            opener.open(req, timeout=timeout)
        except urllib.error.HTTPError:
            pass
        except Exception:
            pass
        if handler.location:
            return handler.location

    return url


def clean_company_name_from_title(title: str) -> str:
    """Attempts to extract a clean company name from typical job search titles."""
    if not title:
        return ""
    # Pattern: "Job Application for <Role> at <Company>"
    m = re.search(r"\bat\s+([A-Za-z0-9\s,\.&'-]+?)(?:\s*\(.*|\s*[-–|].*|$)", title, re.I)
    if m:
        comp = m.group(1).strip()
        if len(comp) > 1 and not comp.lower().startswith("intern"):
            return comp

    # Pattern: "<Company> - <Role>"
    m = re.match(r"^([A-Za-z0-9\s,\.&'-]+?)\s*[-–]\s*", title)
    if m:
        comp = m.group(1).strip()
        if len(comp) > 1 and len(comp.split()) <= 4:
            return comp

    # Pattern: "<Role> @ <Company>"
    m = re.search(r"@\s*([A-Za-z0-9\s,\.&'-]+)", title)
    if m:
        comp = m.group(1).strip()
        if len(comp) > 1:
            return comp

    return ""


def parse_ats_info(url: str, title: str = None, resolve_google: bool = False) -> dict | None:
    """
    Given a URL and optional title, extracts ATS platform and company information
    in a 100% deterministic way without any AI or LLM tokens.
    """
    if not url:
        return None

    resolved_url = url
    if resolve_google and ("google.com/goto" in url or "google.com/url" in url):
        resolved_url = resolve_redirect_url(url)

    for platform_name, regex, extractor in ATS_PATTERNS:
        match = regex.search(resolved_url)
        if match:
            info = extractor(match)
            info["resolved_url"] = resolved_url

            if title:
                extracted_name = clean_company_name_from_title(title)
                if extracted_name and len(extracted_name) >= 2:
                    info["company_name"] = extracted_name

            return info

    return None


def batch_test_and_extract_companies(conn=None, max_workers: int = 25, limit: int = None) -> tuple[int, int]:
    """
    100% Deterministic URL testing and company extraction:
    - Queries jobs from database.
    - Concurrently tests/follows HTTP redirects to find actual ATS destination (0 AI tokens).
    - Strictly prevents duplicate company career links (e.g. greenhouse boards).
    - Returns (new_companies_added, total_companies).
    """
    from concurrent.futures import ThreadPoolExecutor
    import sqlite3
    try:
        from src.core.db import get_conn, save_company, get_company_count, get_all_companies
    except ImportError:
        from db import get_conn, save_company, get_company_count, get_all_companies

    close_after = conn is None
    if conn is None:
        conn = get_conn()

    # Pre-load known company identifiers and career URLs for instant deduplication
    existing_companies = get_all_companies(conn)
    known_keys = {(c["ats_platform"], c["identifier"].lower()) for c in existing_companies}
    known_urls = {c["career_url"].lower().rstrip("/") for c in existing_companies}

    query = "SELECT url, title FROM jobs WHERE company IS NULL OR url LIKE '%google.com%'"
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()

    if not rows:
        print("✅ All jobs in database have already been parsed!")
        if close_after:
            conn.close()
        return 0, len(existing_companies)

    print(f"🔍 Testing & resolving {len(rows)} URLs deterministically (0 AI tokens)...")

    def test_single_job(r):
        url, title = r["url"], r["title"]
        info = parse_ats_info(url, title=title, resolve_google=True)
        return url, info

    new_companies_count = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for orig_url, info in executor.map(test_single_job, rows):
            if not info:
                continue

            ats_plat = info["ats_platform"]
            ident = info["identifier"].lower()
            career = info["career_url"].lower().rstrip("/")

            # Check if this company already exists
            key = (ats_plat, ident)
            if key not in known_keys and career not in known_urls:
                inserted = save_company(
                    name=info["company_name"],
                    ats_platform=ats_plat,
                    identifier=ident,
                    career_url=info["career_url"],
                    conn=conn
                )
                if inserted:
                    known_keys.add(key)
                    known_urls.add(career)
                    new_companies_count += 1
                    print(f"  🏢 Discovered new company: {info['company_name']} ({ats_plat}: {info['career_url']})")

            # Tag job in SQLite with company name and platform
            conn.execute(
                "UPDATE jobs SET company=?, ats_platform=? WHERE url=?",
                (info["company_name"], ats_plat, orig_url)
            )

    conn.commit()
    total_companies = get_company_count(conn)
    print(f"✨ URL Parsing Complete: +{new_companies_count} new companies registered. Total saved companies: {total_companies}")
    if close_after:
        conn.close()
    return new_companies_count, total_companies


if __name__ == "__main__":
    import sys
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    print("="*60)
    print("⚡ DETERMINISTIC ATS URL TESTER & COMPANY EXTRACTOR")
    print("   Method: HTTP redirect inspection & regex (0 AI tokens used)")
    print("="*60)
    batch_test_and_extract_companies(limit=limit_arg)
