"""
job_filter.py — 100% Deterministic Pre-Filtering Engine (Zero AI Tokens).

Filters out:
1. US-based jobs (cities, states, country identifiers, US remote).
2. Senior / Lead / Manager / SDE II / SDE III / Staff / Principal roles.
3. Non-tech roles (Sales, HR, Marketing, Finance, Legal, Support, Healthcare, etc.).
"""
import re
import sys
import os
from datetime import datetime, timezone

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.core.db import get_conn, init_db, get_all_companies
except ImportError:
    from db import get_conn, init_db, get_all_companies

# ──────────────────────────────────────────────
#  Patterns for Senior / Non-Entry Level
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
#  Patterns for Senior / Non-Entry Level
# ──────────────────────────────────────────────
SENIOR_TITLE_PATTERNS = [
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\blead(?:er|ership)?\b",
    r"\bteam\s*lead(?:er)?\b",
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\bexpert\b",
    r"\bchief\b",
    r"\barchitect\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bhead\s+of\b",
    r"\bmanager\b",
    r"\bmanagement\b",
    r"\bconsultant\b",
    r"\b(?:sde|swe|engineer|developer|architect|consultant|analyst)\s*[-_ ]*(?:ii|iii|iv|v|2|3|4|5)\b",
    r"\b(?:software\s+)?(?:development\s+)?engineer\s*[-_ ]*(?:ii|iii|iv|v|2|3|4|5)\b",
    r"\b(?:level|lvl)\s*[23456]\b",
    r"\b[l|e][345678]\b",
    r"\bmid-senior\b",
    r"\bexperienced\b",
]

SENIOR_TITLE_REGEX = re.compile("|".join(SENIOR_TITLE_PATTERNS), re.I)

# Experience requirement patterns in JD body
EXPERIENCE_PATTERNS = [
    r"\b(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?)\b",
    r"\b(?:at\s+least|minimum\s+of|more\s+than)\s+[2-9]\s*(?:years?|yrs?)\b",
    r"\b(?:3|4|5|6|7|8|9|10)\+?\s*years?\s+of\s+(?:software|engineering|relevant|professional)\s+experience\b",
]
EXPERIENCE_REGEX = re.compile("|".join(EXPERIENCE_PATTERNS), re.I)

# ──────────────────────────────────────────────
#  Patterns for Non-Tech Roles
# ──────────────────────────────────────────────
NON_TECH_PATTERNS = [
    r"\b(?:sales|account executive|account manager|business development|bdr|sdr|account partner|partner manager)\b",
    r"\b(?:recruiter|recruiting|talent acquisition|human resources|\bhr\b|sourcer|headhunter)\b",
    r"\b(?:marketing|social media|copywriter|content creator|seo specialist|communications specialist)\b",
    r"\b(?:finance|accountant|accounting|financial analyst|payroll|billing|auditor|controller)\b",
    r"\b(?:legal|attorney|counsel|lawyer|paralegal|compliance officer|regulatory affairs|tax|transfer pricing)\b",
    r"\b(?:nurse|nursing|physician|doctor|medical assistant|dental|healthcare assistant|pharmacist|veterinar(?:y|ian))\b",
    r"\b(?:customer service|customer support|client support|call center|help desk|support specialist)\b",
    r"\b(?:receptionist|office manager|executive assistant|administrative assistant|front desk)\b",
    r"\b(?:warehouse|store associate|driver|delivery|barista|cook|chef|cashier|retail)\b",
    r"\b(?:civil engineer|mechanical engineer|structural engineer|construction|technician|electrician|facilities|hvac)\b",
    r"\b(?:geolog(?:y|ist|ical)|geotechnical|asbestos|metallurg(?:y|ical)|mining\s+engineer|environmental|hydrolog(?:y|ist))\b",
    r"\b(?:fire\s+engineer|fire\s+safety|safety\s+officer|security\s+officer|security\s+guard|health\s+and\s+safety|ehs)\b",
    r"\b(?:traffic\s+engineer|process\s+engineer|field\s+service|warranty|surveyor|surveying)\b",
    r"\b(?:graphic\s+designer|visual\s+designer|video\s+editor)\b",
    r"\b(?:logistics?|supply\s+chain|procurement|inventory|logisztika|shipping)\b",
    r"\b(?:contrôleur|esthétique|habillage|horloger|mouvement|industrialisation|simulation\s+des\s+chocs)\b",
]
NON_TECH_REGEX = re.compile("|".join(NON_TECH_PATTERNS), re.I)

