"""
Web Scraper & Job Application Manager — Main CLI / GUI Entrypoint.

Usage:
    python main.py                  Launch default Desktop GUI (GTK4 on Linux, CustomTkinter on Windows)
    python main.py --gui            Launch Linux GTK4 GUI
    python main.py --win            Launch Windows CustomTkinter GUI
    python main.py --ats            Run high-speed ATS Direct Scraper for saved companies
    python main.py --backfill       Backfill saved companies from existing database jobs
    python main.py --scrape         Run Google Search Scraper (Fast mode)
    python main.py --rank           Run Groq AI LLM Ranker
    python main.py --filter         Run 100% Deterministic Pre-Filter (US, Senior, Non-Tech)
    python main.py --test           Run the automated test suite
"""
import sys
import os
import argparse

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT_DIR, "src")
for p in [ROOT_DIR, SRC_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    parser = argparse.ArgumentParser(description="Job Scraper & AI Tracker System")
    parser.add_argument("--gui", action="store_true", help="Launch Linux GTK4 GUI")
    parser.add_argument("--win", action="store_true", help="Launch Windows CustomTkinter GUI")
    parser.add_argument("--ats", action="store_true", help="Run direct ATS scraper for saved companies")
    parser.add_argument("--backfill", action="store_true", help="Backfill company ATS boards from database")
    parser.add_argument("--scrape", action="store_true", help="Run Google search scraper (Fast)")
    parser.add_argument("--rank", action="store_true", help="Run Groq AI LLM ranker")
    parser.add_argument("--filter", action="store_true", help="Run deterministic pre-filter (0 AI tokens)")
    parser.add_argument("--test", action="store_true", help="Run unit test suite")

    args = parser.parse_args()

    if args.test:
        import unittest
        suite = unittest.defaultTestLoader.discover(os.path.join(ROOT_DIR, "tests"))
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        sys.exit(0 if res.wasSuccessful() else 1)

    elif args.ats:
        from src.scrapers.ats_scraper import run_ats_scraper
        run_ats_scraper()

    elif args.backfill:
        from src.core.db import extract_companies_from_existing_jobs
        cnt = extract_companies_from_existing_jobs(max_workers=25)
        print(f"✅ Total companies saved in pool: {cnt}")

    elif args.scrape:
        import asyncio
        from src.scrapers.intern_scraper_fast import scrape_google_jobs
        asyncio.run(scrape_google_jobs())

    elif args.rank:
        from src.ranking.rank_internships import rank_jobs
        rank_jobs()

    elif args.filter:
        from src.ranking.job_filter import apply_deterministic_filter_to_db
        apply_deterministic_filter_to_db()

    elif args.win:
        from src.gui.win_gui import JobAppWindow
        app = JobAppWindow()
        app.mainloop()

    else:
        # Default: Launch appropriate GUI for platform
        import platform
        if platform.system() == "Windows" or args.win:
            try:
                from src.gui.win_gui import JobAppWindow
                app = JobAppWindow()
                app.mainloop()
            except Exception as e:
                print(f"Windows GUI error: {e}")
        else:
            try:
                from src.gui.gui import JobApp
                app = JobApp()
                app.run(sys.argv)
            except Exception as e:
                print(f"Launching Linux GTK4 GUI error: {e}")
                print("Falling back to CustomTkinter GUI...")
                try:
                    from src.gui.win_gui import JobAppWindow
                    app = JobAppWindow()
                    app.mainloop()
                except Exception as e2:
                    print(f"Could not launch GUI: {e2}")


if __name__ == "__main__":
    main()
