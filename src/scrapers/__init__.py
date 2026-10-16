"""
Scrapers package for Google search scraping and direct company ATS platform scraping.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.scrapers.ats_parser import (
    parse_ats_info, resolve_redirect_url, clean_company_name_from_title,
    batch_test_and_extract_companies, ATS_PATTERNS
)
from src.scrapers.ats_scraper import (
    run_ats_scraper, scrape_company_ats, PLATFORM_FETCHERS
)

__all__ = [
    "parse_ats_info", "resolve_redirect_url", "clean_company_name_from_title",
    "batch_test_and_extract_companies", "ATS_PATTERNS",
    "run_ats_scraper", "scrape_company_ats", "PLATFORM_FETCHERS"
]
