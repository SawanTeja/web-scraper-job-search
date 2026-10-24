import asyncio
import urllib.parse
import random
import subprocess
import sys
import os
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

try:
    from playwright._impl._errors import TargetClosedError
except ImportError:
    TargetClosedError = Exception

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# SQLite DB helper
try:
    from src.core.db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls, save_company,
        save_scraper_checkpoint, get_scraper_checkpoint, clear_scraper_checkpoint
    )
    from src.scrapers.ats_parser import parse_ats_info
except ImportError:
    from db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls, save_company,
        save_scraper_checkpoint, get_scraper_checkpoint, clear_scraper_checkpoint
    )
    from ats_parser import parse_ats_info

# The expanded list of ATS, Portals, and Career Pages (starting with Greenhouse as requested)
SITES = [
    # Startup ATS
    "boards.greenhouse.io",
    "lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "smartrecruiters.com",

    # Enterprise ATS
    "myworkdayjobs.com", #uncomment if you want to scrape myworkdays
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "successfactors.com",
    "taleo.net",
    "oraclecloud.com",
    "keka.com/careers",
]

# The "Broad Tech" Dragnet (proven query format from 06936ea)
SEARCH_QUERIES = [
    'Software',
    'C++ OR Java',
    'Javascript OR React OR Node.js'
]

# TIME_FILTER: "qdr:d" limits search results to the past 24 hours (1 day)
TIME_FILTER = os.environ.get("SCRAPER_TIME_FILTER", "qdr:d")
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", 10))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def notify_user(title, message):
    """Send a cross-platform desktop notification to alert user."""
    try:
        import platform
        if platform.system() == "Windows":
            import ctypes, threading
            threading.Thread(
                target=lambda: ctypes.windll.user32.MessageBoxW(0, message, title, 0x30 | 0x0),
                daemon=True
            ).start()
        else:
            subprocess.run(["notify-send", "--urgency=critical", title, message], check=False)
    except Exception:
        pass


def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if not raw_url:
        return ""
    if "google.com/url" in raw_url or "/url?" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]
        elif 'url' in query_params:
            raw_url = query_params['url'][0]
    if "google.com/goto" in raw_url:
        return raw_url  # Preserve encrypted google redirects
    return raw_url.split('?')[0]


async def resolve_url(raw_url, context):
    """
    Resolves Google redirects (both /url?q= and encrypted /goto?url=)
    into the true, direct ATS destination URL using the browser session.
    """
    if not raw_url:
        return ""
    # Google unpackable redirect
    if "google.com/url" in raw_url or "/url?" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            return query_params['q'][0].split('?')[0]
        elif 'url' in query_params:
            return query_params['url'][0].split('?')[0]

    # Google encrypted redirect (/goto?url=CAES...)
    if "google.com/goto" in raw_url:
        try:
            resp = await context.request.get(raw_url, max_redirects=0, timeout=3000)
            loc = resp.headers.get("location")
            if loc:
                return loc.split('?')[0]
        except Exception:
            pass

    return raw_url.split('?')[0]


# --- Anti-CAPTCHA Mouse and Scroll Function ---
async def human_scroll_and_move(page):
    """Simulates random human scrolling and mouse movements."""
    try:
        await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        await asyncio.sleep(random.uniform(0.5, 1.5))

        await page.mouse.wheel(0, random.randint(400, 1200))
        await asyncio.sleep(random.uniform(2, 4))

        if random.choice([True, False]):
            await page.mouse.wheel(0, -random.randint(100, 300))
            await asyncio.sleep(random.uniform(1, 2))
    except Exception:
        pass
# ---------------------------------------------------


