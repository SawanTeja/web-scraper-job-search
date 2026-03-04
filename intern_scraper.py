import asyncio
import urllib.parse
import random
import json
import os
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# The expanded list of ATS, Portals, and Career Pages
SITES = [
    # --- Core Startup ATS ---
    "lever.co",
    "boards.greenhouse.io",
    "jobs.ashbyhq.com",
    "smartrecruiters.com",
    "apply.workable.com",

    # --- Enterprise ATS ---
    "myworkdayjobs.com",
    "icims.com",
    "taleo.net",
    "jobs.brassring.com",
    "eightfold.ai",
    "phenompro.com",
    "jobvite.com",
    "bamboohr.com",
    "breezy.hr",
    "recruitee.com",

    # --- Regional & Startup Tech Portals ---
    "wellfound.com",
    "ycombinator.com/jobs",
    "instahyre.com",
    "hirist.tech",
    "cutshort.io",

    # --- Fresher / Campus Portals ---
    "unstop.com",
    "simplify.jobs",

    # --- Big Tech Career Pages ---
    "careers.google.com",
    "careers.microsoft.com",
    "amazon.jobs",
    "metacareers.com",
    "jobs.apple.com",

    # --- LinkedIn Recruiters ---
    "linkedin.com/posts/",
    "linkedin.com/feed/update/"
]

# The "Broad Tech" Dragnet (Ensures we catch oddly-named SDE roles)
SEARCH_QUERIES = [
    '(software OR developer OR engineering) (intern OR internship) 2026',
    '(data OR analytics OR quantitative) (intern OR internship) 2026',
    '(backend OR frontend OR fullstack OR systems) (intern OR internship)',
    '(c++ OR react OR node) (intern OR internship)'
]

TIME_FILTER = "qdr:d"
MAX_PAGES = 5
DB_FILE = "jobs_db.json"

def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if "google.com/url" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]
    return raw_url.split('?')[0]

def load_db():
    """Load the jobs database from disk."""
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_db(db):
    """Save the jobs database to disk."""
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, indent=4)

def add_job_to_db(db, title, url):
    """Add a job directly to the DB if it doesn't already exist. Returns True if new."""
    if url in db:
        return False
    db[url] = {
        "title": title,
        "status": "New",
        "rank": "UNKNOWN",
        "reason": "",
        "added_at": datetime.now(timezone.utc).isoformat()
    }
    save_db(db)
    return True

# --- NEW: Anti-CAPTCHA Mouse and Scroll Function ---
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
    unique_jobs = set()  # Track URLs we've seen this run
    search_count = 0
    jobs_db = load_db()  # Load existing DB for deduplication
    # Also add all existing URLs to unique_jobs so we skip them
    unique_jobs.update(jobs_db.keys())
    new_jobs_added = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"🚀 Starting the Massive 2-Hour ATS Scraper...")
        print(f"📁 Data will auto-save to {DB_FILE} in real-time.")
        print(f"📦 {len(jobs_db)} existing jobs loaded (will skip duplicates).\n")

        for site in SITES:
            print(f"\n=====================================")
            print(f"🏢 TARGETING: {site}")
            print(f"=====================================")
            
            for query_term in SEARCH_QUERIES:
                print(f"\n   🔍 Query: {query_term}")
                
                for start in range(0, MAX_PAGES * 10, 10):
                    search_count += 1 # --- NEW ---
                    
                    # --- NEW: RANDOM HOMEPAGE DETOUR ---
                    if search_count % random.randint(8, 12) == 0:
                        print("      🏠 Taking a detour to Google homepage to act human...")
                        await page.goto("https://www.google.com")
                        await human_scroll_and_move(page)
                        await asyncio.sleep(random.uniform(10, 20))
                    # -----------------------------------

                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    
                    search_url = f"https://www.google.com/search?q={encoded_query}&tbs={TIME_FILTER}&start={start}"
                    
                    await page.goto(search_url)
                    
                    # --- NEW: HUMAN SCROLLING AFTER LOAD ---
                    await human_scroll_and_move(page)
                    # ---------------------------------------

                    # 1. PAGE-TO-PAGE GAP
                    sleep_time = random.uniform(12, 25)
                    print(f"      ⏳ Page {int(start/10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)
                    
                    # Check for CAPTCHA
                    if "sorry/index" in page.url or await page.locator('form[action="/sorry/index"]').count() > 0:
                        print("      ⚠️ CAPTCHA detected! Please solve it in the Chromium window.")
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
                            print(f"      🚫 No more results for this query. Moving to next.")
                            break 

                        new_jobs_found = 0
                        for res in results:
                            raw_url = res['url']
                            title = res['title']
                            
                            if site in raw_url:
                                final_url = clean_url(raw_url)
                                if final_url not in unique_jobs:
                                    unique_jobs.add(final_url)
                                    if add_job_to_db(jobs_db, title, final_url):
                                        new_jobs_found += 1
                                        new_jobs_added += 1
                        
                        if new_jobs_found > 0:
                            print(f"      💾 Auto-saved {new_jobs_found} new jobs to DB.")
                                
                    except Exception as e:
                        print(f"      ❌ Error extracting links: {e}")

                # 2. QUERY-TO-QUERY GAP
                query_pause = random.uniform(30, 45)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                await asyncio.sleep(query_pause)

            # 3. SITE-TO-SITE GAP
            site_pause = random.uniform(90, 150)
            print(f"☕ Finished {site}. Taking a long {site_pause:.0f}s break...")
            await asyncio.sleep(site_pause)

        await browser.close()
        return new_jobs_added

if __name__ == "__main__":
    count = asyncio.run(scrape_google_jobs())
    print("\n" + "="*50)
    print(f"🎯 Scrape complete! {count} new jobs added to {DB_FILE}")
    print("="*50)