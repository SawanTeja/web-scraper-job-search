"""
Overnight Google ATS Job Scraper (Slower, stealthy pace for overnight unattended runs).
Collects unique jobs and companies into SQLite jobs_db.sqlite.
Features:
- Deterministic ATS dork generation (Broad Tech Dragnet).
- Human-like search box typing on Google.
- Authentic Next-button pagination.
- Instant zero-network <cite> breadcrumb resolution.
- Real-time company registration in SQLite companies table.
- Automatic non-tech and career hub junk filtering.
- Extended human intervals for safe overnight runs.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Auto-switch to scraper_env virtual environment if run with system python
venv_python = os.path.join(_ROOT_DIR, "scraper_env", "bin", "python")
if os.path.exists(venv_python) and sys.executable != venv_python:
    try:
        import playwright
        import playwright_stealth
    except ImportError:
        os.execv(venv_python, [venv_python] + sys.argv)

import asyncio
import argparse
from src.core.process_manager import clear_stop_flag
from src.scrapers.intern_scraper_fast import (
    scrape_google_jobs as _fast_scrape_google_jobs,
    SITES,
    SEARCH_QUERIES,
    TIME_FILTER,
    MAX_PAGES,
    build_google_dork,
    is_valid_job_url,
    clean_url,
    clean_cite_to_url,
    resolve_url
)


async def scrape_google_jobs(max_pages=None, sites_override=None, time_filter_override=None, pace="overnight"):
    """Run Google ATS scraping at a relaxed Overnight pace."""
    return await _fast_scrape_google_jobs(
        max_pages=max_pages,
        sites_override=sites_override,
        time_filter_override=time_filter_override,
        pace=pace
    )


if __name__ == "__main__":
    clear_stop_flag()
    parser = argparse.ArgumentParser(description="Google ATS Job Scraper (Overnight Pace)")
    parser.add_argument("--pages", type=int, default=None, help=f"Max pages to scrape per query (default: {MAX_PAGES})")
    parser.add_argument("--site", type=str, default=None, help="Target specific ATS site (e.g. boards.greenhouse.io)")
    parser.add_argument("--time-filter", type=str, default=None, choices=["all", "day", "week", "month", "qdr:d", "qdr:w", "qdr:m"], help="Google time filter (default: day)")
    args = parser.parse_args()

    tf_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "all": ""}
    tf_val = tf_map.get(args.time_filter.lower(), args.time_filter) if args.time_filter else None
    sites_to_run = [args.site] if args.site else None

    count = asyncio.run(scrape_google_jobs(max_pages=args.pages, sites_override=sites_to_run, time_filter_override=tf_val, pace="overnight"))
    print("\n" + "=" * 50)
    print(f"🎯 Scrape complete! {count} new jobs added to jobs_db.sqlite")
    print("=" * 50)
