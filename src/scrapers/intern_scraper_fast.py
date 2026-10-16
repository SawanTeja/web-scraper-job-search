import asyncio
import urllib.parse
import random
import subprocess
import sys
import os
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

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

# Supported ATS domains for career page searches
SITES = [
    # Top Startup ATS
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "smartrecruiters.com",

    # Enterprise ATS
    "jobs.jobvite.com",
    "bamboohr.com",
    "successfactors.com",
    "taleo.net",
    "icims.com",
    "oraclecloud.com",
]

# Quoted, high-intent job title queries (prevents Google from matching dictionary entries or YouTube videos)
SEARCH_QUERIES = [
    '"software engineer" OR "software developer"',
    '"intern" OR "internship"',
    '"backend" OR "frontend" OR "full stack"'
]

TIME_FILTER = "qdr:d"

# Default to 3 pages per query to prevent triggering Google's rate limits.
# 3 pages captures ~95% of fresh (<24h) postings while reducing queries by 70%.
MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", 3))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

VALID_ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "jobvite.com", "bamboohr.com",
    "successfactors.com", "taleo.net", "oraclecloud.com",
    "myworkdayjobs.com", "icims.com", "breezy.hr", "recruitee.com"
]

NON_JOB_JUNK_DOMAINS = [
    "youtube.com", "oxfordlearnersdictionaries.com", "dictionary.com",
    "wikipedia.org", "reddit.com", "support.google.com", "google.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "medium.com",
    "quora.com", "github.com", "amazon.com", "apple.com/support"
]


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
    """Decodes Google redirects and returns the clean destination URL."""
    if not raw_url:
        return ""
    url = raw_url
    if "google.com/url" in url:
        parsed = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            url = query_params['q'][0]
        elif 'url' in query_params:
            url = query_params['url'][0]
    return url.split('?')[0]


def is_valid_job_url(final_url, target_site):
    """
    Strict validation: ensures the URL actually belongs to the target site
    or a recognized ATS career portal, and explicitly rejects non-job web pages.
    """
    if not final_url or not final_url.startswith("http"):
        return False

    url_lower = final_url.lower()

    # Reject encrypted or unresolved Google redirect tracking tokens
    if "google.com" in url_lower:
        return False

    # Reject non-job domains (dictionaries, YouTube videos, blogs, reddit)
    if any(junk in url_lower for junk in NON_JOB_JUNK_DOMAINS):
        return False

    # Check if target site (e.g. jobvite.com) is in the URL
    if target_site:
        clean_target = target_site.replace("jobs.", "").replace("boards.", "").lower()
        if clean_target in url_lower:
            return True

    # Or any recognized ATS platform
    return any(d in url_lower for d in VALID_ATS_DOMAINS)


# --- Anti-CAPTCHA Mouse and Scroll Function ---
async def human_scroll_and_move(page):
    """Simulates random human scrolling and mouse movements."""
    try:
        await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
        await asyncio.sleep(random.uniform(0.5, 1.2))

        await page.mouse.wheel(0, random.randint(300, 900))
        await asyncio.sleep(random.uniform(1.5, 2.5))

        if random.choice([True, False]):
            await page.mouse.wheel(0, -random.randint(100, 250))
            await asyncio.sleep(random.uniform(0.8, 1.5))
    except Exception:
        pass