# ──────────────────────────────────────────────
#  Patterns for US Locations (Country, States, Tech Hubs)
# ──────────────────────────────────────────────
US_COUNTRY_PATTERNS = [
    r"\bunited\s+states\b",
    r"\bu\.s\.a?\b",
    r"\busa\b",
    r"\bus\s+remote\b",
    r"\bremote\s*[-–(]\s*us\b",
    r"\bremote\s+in\s+the\s+us\b",
    r"\bus\s+only\b",
    r"\bwithin\s+the\s+us\b",
    r"\bus\s+citizenship\b",
    r"\bus\s+work\s+authori[zs]ation\b",
]

US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming", "District of Columbia"
]

US_STATE_ABBRS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
]

US_CITIES = [
    "San Francisco", "Silicon Valley", "San Jose", "Bay Area", "Los Angeles",
    "San Diego", "Seattle", "Redmond", "Bellevue", "Austin", "Dallas",
    "Houston", "New York City", "NYC", "Manhattan", "Brooklyn", "Boston",
    "Cambridge", "Chicago", "Atlanta", "Denver", "Boulder", "Portland",
    "Salt Lake City", "Miami", "Pittsburgh", "Philadelphia", "Washington DC",
    "Phoenix", "Raleigh", "Charlotte", "Minneapolis", "Detroit", "Sunnyvale",
    "Palo Alto", "Mountain View", "Menlo Park", "Cupertino", "Santa Clara",
    "Omaha", "Des Moines", "Lincoln", "Sioux Falls", "Milwaukee", "Indianapolis",
    "Nashville", "Memphis", "Louisville", "Oklahoma City", "Kansas City",
    "Cleveland", "Columbus", "Cincinnati", "Tampa", "Orlando", "Jacksonville",
    "St. Louis", "Saint Louis", "San Antonio", "Honolulu", "Anchorage",
    "Las Vegas", "Sacramento", "Baltimore", "Richmond", "Providence", "Hartford",
    "Jonestown", "Eden Prairie", "Maple Grove"
]

# Combined Regexes for Location
US_COUNTRY_REGEX = re.compile("|".join(US_COUNTRY_PATTERNS), re.I)
US_STATES_REGEX = re.compile(r"\b(" + "|".join(US_STATES) + r")\b", re.I)
US_CITIES_REGEX = re.compile(r"\b(" + "|".join(US_CITIES) + r")\b", re.I)
# Two-letter state abbreviation preceded by comma or space and followed by word boundary or end
US_STATE_ABBR_REGEX = re.compile(r"(?:,\s*|\s+)(" + "|".join(US_STATE_ABBRS) + r")(?:\s*,\s*USA|\s*,\s*United States|\s*$|[,\s\)\-])", re.I)


# ──────────────────────────────────────────────
#  Deterministic Evaluation Functions
# ──────────────────────────────────────────────

