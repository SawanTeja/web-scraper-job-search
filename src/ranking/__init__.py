"""
Ranking package for AI evaluation (Groq) and deterministic pre-filtering.
"""
import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.ranking.job_filter import (
    is_us_based, is_senior_or_lead, is_non_tech, is_career_hub, is_early_career,
    categorize_job_tier, evaluate_deterministic_ignore, apply_deterministic_filter_to_db,
    sync_ats_locations_to_db
)
from src.ranking.rank_internships import GroqManager, rank_jobs
from src.ranking.reset_ranks import reset_ranks

__all__ = [
    "is_us_based", "is_senior_or_lead", "is_non_tech", "is_career_hub", "is_early_career",
    "categorize_job_tier", "evaluate_deterministic_ignore", "apply_deterministic_filter_to_db",
    "sync_ats_locations_to_db",
    "GroqManager", "rank_jobs", "reset_ranks"
]