async def scrape_google_jobs(max_pages=None, resume=True, sites_override=None):
    search_count = 0
    new_jobs_added = 0
    pages_limit = max_pages or MAX_PAGES

    conn = get_conn()
    init_db(conn)

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
    if not resume_site:
        random.shuffle(sites_list)

    skip_until_site = resume_site is not None
    stopped_due_to_rate_limit = False

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

        print("🚀 Starting the Smart Resumable ATS Scraper...")
        print(f"📁 Data auto-saves to jobs_db.sqlite. Max pages per query: {pages_limit}\n")

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
                            await asyncio.sleep(random.uniform(3, 6))
                        except Exception:
                            pass

                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    search_url = (
                        f"https://www.google.com/search"
                        f"?q={encoded_query}&tbs={TIME_FILTER}&start={start}"
                    )

                    try:
                        await page.goto(search_url)
                    except Exception as e:
                        print(f"      ⚠️ Navigation error: {e}")

                    # Check for Google rate-limit or CAPTCHA
                    page_url = page.url
                    is_sorry = "sorry/index" in page_url or await page.locator('form[action*="/sorry/index"]').count() > 0

                    if is_sorry:
                        try:
                            page_content = await page.content()
                        except Exception:
                            page_content = ""

                        has_recaptcha = (
                            "g-recaptcha" in page_content
                            or await page.locator('iframe[src*="recaptcha"], div.g-recaptcha').count() > 0
                        )
                        is_hard_block = (
                            "unusual traffic from your computer network" in page_content
                            and not has_recaptcha
                        )

                        if is_hard_block:
                            print("\n" + "!" * 65)
                            print("🚨 GOOGLE TEMPORARY RATE LIMIT DETECTED (/sorry/index)")
                            print("   Google has temporarily paused automated searches from this IP.")
                            print(f"💾 Checkpoint auto-saved: {site} -> '{query_term}' (Page {int(start / 10) + 1})")
                            print("   ✅ You will NOT lose your spot or have to restart from scratch!")
                            print("\n💡 Recommended Actions:")
                            print("   1. Run 'python main.py --ats' (Direct ATS Scraper):")
                            print("      Directly scrapes company ATS boards with ZERO Google rate limits!")
                            print("   2. Or wait ~15-30 minutes for Google cooldown, then re-run to resume.")
                            print("   3. Or toggle a VPN / mobile hotspot to change IP and resume now.")
                            print("!" * 65 + "\n")

                            save_scraper_checkpoint("google_fast", site, query_term, start, conn=conn)
                            notify_user(
                                "🚨 Google Rate Limit (IP Cooldown)",
                                "Google temporarily rate-limited searches. Checkpoint saved! Use Direct ATS Scraper or resume later."
                            )
                            stopped_due_to_rate_limit = True
                            break
                        else:
                            print("      ⚠️ Solvable CAPTCHA detected! Please solve it in the Chromium window.")
                            notify_user("🚨 CAPTCHA Alert!", "Google CAPTCHA detected. Please solve it in the Chromium window.")
                            try:
                                await page.wait_for_selector('div#search', timeout=180000)
                                print("      ✅ CAPTCHA solved! Resuming scraping...")
                            except Exception:
                                print("      ⏰ CAPTCHA wait timed out (180s). Progress checkpoint saved.")
                                save_scraper_checkpoint("google_fast", site, query_term, start, conn=conn)
                                stopped_due_to_rate_limit = True
                                break

                    # Check if Google relaxed the query (0 results on target site)
                    try:
                        page_html = await page.content()
                        is_relaxed = (
                            "did not match any documents" in page_html
                            or f"No results found for site:{site}" in page_html
                            or "It looks like there aren't many great matches for your search" in page_html
                            or "Showing results for" in page_html
                            or "Must include:" in page_html
                        )
                        if is_relaxed:
                            print(f"      🚫 Google found 0 recent results for site:{site}. (Google relaxed to general web). Skipping query.")
                            break
                    except Exception:
                        pass

                    # Wait for search results
                    try:
                        await page.wait_for_selector('div.yuRUbf, div.g, div[data-hveid]', timeout=10000)
                    except Exception:
                        pass

                    await human_scroll_and_move(page)

                    sleep_time = random.uniform(4, 8)
                    print(f"      ⏳ Page {int(start / 10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)

                    try:
                        # Extract cards using modern Google selectors with fallback
                        results = await page.eval_on_selector_all(
                            "div.yuRUbf, div.g, div[data-hveid]",
                            """elements => {
                                let list = [];
                                for (let e of elements) {
                                    let a = e.querySelector('a:has(h3)') || e.querySelector('a[href^="http"]');
                                    let h3 = e.querySelector('h3');
                                    if (a && h3 && a.href) {
                                        list.push({
                                            url: a.href,
                                            title: h3.innerText ? h3.innerText.trim() : 'Unknown Title'
                                        });
                                    }
                                }
                                return list;
                            }"""
                        )

                        if not results:
                            print("      🚫 No more results for this query. Moving to next.")
                            break

                        new_jobs_found = 0

                        for res in results:
                            raw_url = res.get('url', '')
                            title = res.get('title', '')
                            final_url = clean_url(raw_url)

                            # STRICT VALIDATION: Ensure URL is a legitimate ATS job page and not dictionary/youtube
                            if not is_valid_job_url(final_url, site):
                                continue

                            if final_url not in unique_jobs:
                                unique_jobs.add(final_url)

                                ats_info = parse_ats_info(final_url, title=title, resolve_google=False)
                                comp_name = ats_info.get("company_name") if ats_info else None
                                plat = ats_info.get("ats_platform") if ats_info else None

                                if ats_info:
                                    save_company(
                                        name=comp_name,
                                        ats_platform=plat,
                                        identifier=ats_info["identifier"],
                                        career_url=ats_info["career_url"],
                                        conn=conn
                                    )

                                inserted = insert_or_ignore_job(
                                    final_url, title, conn,
                                    company=comp_name,
                                    ats_platform=plat
                                )
                                if inserted:
                                    new_jobs_found += 1
                                    new_jobs_added += 1
                                    print(f"      💾 [{plat or 'Job'}] {comp_name or 'Company'}: {title[:40]} -> {final_url[:60]}")
                            else:
                                pass

                        if new_jobs_found > 0:
                            conn.commit()

                    except Exception as e:
                        print(f"      ❌ Error extracting links: {e}")

                if stopped_due_to_rate_limit:
                    break

                query_pause = random.uniform(8, 15)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                await asyncio.sleep(query_pause)

            if stopped_due_to_rate_limit:
                break

            site_pause = random.uniform(18, 28)
            print(f"☕ Finished {site}. Taking a {site_pause:.0f}s break...")
            await asyncio.sleep(site_pause)

        await context.close()

    if not stopped_due_to_rate_limit:
        clear_scraper_checkpoint("google_fast", conn=conn)
        print("\n🎉 Full Google scraping cycle completed! Checkpoint cleared.")

    conn.close()
    return new_jobs_added


if __name__ == "__main__":
    count = asyncio.run(scrape_google_jobs())
    print("\n" + "=" * 50)
    print(f"🎯 Scrape session ended. {count} new jobs added to database.")
    print("=" * 50)