def is_senior_or_lead(title: str, jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is Senior/Lead/Manager/SDE 2/SDE 3/Staff/etc. from title."""
    if title:
        # Check direct and normalized (replace underscores/slashes with space)
        clean_title = re.sub(r"[_/\\]+", " ", title)
        m = SENIOR_TITLE_REGEX.search(title) or SENIOR_TITLE_REGEX.search(clean_title)
        if m:
            return True, f"Senior/Lead level detected in title: '{m.group(0)}'"

    return False, ""


def is_non_tech(title: str, jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is non-technical (Sales, HR, Marketing, etc.) from title."""
    if title:
        clean_title = re.sub(r"[_/\\]+", " ", title)
        m = NON_TECH_REGEX.search(title) or NON_TECH_REGEX.search(clean_title)
        if m:
            return True, f"Non-tech role detected in title: '{m.group(0)}'"

    return False, ""


def is_us_based(title: str = "", location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is located in the USA."""
    # 1. Check title explicitly for US country code (e.g. "2027 US AI Engineering", "(US)", "[US]", "US Remote")
    if title:
        m = re.search(r"(?:\b|_)(?:US|USA|U\.S\.A?)(?:\b|_)", title)
        if m:
            return True, f"US country code detected in title: '{m.group(0)}'"

    # 2. Check location string directly
    if location:
        if re.search(r"\b(?:US|USA|U\.S\.A?)\b", location, re.I):
            return True, f"US country code in location: '{location}'"

    # 3. Check combined title + location with standard patterns
    loc_combined = f"{title} {location}".strip()
    if loc_combined:
        m = US_COUNTRY_REGEX.search(loc_combined)
        if m:
            return True, f"US country identifier detected: '{m.group(0)}'"
        m = US_CITIES_REGEX.search(loc_combined)
        if m:
            return True, f"US tech city detected: '{m.group(0)}'"
        m = US_STATES_REGEX.search(loc_combined)
        if m:
            return True, f"US state detected: '{m.group(0)}'"
        m = US_STATE_ABBR_REGEX.search(loc_combined)
        if m:
            return True, f"US state postal code detected: '{m.group(1)}'"

    # 4. Check full JD text
    if jd_text:
        jd_header = jd_text[:800]
        m = US_COUNTRY_REGEX.search(jd_header)
        if m:
            return True, f"US country identifier in JD: '{m.group(0)}'"
        m = US_CITIES_REGEX.search(jd_header)
        if m:
            return True, f"US city in JD: '{m.group(0)}'"
        m = US_STATES_REGEX.search(jd_header)
        if m:
            return True, f"US state in JD: '{m.group(0)}'"
        m = US_STATE_ABBR_REGEX.search(jd_header)
        if m:
            return True, f"US state postal code in JD: '{m.group(1)}'"

    return False, ""


def evaluate_deterministic_ignore(title: str, location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """
    Master pre-filter: Returns (True, reason) if job should be deterministically IGNORED.
    Evaluates:
      1. Senior / SDE 2 / SDE 3 / Lead / Staff / Manager
      2. Non-tech roles (Sales, HR, Marketing, Legal, etc.)
      3. US-based locations
    """
    # 1. Senior / Lead check
    is_sr, reason = is_senior_or_lead(title, jd_text)
    if is_sr:
        return True, reason

    # 2. Non-tech role check
    is_nt, reason = is_non_tech(title, jd_text)
    if is_nt:
        return True, reason

    # 3. US location check
    is_us, reason = is_us_based(title=title, location=location, jd_text=jd_text)
    if is_us:
        return True, reason

    return False, ""


# ──────────────────────────────────────────────
#  Database Pre-Filter Batch Runner
# ──────────────────────────────────────────────

def sync_ats_locations_to_db(conn=None) -> int:
    """
    Directly fetches locations from saved ATS companies and backfills jobs whose
    details field is missing or lacks location. Runs concurrently in seconds (0 AI tokens).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        from src.scrapers.ats_scraper import PLATFORM_FETCHERS
    except ImportError:
        from ats_scraper import PLATFORM_FETCHERS
    import json

    close_after = conn is None
    if conn is None:
        conn = get_conn()

    companies = get_all_companies(conn)
    if not companies:
        if close_after:
            conn.close()
        return 0

    url_to_loc = {}
    def fetch_comp(c):
        p, i = c.get('ats_platform'), c.get('identifier')
        f = PLATFORM_FETCHERS.get(p)
        if not f:
            return []
        try:
            return f(i)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=25) as ex:
        futs = [ex.submit(fetch_comp, c) for c in companies]
        for fut in as_completed(futs):
            for j in fut.result():
                u, loc = j.get('url'), j.get('location')
                if u and loc:
                    url_to_loc[u] = loc

    updated = 0
    for u, loc in url_to_loc.items():
        cur = conn.execute(
            "UPDATE jobs SET details=? WHERE url=? AND (details IS NULL OR details NOT LIKE '%location%')",
            (json.dumps({'location': loc}), u)
        )
        updated += cur.rowcount

    conn.commit()
    if close_after:
        conn.close()
    return updated


def apply_deterministic_filter_to_db(conn=None, only_unknown: bool = False, move_to_na: bool = True, sync_locations: bool = True) -> dict:
    """
    Runs deterministic pre-filtering over SQLite database in bulk.
    Instantly marks matching jobs as rank='IGNORE' and moves them out of 'New' to 'NA'.
    Returns stats dict.
    """
    import sqlite3
    close_after = conn is None
    if conn is None:
        conn = get_conn()
        init_db(conn)
    elif not conn.row_factory:
        conn.row_factory = sqlite3.Row

    # Sync missing location details from ATS platforms if needed
    if sync_locations:
        try:
            sync_ats_locations_to_db(conn)
        except Exception as e:
            print(f"Location sync note: {e}")

    # First: move any existing IGNORE jobs that are still in 'New' to 'NA'
    cur = conn.execute("UPDATE jobs SET status='NA' WHERE rank='IGNORE' AND status='New'")
    already_ignored_moved = cur.rowcount

    query = "SELECT url, title, details FROM jobs WHERE status = 'New' OR rank = 'UNKNOWN'"
    rows = conn.execute(query).fetchall()

    stats = {
        "scanned": len(rows),
        "senior_ignored": 0,
        "non_tech_ignored": 0,
        "us_ignored": 0,
        "total_ignored": already_ignored_moved,
        "passed": 0
    }

    status_target = "NA" if move_to_na else "New"

    for r in rows:
        if isinstance(r, (tuple, list)):
            url, title, det_raw = r[0], r[1] or "", r[2]
        else:
            url = r["url"]
            title = r["title"] or ""
            det_raw = r["details"]

        location = ""
        jd_snippet = ""
        if det_raw:
            try:
                import json
                det = json.loads(det_raw)
                location = det.get("location") or ""
                jd_snippet = det.get("about_job") or ""
            except Exception:
                pass

        # 1. Senior
        is_sr, sr_reason = is_senior_or_lead(title, jd_snippet)
        if is_sr:
            conn.execute("UPDATE jobs SET rank='IGNORE', status=?, reason=? WHERE url=?", (status_target, f"[Deterministic] {sr_reason}", url))
            stats["senior_ignored"] += 1
            stats["total_ignored"] += 1
            continue

        # 2. Non-tech
        is_nt, nt_reason = is_non_tech(title, jd_snippet)
        if is_nt:
            conn.execute("UPDATE jobs SET rank='IGNORE', status=?, reason=? WHERE url=?", (status_target, f"[Deterministic] {nt_reason}", url))
            stats["non_tech_ignored"] += 1
            stats["total_ignored"] += 1
            continue

        # 3. US based
        is_us, us_reason = is_us_based(title=title, location=location, jd_text=jd_snippet)
        if is_us:
            conn.execute("UPDATE jobs SET rank='IGNORE', status=?, reason=? WHERE url=?", (status_target, f"[Deterministic] {us_reason}", url))
            stats["us_ignored"] += 1
            stats["total_ignored"] += 1
            continue

        stats["passed"] += 1

    conn.commit()
    if close_after:
        conn.close()

    return stats


if __name__ == "__main__":
    print("="*60)
    print("⚡ RUNNING DETERMINISTIC PRE-FILTER ON DATABASE (0 AI TOKENS)")
    print("="*60)
    res = apply_deterministic_filter_to_db(only_unknown=True)
    print(f"📊 Jobs scanned:              {res['scanned']}")
    print(f"   🚫 Senior / SDE II/III:     {res['senior_ignored']}")
    print(f"   🚫 Non-tech roles:          {res['non_tech_ignored']}")
    print(f"   🚫 US-based location:       {res['us_ignored']}")
    print(f"   ----------------------------------------")
    print(f"   🎯 Total Ignored:           {res['total_ignored']} ({res['total_ignored']/(res['scanned'] or 1)*100:.1f}%)")
    print(f"   ✅ Remaining Candidate Jobs:{res['passed']}")
    print("="*60)
