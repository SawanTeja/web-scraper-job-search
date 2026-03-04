import asyncio
import urllib.parse
import random
import csv
import os
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
CSV_FILENAME = "fresh_internships.csv"

def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if "google.com/url" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]
    return raw_url.split('?')[0]

def append_to_csv(title, url):
    """Instantly saves a single job to the CSV to prevent data loss."""
    file_exists = os.path.isfile(CSV_FILENAME)
    with open(CSV_FILENAME, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Job Title / Company", "Application Link"])
        writer.writerow([title, url])

async def scrape_google_jobs():
    unique_jobs = set() # Changed to a set to just track URLs we've seen this run

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"🚀 Starting the Massive 2-Hour ATS Scraper...")
        print(f"📁 Data will auto-save to {CSV_FILENAME} in real-time.\n")

        for site in SITES:
            print(f"\n=====================================")
            print(f"🏢 TARGETING: {site}")
            print(f"=====================================")
            
            for query_term in SEARCH_QUERIES:
                print(f"\n   🔍 Query: {query_term}")
                
                for start in range(0, MAX_PAGES * 10, 10):
                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    
                    search_url = f"https://www.google.com/search?q={encoded_query}&tbs={TIME_FILTER}&start={start}"
                    
                    await page.goto(search_url)
                    
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
                                    append_to_csv(title, final_url) # Real-time save
                                    new_jobs_found += 1
                        
                        if new_jobs_found > 0:
                            print(f"      💾 Auto-saved {new_jobs_found} new jobs.")
                                
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
        return unique_jobs

if __name__ == "__main__":
    jobs = asyncio.run(scrape_google_jobs())
    print("\n" + "="*50)
    print(f"🎯 Scrape complete! Total unique jobs found this run: {len(jobs)}")
    print(f"✅ All data is safely secured in {CSV_FILENAME}")
    print("="*50)
