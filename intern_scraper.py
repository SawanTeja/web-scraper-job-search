"""
Forwarding alias for intern_scraper.py to src/scrapers/intern_scraper.py.
"""
import sys
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
for _p in [_ROOT, _SRC]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.scrapers.intern_scraper import *

if __name__ == "__main__":
    import asyncio
    import argparse
    from src.core.process_manager import clear_stop_flag
    clear_stop_flag()

    from src.scrapers.intern_scraper import scrape_google_jobs, MAX_PAGES

    parser = argparse.ArgumentParser(description="Google ATS Job Scraper (Medium Pace)")
    parser.add_argument("--pages", type=int, default=None, help=f"Max pages to scrape per query (default: {MAX_PAGES})")
    parser.add_argument("--site", type=str, default=None, help="Target specific ATS site (e.g. boards.greenhouse.io)")
    parser.add_argument("--time-filter", type=str, default=None, choices=["all", "day", "week", "month", "qdr:d", "qdr:w", "qdr:m"], help="Google time filter (default: day)")
    args = parser.parse_args()

    tf_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "all": ""}
    tf_val = tf_map.get(args.time_filter.lower(), args.time_filter) if args.time_filter else None
    sites_to_run = [args.site] if args.site else None

    count = asyncio.run(scrape_google_jobs(max_pages=args.pages, sites_override=sites_to_run, time_filter_override=tf_val, pace="medium"))
    print("\n" + "=" * 50)
    print(f"🎯 Scrape complete! {count} new jobs added to jobs_db.sqlite")
    print("=" * 50)
