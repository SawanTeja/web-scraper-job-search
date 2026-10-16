"""
Core package providing database access, schema initialization, and persistence.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.core.db import (
    get_conn, init_db, load_db, save_db, delete_jobs_by_rank,
    delete_jobs_by_status, clear_all_jobs, clear_all_companies,
    clear_everything, update_job, get_company_count, get_all_companies,
    delete_company, get_company_stats, save_company, job_exists, get_all_urls,
    extract_companies_from_existing_jobs, DB_FILE
)

__all__ = [
    "get_conn", "init_db", "load_db", "save_db", "delete_jobs_by_rank",
    "delete_jobs_by_status", "clear_all_jobs", "clear_all_companies",
    "clear_everything", "update_job", "get_company_count", "get_all_companies",
    "delete_company", "get_company_stats", "save_company", "job_exists", "get_all_urls",
    "extract_companies_from_existing_jobs", "DB_FILE"
]