async def scrape_google_jobs(max_pages=None, resume=True, sites_override=None):
    search_count = 0
    new_jobs_added = 0
    pages_limit = max_pages or MAX_PAGES

    # Open ONE persistent DB connection for the whole scrape session.
    conn = get_conn()
    init_db(conn)

    # Load known URLs into an in-memory set for fast duplicate checking.
    unique_jobs = get_all_urls(conn)
    print(f"📦 {len(unique_jobs)} existing jobs loaded (will skip duplicates).\n")

    # Check for saved progress checkpoint
    checkpoint = get_scraper_checkpoint("google_fast", conn=conn) if resume else None
    resume_site = checkpoint.get("site") if checkpoint else None
    resume_query = checkpoint.get("query_term") if checkpoint else None
    resume_start = checkpoint.get("page_start", 0) if checkpoint else 0

    if resume_site:
        print("=" * 65)
        print(f"🔄 RESUMING FROM CHECKPOINT ({checkpoint.get('updated_at', '')[:19]}):")
        print(f"   Target: {resume_site} | Query: '{resume_query}' | Page: {int(resume_start / 10) + 1}")
        print("   (Previously completed sites/queries will be skipped automatically!)")
        print("=" * 65 + "\n")

    sites_list = list(sites_override or SITES)
    skip_until_site = resume_site is not None

    async with async_playwright() as p:
        user_data_dir = os.path.join(_ROOT_DIR, "browser_cache")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            user_agent=USER_AGENT,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"]
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await Stealth().apply_stealth_async(page)

        print("🚀 Starting the ATS Scraper...")
        print(f"📁 Data will auto-save to jobs_db.sqlite in real-time. Time filter: {TIME_FILTER or 'all'}\n")

        for site in sites_list:
            if skip_until_site:
                if site != resume_site:
                    continue
                skip_until_query = resume_query is not None
            else:
                skip_until_query = False

            print(f"\n=====================================")
            print(f"🏢 TARGETING: {site}")
            print(f"=====================================")

            for query_term in SEARCH_QUERIES:
                if skip_until_site and skip_until_query:
                    if query_term != resume_query:
                        continue
                    start_page_offset = resume_start
                    skip_until_site = False
                    skip_until_query = False
                else:
                    start_page_offset = 0

                print(f"\n   🔍 Query: {query_term}")

                for start in range(start_page_offset, pages_limit * 10, 10):
                    search_count += 1
                    save_scraper_checkpoint("google_fast", site, query_term, start, conn=conn)

                    # RANDOM HOMEPAGE DETOUR
                    if search_count % random.randint(8, 12) == 0:
                        print("      🏠 Taking a detour to Google homepage to act human...")
                        try:
                            await page.goto("https://www.google.com")
                            await human_scroll_and_move(page)
                            await asyncio.sleep(random.uniform(3, 7))
                        except Exception:
                            pass

                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    tbs_param = f"&tbs={TIME_FILTER}" if TIME_FILTER else ""
                    # &filter=0 ensures Google does NOT artificially omit similar results, revealing 10+ pages!
                    search_url = (
                        f"https://www.google.com/search"
                        f"?q={encoded_query}{tbs_param}&start={start}&filter=0&hl=en"
                    )

                    try:
                        await page.goto(search_url)
                    except Exception as e:
                        if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                            print("      ⚠️ Browser closed by user. Exiting cleanly.")
                            conn.close()
                            return new_jobs_added
                        print(f"      ⚠️ Navigation error: {e}")

                    # Wait for search results to actually render in the DOM
                    try:
                        await page.wait_for_selector('div.yuRUbf, div.g', timeout=15000)
                    except Exception:
                        print("      ⚠️ Results didn't render in time, will try to extract anyway...")

                    # HUMAN SCROLLING AFTER LOAD
                    await human_scroll_and_move(page)

                    # PAGE-TO-PAGE GAP
                    sleep_time = random.uniform(3, 8)
                    print(f"      ⏳ Page {int(start / 10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)

                    # Check for CAPTCHA
                    try:
                        is_captcha = "sorry/index" in page.url or await page.locator('form[action*="/sorry/index"]').count() > 0
                    except Exception:
                        is_captcha = False

                    if is_captcha:
                        print("      ⚠️ CAPTCHA detected! Please solve it in the Chromium window.")
                        notify_user(
                            "🚨 CAPTCHA Alert!",
                            "Google CAPTCHA detected. Please solve it in the Chromium window."
                        )
                        try:
                            await page.wait_for_selector('div#search', timeout=0)
                            print("      ✅ CAPTCHA solved! Resuming scraping...")
                        except Exception as e:
                            print(f"      ⚠️ CAPTCHA wait ended: {e}")
                            conn.close()
                            return new_jobs_added

                    try:
                        results = await page.eval_on_selector_all(
                            "div.yuRUbf, div.g",
                            """elements => {
                                let list = [];
                                for (let e of elements) {
                                    let a = e.querySelector('a:has(h3)') || e.querySelector('a');
                                    let h3 = e.querySelector('h3');
                                    if (a && a.href && (h3 || e.classList.contains('yuRUbf'))) {
                                        list.push({
                                            url: a.href,
                                            title: h3 ? h3.innerText.trim() : 'Unknown Title'
                                        });
                                    }
                                }
                                return list;
                            }"""
                        )

                        if not results:
                            print("      🚫 No more results for this query. Moving to next.")
                            break

                        print(f"      📊 Found {len(results)} results on page.")
                        new_jobs_found = 0

                        target_clean = site.replace("boards.", "").replace("jobs.", "").replace("apply.", "").lower()

                        for res in results:
                            raw_url = res.get('url', '')
                            title = res.get('title', 'Unknown Title')
                            if not raw_url:
                                continue

                            # Resolve Google redirect to direct ATS destination URL
                            final_url = await resolve_url(raw_url, context)

                            # Validate URL matches target ATS site or preserved goto redirect
                            is_target_site = (site in final_url.lower() or target_clean in final_url.lower() or site in raw_url)
                            if is_target_site or "google.com/goto" in final_url:
                                if final_url not in unique_jobs:
                                    unique_jobs.add(final_url)

                                    # Save ATS company if available
                                    ats_info = parse_ats_info(final_url, title=title, resolve_google=False)
                                    comp_name = ats_info.get("company_name") if ats_info else None
                                    plat = ats_info.get("ats_platform") if ats_info else None

                                    if ats_info:
                                        try:
                                            save_company(
                                                name=comp_name,
                                                ats_platform=plat,
                                                identifier=ats_info["identifier"],
                                                career_url=ats_info["career_url"],
                                                conn=conn
                                            )
                                        except Exception:
                                            pass

                                    # Deterministic Smart Tiering (Tier 1 HIGH, Tier 2 SWE, Tier 3 IGNORE)
                                    try:
                                        from src.ranking.job_filter import categorize_job_tier
                                    except ImportError:
                                        try:
                                            from ranking.job_filter import categorize_job_tier
                                        except ImportError:
                                            from job_filter import categorize_job_tier
                                    tier_res = categorize_job_tier(title=title)

                                    tier_mode = os.environ.get("SCRAPER_TIER_MODE", "all").lower()
                                    if ("--tier1-only" in sys.argv or tier_mode in ("tier1", "tier1_only")) and tier_res.get("tier") != 1:
                                        continue

                                    # Write directly to SQLite
                                    inserted = insert_or_ignore_job(
                                        final_url, title, conn,
                                        status=tier_res["status"],
                                        rank=tier_res["rank"],
                                        reason=tier_res["reason"],
                                        company=comp_name,
                                        ats_platform=plat
                                    )
                                    if inserted:
                                        new_jobs_found += 1
                                        new_jobs_added += 1
                                        print(f"      💾 Found: {title[:40]} -> {final_url[:65]}")
                                else:
                                    print(f"      ⏭️ Skipped (duplicate): {final_url[:65]}")
                            else:
                                print(f"      ⏭️ Skipped (site mismatch): {final_url[:65]}")

                        # Commit the batch to disk once per page (not per job)
                        if new_jobs_found > 0:
                            conn.commit()
                            print(f"      💾 Saved {new_jobs_found} new jobs to DB.")

                    except Exception as e:
                        if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                            print("      ⚠️ Browser closed by user. Exiting cleanly.")
                            conn.close()
                            return new_jobs_added
                        print(f"      ❌ Error extracting links: {e}")

                # QUERY-TO-QUERY GAP
                query_pause = random.uniform(8, 15)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                await asyncio.sleep(query_pause)

            # SITE-TO-SITE GAP
            site_pause = random.uniform(20, 30)
            print(f"☕ Finished {site}. Taking a long {site_pause:.0f}s break...")
            await asyncio.sleep(site_pause)

        await context.close()

    clear_scraper_checkpoint("google_fast", conn=conn)
    conn.close()
    return new_jobs_added


if __name__ == "__main__":
    count = asyncio.run(scrape_google_jobs())
    print("\n" + "=" * 50)
    print(f"🎯 Scrape complete! {count} new jobs added to jobs_db.sqlite")
    print("=" * 50)