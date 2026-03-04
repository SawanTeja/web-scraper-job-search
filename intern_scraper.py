import asyncio
import urllib.parse
import random
import csv
from playwright.async_api import async_playwright

# The ATS domains to target
SITES = [
    "lever.co",
    "smartrecruiters.com",
    "boards.greenhouse.io",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "myworkdayjobs.com"
]

# Multiple specific combinations to trick Google into showing hidden results
SEARCH_QUERIES = [
    '"software engineer" (intern OR internship) 2026',
    '"sde" (intern OR internship) 2026',
    '"full stack" OR "react" (intern OR internship)',
    '"data analyst" OR "data analysis" (intern OR internship)',
    '"developer" summer internship fresher'
]

# Time filter: 'qdr:d' restricts results to the past 24 hours
TIME_FILTER = "qdr:d"

# Scrape up to 2 pages (20 results) per query to keep volume manageable
MAX_PAGES = 2 

def clean_url(raw_url):
    """
    Decodes Google redirects and removes tracking queries 
    to ensure unique job IDs.
    """
    if "google.com/url" in raw_url:
        parsed = urllib.parse.urlparse(raw_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'q' in query_params:
            raw_url = query_params['q'][0]

    # Strip tracking parameters to deduplicate (e.g., ?utm_source=...)
    clean = raw_url.split('?')[0]
    return clean

async def scrape_google_jobs():
    # Dictionary to store unique jobs. Key: Clean URL, Value: Job Title
    unique_jobs = {}

    async with async_playwright() as p:
        # headless=False opens a visible browser for manual CAPTCHA solving
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print("🚀 Starting the Multi-Query Stealth ATS Scraper...\n")

        for site in SITES:
            print(f"\n=====================================")
            print(f"🏢 TARGETING ATS: {site}")
            print(f"=====================================")
            
            # Loop through every query combination for this specific ATS
            for query_term in SEARCH_QUERIES:
                print(f"\n   🔍 Query: {query_term}")
                
                # Implemented Pagination (0, 10)
                for start in range(0, MAX_PAGES * 10, 10):
                    query = f'site:{site} {query_term}'
                    encoded_query = urllib.parse.quote_plus(query)
                    
                    search_url = f"https://www.google.com/search?q={encoded_query}&tbs={TIME_FILTER}&start={start}"
                    
                    await page.goto(search_url)
                    
                    # 1. PAGE-TO-PAGE GAP: 12-25 seconds
                    sleep_time = random.uniform(12, 25)
                    print(f"      ⏳ Page {int(start/10) + 1} loaded. Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)
                    
                    # Check for CAPTCHA
                    if "sorry/index" in page.url or await page.locator('form[action="/sorry/index"]').count() > 0:
                        print("      ⚠️ CAPTCHA detected! Please solve it in the Chromium window.")
                        await page.wait_for_selector('div#search', timeout=0) 
                    
                    try:
                        # Extract both the URL and the Google Result Title (h3)
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
                            break # Stop paginating if no results are on this page

                        for res in results:
                            raw_url = res['url']
                            title = res['title']
                            
                            if site in raw_url:
                                final_url = clean_url(raw_url)
                                # Add to dictionary (automatically handles duplicates)
                                if final_url not in unique_jobs:
                                    unique_jobs[final_url] = title
                                
                    except Exception as e:
                        print(f"      ❌ Error extracting links on page {int(start/10) + 1}: {e}")

                # 2. QUERY-TO-QUERY GAP: 30-45 seconds
                query_pause = random.uniform(30, 45)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                await asyncio.sleep(query_pause)

            # 3. SITE-TO-SITE GAP: 90-150 seconds (1.5 - 2.5 minutes)
            site_pause = random.uniform(90, 150)
            print(f"☕ Finished {site}. Taking a long {site_pause:.0f}s break...")
            await asyncio.sleep(site_pause)

        await browser.close()
        return unique_jobs

if __name__ == "__main__":
    jobs = asyncio.run(scrape_google_jobs())
    
    print("\n" + "="*50)
    print(f"🎯 Found {len(jobs)} unique internship postings!")
    print("="*50)
    
    # Export to CSV
    csv_filename = "fresh_internships.csv"
    with open(csv_filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["Job Title / Company", "Application Link"])
        
        for url, title in jobs.items():
            writer.writerow([title, url])
            
    print(f"✅ Data successfully saved to {csv_filename}")
