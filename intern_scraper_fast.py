import asyncio
import urllib.parse
import random
import subprocess
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# SQLite DB helper (replaces all json load/save logic)
from db import get_conn, init_db, insert_or_ignore_job, get_all_urls

# The expanded list of ATS, Portals, and Career Pages
SITES = [
    # Startup ATS
    "lever.co",
    "boards.greenhouse.io",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "smartrecruiters.com",

    # Enterprise ATS
    # "myworkdayjobs.com", #uncomment if you want to scrape myworkdays
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "breezy.hr",
    "recruitee.com",
]

# The "Broad Tech" Dragnet
SEARCH_QUERIES = [
    '(software engineer OR software developer) (intern OR internship) 2026',
    '(backend OR backend engineer) (intern OR internship)',
    '(full stack OR fullstack) (intern OR internship)',
    '(c++ OR systems OR networking) (intern OR internship)',
    '(node.js OR react OR javascript) (intern OR internship)'
]

TIME_FILTER = "qdr:d"
MAX_PAGES = 5

# Recycle the Chromium context (flush its RAM cache) every N searches
CONTEXT_RECYCLE_INTERVAL = 50

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if "google.com/url" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]
    return raw_url.split('?')[0]


# --- Anti-CAPTCHA Mouse and Scroll Function ---
async def human_scroll_and_move(page):
    """Simulates random human scrolling and mouse movements."""
    await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
    await asyncio.sleep(random.uniform(0.5, 1.5))

    await page.mouse.wheel(0, random.randint(400, 1200))
    await asyncio.sleep(random.uniform(2, 4))

    if random.choice([True, False]):
        await page.mouse.wheel(0, -random.randint(100, 300))
        await asyncio.sleep(random.uniform(1, 2))
# ---------------------------------------------------


async def scrape_google_jobs():
    search_count = 0
    new_jobs_added = 0

    # Open ONE persistent DB connection for the whole scrape session.
    # SQLite writes are cheap, no full-dict serialisation needed.
    conn = get_conn()
    init_db(conn)

    # Load known URLs into an in-memory set for fast duplicate checking.
    # This is just a set of strings — far smaller than the old full-dict load.
    unique_jobs = get_all_urls(conn)
    print(f"📦 {len(unique_jobs)} existing jobs loaded (will skip duplicates).\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)

        async def recycle_context():
            """Close and reopen the browser context to flush Chromium's RAM cache."""
            nonlocal context, page
            print("      ♻️  Recycling browser context to free RAM...")
            await page.close()
            await context.close()
            context = await browser.new_context(user_agent=USER_AGENT)
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

        print("🚀 Starting the Massive ATS Scraper...")
        print(f"📁 Data will auto-save to jobs_db.sqlite in real-time.\n")

        for site in SITES:
            print(f"\n=====================================")
            print(f"🏢 TARGETING: {site}")
            print(f"=====================================")

            for query_term in SEARCH_QUERIES:
                print(f"\n   🔍 Query: {query_term}")

                for start in range(0, MAX_PAGES * 10, 10):
                    search_count += 1

                    # PERIODIC CONTEXT RECYCLE — flush Chromium's growing RAM cache
                    if search_count % CONTEXT_RECYCLE_INTERVAL == 0:
                        await recycle_context()

                    # RANDOM HOMEPAGE DETOUR
                    elif search_count % random.randint(8, 12) == 0:
                        print("      🏠 Taking a detour to Google homepage to act human...")
                        await page.goto("https://www.google.com")
                        await human_scroll_and_move(page)
                        await asyncio.sleep(random.uniform(3, 7))

                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    search_url = (
                        f"https://www.google.com/search"
                        f"?q={encoded_query}&tbs={TIME_FILTER}&start={start}"
                    )

                    await page.goto(search_url)

                    # Wait for search results to actually render in the DOM
                    try:
                        await page.wait_for_selector('div.yuRUbf', timeout=15000)
                    except Exception:
                        print("      ⚠️ Results didn't render in time, will try to extract anyway...")

                    # HUMAN SCROLLING AFTER LOAD
                    await human_scroll_and_move(page)

                    # PAGE-TO-PAGE GAP
                    sleep_time = random.uniform(3, 8)
                    print(f"      ⏳ Page {int(start/10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)

                    # Check for CAPTCHA
                    if "sorry/index" in page.url or await page.locator('form[action="/sorry/index"]').count() > 0:
                        print("      ⚠️ CAPTCHA detected! Please solve it in the Chromium window.")
                        try:
                            subprocess.run([
                                "notify-send", "--urgency=critical",
                                "🚨 CAPTCHA Alert!",
                                "Google CAPTCHA detected. Please solve it in the Chromium window."
                            ], check=False)
                        except Exception:
                            pass
                        await page.wait_for_selector('div#search', timeout=0)

                    try:
                        results = await page.eval_on_selector_all(
                            "div.yuRUbf",
                            """elements => elements.map(e => {
                                let a = e.querySelector('a');
                                let h3 = e.querySelector('h3');
                                return {
                                    url: a ? a.href : '',
                                    title: h3 ? h3.innerText : 'Unknown Title'
                                };
                            })"""
                        )

                        if not results:
                            print("      🚫 No more results for this query. Moving to next.")
                            break

                        print(f"      📊 Found {len(results)} results on page.")
                        new_jobs_found = 0

                        for res in results:
                            raw_url = res['url']
                            title = res['title']

                            if site in raw_url:
                                final_url = clean_url(raw_url)
                                if final_url not in unique_jobs:
                                    unique_jobs.add(final_url)
                                    # Write directly to SQLite — no dict, no JSON dump
                                    inserted = insert_or_ignore_job(final_url, title, conn)
                                    if inserted:
                                        new_jobs_found += 1
                                        new_jobs_added += 1
                                else:
                                    print(f"      ⏭️ Skipped (duplicate): {final_url[:80]}")
                            else:
                                print(f"      ⏭️ Skipped (site mismatch): {raw_url[:80]}")

                        # Commit the batch to disk once per page (not per job)
                        if new_jobs_found > 0:
                            conn.commit()
                            print(f"      💾 Saved {new_jobs_found} new jobs to DB.")

                    except Exception as e:
                        print(f"      ❌ Error extracting links: {e}")

                # QUERY-TO-QUERY GAP
                query_pause = random.uniform(8, 15)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                await asyncio.sleep(query_pause)

            # SITE-TO-SITE GAP
            site_pause = random.uniform(20, 30)
            print(f"☕ Finished {site}. Taking a long {site_pause:.0f}s break...")
            await asyncio.sleep(site_pause)

        await browser.close()

    conn.close()
    return new_jobs_added


if __name__ == "__main__":
    count = asyncio.run(scrape_google_jobs())
    print("\n" + "="*50)
    print(f"🎯 Scrape complete! {count} new jobs added to jobs_db.sqlite")
    print("="*50)