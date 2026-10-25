"""
Web Scraper & Job Application Manager — Main CLI / GUI Entrypoint.

Usage:
    python main.py                  Launch Linux Desktop GUI (GTK4)
    python main.py --gui            Launch Linux Desktop GUI (GTK4)
    python main.py --ats            Run high-speed ATS Direct Scraper for saved companies
    python main.py --backfill       Backfill saved companies from existing database jobs
    python main.py --scrape         Run Google Search Scraper (Fast mode)
    python main.py --extract        Stage 1: Extract structured JDs via Playwright + Groq
    python main.py --jd-filter      Stage 2: Deterministic 0-token deep JD filter
    python main.py --ai-rank        Stage 3: AI Rank clean jobs from DB (0 browser overhead)
    python main.py --rank           Stream Pipeline: Extract JD -> Filter Immediately -> AI Rank
    python main.py --limits         Check live Groq API token & request limits across 7 keys
    python main.py --test           Run the automated test suite
"""
import sys
import os
import argparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")

# Auto-switch to scraper_env virtual environment if run with system python
venv_python = os.path.join(ROOT_DIR, "scraper_env", "bin", "python")
if os.path.exists(venv_python) and sys.executable != venv_python:
    try:
        import groq
        import playwright
        import dotenv
    except ImportError:
        os.execv(venv_python, [venv_python] + sys.argv)

