import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import asyncio
import urllib.parse
import random
import json
import subprocess
from datetime import datetime, timezone
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# The expanded list of ATS, Portals, and Career Pages
SITES = [
    # Startup ATS
    "boards.greenhouse.io",
    "lever.co",
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
    "keka.com/careers",
]

# The "Broad Tech" Dragnet
SEARCH_QUERIES = [
    '"software engineer" intern',
    '"software developer" intern',
    '"backend" intern',
    '"full stack" intern',
    'c++ intern',
    'systems intern',
    'networking intern',
    'node.js intern',
    'react intern'
]

TIME_FILTER = "qdr:d"
MAX_PAGES = 5

DB_FILE = os.path.join(_ROOT_DIR, "jobs_db.json")

def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if "google.com/url" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]
    if "google.com/goto" in raw_url:
        return raw_url # Preserve encrypted google redirects
    return raw_url.split('?')[0]

async def resolve_url(raw_url, context):
    """Resolves Google redirects into direct ATS destination URL."""
    if not raw_url:
        return ""
    if "google.com/url" in raw_url or "/url?" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            return query_params['q'][0].split('?')[0]
        elif 'url' in query_params:
            return query_params['url'][0].split('?')[0]
    if "google.com/goto" in raw_url:
        try:
            resp = await context.request.get(raw_url, max_redirects=0, timeout=3000)
            loc = resp.headers.get("location")
            if loc:
                return loc.split('?')[0]
        except Exception:
            pass
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

import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.scrapers.ats_parser import parse_ats_info
    from src.core.db import save_company, get_conn, insert_or_ignore_job
except ImportError:
    from ats_parser import parse_ats_info
    from db import save_company, get_conn, insert_or_ignore_job

def add_job_to_db(db, title, url):
    """Add a job directly to the DB if it doesn't already exist. Returns True if new."""
    if url in db:
        return False
    
    # Fast regex check only (no network delay during Google scrape)
    ats_info = parse_ats_info(url, title=title, resolve_google=False)
    comp_name = ats_info.get("company_name") if ats_info else None
    plat = ats_info.get("ats_platform") if ats_info else None
    if ats_info:
        try:
            save_company(
                name=comp_name,
                ats_platform=plat,
                identifier=ats_info["identifier"],
                career_url=ats_info["career_url"]
            )
        except Exception:
            pass

    db[url] = {
        "title": title,
        "status": "New",
        "rank": "UNKNOWN",
        "reason": "",
        "added_at": datetime.now(timezone.utc).isoformat(),
        "company": comp_name,
        "ats_platform": plat
    }
    save_db(db)

    # Mirror to SQLite
    try:
        conn = get_conn()
        insert_or_ignore_job(url, title, conn, company=comp_name, ats_platform=plat)
        conn.commit()
        conn.close()
    except Exception:
        pass

    return True

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
    unique_jobs = set()  
    search_count = 0
    jobs_db = load_db()  
    
    # Add all existing URLs to unique_jobs so we skip them
    unique_jobs.update(jobs_db.keys())
    new_jobs_added = 0

    async with async_playwright() as p:
        import os
        user_data_dir = os.path.join(os.getcwd(), "browser_cache")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"]
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await Stealth().apply_stealth_async(page)

        print(f"🚀 Starting the Massive ATS Scraper...")
        print(f"📁 Data will auto-save to {DB_FILE} in real-time.")
        print(f"📦 {len(jobs_db)} existing jobs loaded (will skip duplicates).\n")

        try:
            from src.core.db import get_excluded_ats_sites
            excluded = set(s.lower() for s in get_excluded_ats_sites())
        except Exception:
            excluded = set()
        env_ex = os.environ.get("SCRAPER_EXCLUDED_SITES", "")
        if env_ex:
            excluded.update(s.strip().lower() for s in env_ex.split(",") if s.strip())

        sites_list = [s for s in SITES if s.lower() not in excluded]
        if excluded:
            print(f"🚫 Excluded ATS Sites ({len(excluded)}): {', '.join(sorted(excluded))}")
        print(f"🎯 Active ATS Sites ({len(sites_list)}): {', '.join(sites_list)}\n")

        for site in sites_list:
            print(f"\n=====================================")
            print(f"🏢 TARGETING: {site}")
            print(f"=====================================")
            
            for query_term in SEARCH_QUERIES:
                print(f"\n   🔍 Query: {query_term}")
                
                for start in range(0, MAX_PAGES * 10, 10):
                    search_count += 1 
                    
                    # RANDOM HOMEPAGE DETOUR 
                    if search_count % random.randint(8, 12) == 0:
                        print("      🏠 Taking a detour to Google homepage to act human...")
                        await page.goto("https://www.google.com")
                        await human_scroll_and_move(page)
                        await asyncio.sleep(random.uniform(10, 20))

                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    
                    search_url = f"https://www.google.com/search?q={encoded_query}&tbs={TIME_FILTER}&start={start}&filter=0&hl=en"
                    
                    await page.goto(search_url)

                    # Wait for search results to actually render in the DOM
                    try:
                        await page.wait_for_selector('div.yuRUbf, div.g', timeout=15000)
                    except Exception:
                        print(f"      ⚠️ Results didn't render in time, will try to extract anyway...")
                    
                    # HUMAN SCROLLING AFTER LOAD 
                    await human_scroll_and_move(page)

                    # 1. PAGE-TO-PAGE GAP
                    sleep_time = random.uniform(12, 25)
                    print(f"      ⏳ Page {int(start/10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)
                    
                    # Check for CAPTCHA
                    if "sorry/index" in page.url or await page.locator('form[action*="/sorry/index"]').count() > 0:
                        print("      ⚠️ CAPTCHA detected! Please solve it in the Chromium window.")
                        try:
                            import platform
                            if platform.system() == "Windows":
                                import ctypes, threading
                                threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(0, "Google CAPTCHA detected. Please solve it in the Chromium window.", "🚨 CAPTCHA Alert!", 0x30 | 0x0), daemon=True).start()
                            else:
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
                            print(f"      🚫 No more results for this query. Moving to next.")
                            break 

                        print(f"      📊 Found {len(results)} results on page.")
                        new_jobs_found = 0
                        target_clean = site.replace("boards.", "").replace("jobs.", "").replace("apply.", "").lower()

                        for res in results:
                            raw_url = res.get('url', '')
                            title = res.get('title', 'Unknown Title')
                            if not raw_url:
                                continue

                            final_url = await resolve_url(raw_url, context)
                            is_target_site = (site in final_url.lower() or target_clean in final_url.lower() or site in raw_url)
                            if is_target_site or "google.com/goto" in final_url:
                                if final_url not in unique_jobs:
                                    unique_jobs.add(final_url)
                                    if add_job_to_db(jobs_db, title, final_url):
                                        new_jobs_found += 1
                                        new_jobs_added += 1
                                        print(f"      💾 Found: {title[:40]} -> {final_url[:65]}")
                                else:
                                    print(f"      ⏭️ Skipped (duplicate): {final_url[:65]}")
                            else:
                                print(f"      ⏭️ Skipped (site mismatch): {final_url[:65]}")
                        
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

        await context.close()
        return new_jobs_added

if __name__ == "__main__":
    count = asyncio.run(scrape_google_jobs())
    print("\n" + "="*50)
    print(f"🎯 Scrape complete! {count} new jobs added to {DB_FILE}")
    print("="*50)