for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    parser = argparse.ArgumentParser(description="Job Scraper & AI Tracker System (Linux)")
    parser.add_argument("--gui", action="store_true", help="Launch Linux GTK4 GUI")
    parser.add_argument("--ats", action="store_true", help="Run direct ATS scraper for saved companies")
    parser.add_argument("--backfill", action="store_true", help="Backfill company ATS boards from database")
    parser.add_argument("--seed", action="store_true", help="Seed database with top tech ATS companies")
    parser.add_argument("--scrape", action="store_true", help="Run Google search scraper (Fast)")
    parser.add_argument("--extract", action="store_true", help="Stage 1: Extract structured JDs from web pages via Playwright + Groq")
    parser.add_argument("--filter", action="store_true", help="Run deterministic pre-filter (0 AI tokens)")
    parser.add_argument("--jd-filter", action="store_true", help="Stage 2: Deterministic deep JD filter on scraped jobs (0 AI tokens)")
    parser.add_argument("--ai-rank", action="store_true", help="Stage 3: AI Rank clean jobs from DB against candidate profile (0 browser overhead)")
    parser.add_argument("--rank", action="store_true", help="Stream Pipeline: Extract JD -> Filter Immediately -> AI Rank Immediately")
    parser.add_argument("--limits", action="store_true", help="Check live Groq API rate limits & remaining quota across all keys")
    parser.add_argument("--crawl-companies", action="store_true", help="Crawl and discover valid company career portals across MyWorkday and all ATS platforms")
    parser.add_argument("--crawl-platform", default="ALL", help="Target platform for company crawler (ALL, workday, greenhouse, lever, ashby, etc.)")
    parser.add_argument("--crawl-mode", default="all", choices=["all", "feeds", "google"], help="Company crawler engine mode")
    parser.add_argument("--crawl-limit", type=int, default=None, help="Max new companies to add via crawler")
    parser.add_argument("--tier1-only", action="store_true", help="Scope 3-stage AI ranking pipeline to Tier 1 (Early Career / Intern) jobs only")
    parser.add_argument("--test", action="store_true", help="Run unit test suite")

    args = parser.parse_args()

    if args.tier1_only:
        os.environ["SCRAPER_TIER_MODE"] = "tier1"

    if args.limits:
        from src.ranking.groq_limits import run_limits_cli
        run_limits_cli()
        return

    if args.test:
        import unittest
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT_DIR, "tests"))
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        sys.exit(0 if res.wasSuccessful() else 1)

    elif args.ats:
        from src.scrapers.ats_scraper import run_ats_scraper
        run_ats_scraper()

    elif args.crawl_companies:
        from src.scrapers.company_crawler import run_company_crawler
        run_company_crawler(
            target_platform=args.crawl_platform,
            mode=args.crawl_mode,
            limit=args.crawl_limit
        )

    elif args.backfill:
        from src.core.db import extract_companies_from_existing_jobs
        cnt = extract_companies_from_existing_jobs(max_workers=25)
        print(f"✅ Total companies saved in pool: {cnt}")

    elif args.seed:
        from src.core.db import seed_starter_companies, get_company_count
        added = seed_starter_companies()
        print(f"🌱 Added {added} starter tech companies to ATS pool. Total companies: {get_company_count()}")

    elif args.scrape:
        import asyncio
        from src.scrapers.intern_scraper_fast import scrape_google_jobs
        asyncio.run(scrape_google_jobs())

    elif args.extract:
        from src.ranking.rank_internships import rank_jobs
        rank_jobs(mode="extract")

    elif args.ai_rank:
        from src.ranking.rank_internships import rank_jobs
        rank_jobs(mode="rank")

    elif args.rank:
        from src.ranking.rank_internships import rank_jobs
        rank_jobs(mode="full")

    elif args.filter:
        from src.ranking.job_filter import apply_deterministic_filter_to_db
        print("="*60)
        print("⚡ RUNNING SMART TIER DETERMINISTIC FILTER (0 AI TOKENS)")
        print("="*60)
        res = apply_deterministic_filter_to_db(sync_locations=False)
        print(f"📊 Jobs scanned:              {res['scanned']}")
        print(f"   🚫 Senior / Supervisor:     {res['senior_ignored']}")
        print(f"   🚫 Non-tech / Helpdesk:     {res['non_tech_ignored']}")
        print(f"   🚫 US-based location:       {res['us_ignored']}")
        print(f"   🚫 Canada-based location:   {res['canada_ignored']}")
        print(f"   🚫 UK-based location:       {res.get('uk_ignored', 0)}")
        print(f"   🚫 Paris-based location:    {res.get('paris_ignored', 0)}")
        print(f"   🚫 Career hub landing pages:{res['career_hub_ignored']}")
        print(f"   ----------------------------------------")
        print(f"   🎯 Tier 3 (Ignored / NA):   {res['total_ignored']} ({res['total_ignored']/(res['scanned'] or 1)*100:.1f}%)")
        print(f"   ⭐ Tier 1 (High Priority):  {res['tier1_early_career']} (Early career / Intern)")
        print(f"   💻 Tier 2 (General SWE):    {res['tier2_general_swe']} (Ready for Groq LLM)")
        print(f"   ✅ Total Active Jobs:       {res['passed']}")
        print("="*60)

    elif args.jd_filter:
        from src.ranking.job_filter import apply_deep_jd_filter_to_db
        print("="*60)
        print("🎯 RUNNING 2ND PHASE DEEP JD FILTER (0 AI TOKENS)")
        print("="*60)
        res = apply_deep_jd_filter_to_db()
        print(f"📊 Jobs scanned:              {res['scanned']}")
        print(f"   🚫 Experience >= 1 year:    {res['exp_ignored']}")
        print(f"   🚫 Senior / Lead / Staff:   {res['senior_ignored']}")
        print(f"   🚫 Non-tech / Trade roles:  {res['non_tech_ignored']}")
        print(f"   🚫 US-based location:       {res['us_ignored']}")
        print(f"   🚫 Canada-based location:   {res['canada_ignored']}")
        print(f"   🚫 UK-based location:       {res.get('uk_ignored', 0)}")
        print(f"   🚫 Paris-based location:    {res.get('paris_ignored', 0)}")
        print(f"   🚫 Security clearance:      {res['clearance_ignored']}")
        print(f"   ----------------------------------------")
        print(f"   🎯 Total Ignored (NA):      {res['total_ignored']}")
        print(f"   ✅ Clean Candidates for AI: {res['passed']}")
        print("="*60)

    else:
        # Launch Linux GTK4 GUI
        from src.gui.gui import JobApp
        app = JobApp()
        app.run(sys.argv)


if __name__ == "__main__":
    main()
