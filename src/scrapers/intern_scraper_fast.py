import asyncio
import urllib.parse
import random
import subprocess
import sys
import os
from datetime import datetime, timezone

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Auto-switch to scraper_env virtual environment if run with system python
venv_python = os.path.join(_ROOT_DIR, "scraper_env", "bin", "python")
if os.path.exists(venv_python) and sys.executable != venv_python:
    try:
        import playwright
        import playwright_stealth
    except ImportError:
        os.execv(venv_python, [venv_python] + sys.argv)

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

try:
    from playwright._impl._errors import TargetClosedError
except ImportError:
    TargetClosedError = Exception

try:
    from src.core.process_manager import clear_stop_flag
except ImportError:
    def clear_stop_flag(flag_path="stop.flag"):
        if os.path.exists(flag_path):
            try:
                os.remove(flag_path)
            except Exception:
                pass

# SQLite DB helper
try:
    from src.core.db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls, save_company
    )
    from src.scrapers.ats_parser import (
        parse_ats_info, build_google_dork, resolve_redirect_url,
        clean_company_name_from_title
    )
    from src.ranking.job_filter import is_non_tech, is_career_hub
except ImportError:
    from db import (
        get_conn, init_db, insert_or_ignore_job, get_all_urls, save_company
    )
    try:
        from ats_parser import (
            parse_ats_info, build_google_dork, resolve_redirect_url,
            clean_company_name_from_title
        )
    except ImportError:
        clean_company_name_from_title = lambda t: ""
    try:
        from ranking.job_filter import is_non_tech, is_career_hub
    except ImportError:
        is_non_tech = lambda t, jd="": (False, "")
        is_career_hub = lambda t: (False, "")

# Supported ATS domains for career page searches
SITES = [
    # Top Startup ATS
    "boards.greenhouse.io",
    "lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "smartrecruiters.com",

    # Enterprise ATS
    "myworkdayjobs.com",
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "successfactors.com",
    "taleo.net",
    "oraclecloud.com",
    "keka.com",
]

# The "Broad Tech" Dragnet (proven query format from 30-40 versions ago)
# Broad terms cast a wide net across Google to discover companies and both full-time and intern jobs
SEARCH_QUERIES = [
    'Software',
    'C++ OR Java',
    'Javascript OR React OR Node.js',
    'intern OR internship'
]

# TIME_FILTER: "qdr:d" (past 24h) by default to dragnet fresh indexed jobs and active companies,
# but can be overridden via SCRAPER_TIME_FILTER env var or --time-filter CLI flag
_raw_tf = os.environ.get("SCRAPER_TIME_FILTER", "").strip().lower()
if _raw_tf in ("all", "none", "off"):
    TIME_FILTER = ""
elif _raw_tf in ("day", "qdr:d"):
    TIME_FILTER = "qdr:d"
elif _raw_tf in ("week", "qdr:w"):
    TIME_FILTER = "qdr:w"
elif _raw_tf in ("month", "qdr:m"):
    TIME_FILTER = "qdr:m"
elif _raw_tf:
    TIME_FILTER = _raw_tf
else:
    TIME_FILTER = "qdr:d"

MAX_PAGES = int(os.environ.get("SCRAPER_MAX_PAGES", 10))

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

VALID_ATS_DOMAINS = [
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "jobvite.com", "bamboohr.com",
    "successfactors.com", "taleo.net", "oraclecloud.com",
    "myworkdayjobs.com", "icims.com", "breezy.hr", "recruitee.com",
    "keka.com"
]

NON_JOB_JUNK_DOMAINS = [
    "youtube.com", "oxfordlearnersdictionaries.com", "dictionary.com",
    "wikipedia.org", "reddit.com", "support.google.com", "google.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com", "medium.com",
    "quora.com", "github.com", "amazon.com", "apple.com",
    "geeksforgeeks.org", "tme.com", "damien-musy.fr"
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


def unpack_google_url(raw_url: str) -> str:
    """
    Instantly decodes Google redirect tracking links (/url?q=, /url?url=, /url?target=)
    to true ATS destination URL without network requests.
    """
    if not raw_url:
        return ""
    url = raw_url.strip()

    # Direct ATS URL already (no Google redirect)
    if "google.com" not in url:
        return url.split('?')[0] if '?' in url else url

    # Google unpackable redirect (/url?q=... or /url?url=...)
    if "google.com/url" in url or "/url?" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            query_params = urllib.parse.parse_qs(parsed.query)
            for key in ('q', 'url', 'target', 'u', 'dest'):
                if key in query_params and query_params[key]:
                    target = query_params[key][0]
                    if target.startswith("http") and "google.com" not in target:
                        return target.split('?')[0] if '?' in target else target
        except Exception:
            pass

    return url


def clean_cite_to_url(cite_text: str) -> str:
    """
    Extracts and normalizes clean ATS destination URL from Google card <cite> breadcrumbs.
    e.g. 'https://boards.greenhouse.io › mark43 › jobs › 8360301'
    -> 'https://boards.greenhouse.io/mark43/jobs/8360301'
    """
    if not cite_text:
        return ""
    cleaned = cite_text.split("·")[0].split("...")[0].strip()
    parts = [
        p.strip() for p in cleaned.replace("›", "/").replace(">", "/").split("/")
        if p.strip()
    ]
    if not parts:
        return ""
    if parts[0].startswith("http:"):
        parts[0] = "https:"
    elif not parts[0].startswith("https:"):
        parts.insert(0, "https:")
    if len(parts) >= 2:
        return parts[0] + "//" + "/".join(parts[1:])
    return ""


def clean_url(raw_url):
    """Decodes Google redirects and removes tracking queries."""
    if not raw_url:
        return ""
    direct = unpack_google_url(raw_url)
    if "google.com/goto" in raw_url and direct == raw_url:
        return raw_url  # Preserve encrypted google redirects
    return direct


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

    # Check if target site is in URL
    if target_site:
        clean_target = target_site.replace("jobs.", "").replace("boards.", "").replace("apply.", "").split("/")[0].lower()
        if clean_target in url_lower:
            return True

    # Or any recognized ATS platform
    return any(d in url_lower for d in VALID_ATS_DOMAINS)


async def resolve_url(raw_url, context=None, cite_url=None):
    """
    Resolves Google redirects into true, direct ATS destination URL.
    Decodes standard Google redirects synchronously, utilizes DOM <cite> breadcrumbs,
    and queries network only if encrypted /goto redirect is present.
    """
    if not raw_url:
        return ""

    # 1. Try zero-network instant decode
    direct = unpack_google_url(raw_url)
    if direct and "google.com" not in direct:
        return direct

    # 2. Try reconstructing direct ATS URL from <cite> breadcrumbs (zero network I/O)
    if cite_url:
        cleaned_cite = clean_cite_to_url(cite_url)
        if cleaned_cite and "google.com" not in cleaned_cite:
            return cleaned_cite

    # 3. Google encrypted redirect (/goto?url=CAES...)
    if "google.com/goto" in raw_url:
        if context:
            # Try resolving via Playwright context request
            try:
                resp = await context.request.get(
                    raw_url,
                    max_redirects=0,
                    timeout=3500,
                    headers={"User-Agent": USER_AGENT}
                )
                loc = resp.headers.get("location")
                if loc and "google.com" not in loc:
                    return loc.split('?')[0] if '?' in loc else loc
            except Exception:
                pass

        # Try urllib redirect handler
        try:
            loop = asyncio.get_running_loop()
            target = await asyncio.wait_for(
                loop.run_in_executor(None, resolve_redirect_url, raw_url),
                timeout=3.0
            )
            if target and target != raw_url and "google.com" not in target:
                return target.split('?')[0] if '?' in target else target
        except Exception:
            pass

        return ""

    return direct if "google.com" not in direct else ""


# --- Anti-CAPTCHA Mouse and Scroll Function ---
async def human_scroll_and_move(page):
    """Simulates realistic human scrolling down the page to trigger DOM hydration of all 10 cards."""
    try:
        await page.mouse.move(random.randint(200, 700), random.randint(150, 450))
        await asyncio.sleep(random.uniform(0.3, 0.6))

        # Scroll down in stages to trigger lazy loading for all search cards
        await page.mouse.wheel(0, random.randint(400, 700))
        await asyncio.sleep(random.uniform(1.0, 1.8))

        await page.mouse.wheel(0, random.randint(300, 600))
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Natural small adjustment scroll
        if random.choice([True, False]):
            await page.mouse.wheel(0, -random.randint(150, 300))
            await asyncio.sleep(random.uniform(0.4, 0.8))
    except Exception:
        pass
# ---------------------------------------------------


async def scrape_google_jobs(max_pages=None, sites_override=None, time_filter_override=None, pace="fast"):
    clear_stop_flag()
    search_count = 0
    new_jobs_added = 0
    pages_limit = max_pages or MAX_PAGES
    active_time_filter = TIME_FILTER if time_filter_override is None else time_filter_override

    # Pace timing profiles
    if pace == "overnight":
        page_gap_range = (10.0, 15.0)
        query_gap_range = (25.0, 45.0)
        site_gap_range = (35.0, 60.0)
    elif pace == "medium":
        page_gap_range = (6.0, 9.0)
        query_gap_range = (14.0, 22.0)
        site_gap_range = (20.0, 35.0)
    else:  # "fast"
        page_gap_range = (4.0, 6.5)
        query_gap_range = (10.0, 16.0)
        site_gap_range = (15.0, 25.0)

    # Open ONE persistent DB connection for the whole scrape session.
    conn = get_conn()
    init_db(conn)

    # Purge any corrupt 'google.com' entries from past runs
    try:
        c = conn.cursor()
        c.execute("DELETE FROM jobs WHERE url = 'https://www.google.com/goto' OR url = 'https://www.google.com/url'")
        if c.rowcount > 0:
            conn.commit()
    except Exception:
        pass

    # Load known URLs into an in-memory set for fast duplicate checking.
    unique_jobs = {u for u in get_all_urls(conn) if "google.com" not in u}
    print(f"📦 {len(unique_jobs)} existing jobs loaded (will skip duplicates).\n")

    sites_list = list(sites_override or SITES)

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

        print("🚀 Starting the Massive ATS Scraper...")
        print("📁 Data will auto-save to jobs_db.sqlite in real-time.\n")

        for site in sites_list:
            if os.path.exists("stop.flag"):
                print("🛑 Stop flag detected! Stopping Google scraper cleanly.")
                clear_stop_flag()
                conn.close()
                return new_jobs_added

            clean_site = site.lower().strip().split("://")[-1].split("/")[0]

            print(f"\n=====================================")
            print(f"🏢 TARGETING: {clean_site}")
            print(f"=====================================")

            for query_term in SEARCH_QUERIES:
                if os.path.exists("stop.flag"):
                    print("🛑 Stop flag detected! Stopping Google scraper cleanly.")
                    clear_stop_flag()
                    conn.close()
                    return new_jobs_added

                raw_query = build_google_dork(clean_site, query_term)
                print(f"\n   🔍 Query: {query_term}  ->  [{raw_query}]")
                consecutive_duplicate_pages = 0

                for start in range(0, pages_limit * 10, 10):
                    if os.path.exists("stop.flag"):
                        print("      🛑 Stop flag detected! Stopping Google scraper cleanly.")
                        clear_stop_flag()
                        conn.close()
                        return new_jobs_added

                    search_count += 1

                    # Periodic tab recycling every 25 searches to clear Chromium DOM/V8 heap bloat
                    if search_count > 1 and search_count % 25 == 0:
                        try:
                            print("      🔄 Recycling browser tab to release DOM memory...")
                            await page.close()
                            page = await context.new_page()
                            await Stealth().apply_stealth_async(page)
                        except Exception:
                            pass

                    # RANDOM HOMEPAGE DETOUR
                    if search_count % random.randint(12, 18) == 0:
                        print("      🏠 Taking a detour to Google homepage to act human...")
                        try:
                            await page.goto("https://www.google.com")
                            await human_scroll_and_move(page)
                            await asyncio.sleep(random.uniform(2, 4))
                        except Exception as e:
                            if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                                print("      ⚠️ Browser closed by user. Exiting cleanly.")
                                conn.close()
                                return new_jobs_added

                    encoded_query = urllib.parse.quote_plus(raw_query)
                    used_direct_search = False

                    # For page 1 (start == 0):
                    # Enter query into Google's search box and press Enter like a human!
                    # This triggers Google's client-side search engine with authentic session tokens
                    # (sclient=gws-wiz-serp, gs_lp, sxsrf, sca_esv) instead of triggering anti-bot zero-result filters.
                    if start == 0:
                        try:
                            # 1. Ensure page is on Google and scrolled to top so search input is visible
                            if "google.com" not in page.url or "sorry/index" in page.url:
                                url_params = [f"q={encoded_query}"]
                                if active_time_filter:
                                    url_params.append(f"tbs={active_time_filter}")
                                url_params.append("hl=en")
                                base_url = f"https://www.google.com/search?{'&'.join(url_params)}"
                                await page.goto(base_url, timeout=30000)
                                await asyncio.sleep(1.0)
                            else:
                                await page.evaluate("window.scrollTo(0, 0)")
                                await asyncio.sleep(0.3)

                            # 2. Locate visible search input
                            search_input = page.locator('textarea[name="q"]:not([type="hidden"]), input[name="q"]:not([type="hidden"])').first
                            try:
                                await search_input.scroll_into_view_if_needed()
                                await search_input.wait_for(state="visible", timeout=4000)
                            except Exception:
                                pass

                            if await search_input.count() > 0:
                                print(f"      ⌨️ Typing search query into Google: '{raw_query}'...")
                                await search_input.click()
                                await search_input.fill("")
                                await asyncio.sleep(0.1)
                                await search_input.fill(raw_query)
                                await asyncio.sleep(random.uniform(0.3, 0.5))
                                await search_input.press("Enter")
                                used_direct_search = True
                        except Exception as e:
                            print(f"      ℹ️ Search box input fallback: {e}")

                        # Fallback for page 1 if typing didn't succeed
                        if not used_direct_search:
                            url_params = [f"q={encoded_query}"]
                            if active_time_filter:
                                url_params.append(f"tbs={active_time_filter}")
                            url_params.append("hl=en")
                            search_url = f"https://www.google.com/search?{'&'.join(url_params)}"
                            print(f"      🌐 Direct navigation fallback to '{raw_query}'...")
                            try:
                                await page.goto(search_url, timeout=30000)
                                used_direct_search = True
                            except Exception as e:
                                if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                                    print("      ⚠️ Browser closed by user. Exiting cleanly.")
                                    conn.close()
                                    return new_jobs_added
                                print(f"      ⚠️ Navigation error: {e}")

                    # For subsequent pages (start > 0):
                    # Click Google's "Next" button (#pnnext, a:has-text("Next"), etc.)!
                    # This utilizes Google's authentic session tokens without triggering 0-result anti-bot screens.
                    if not used_direct_search and start > 0:
                        try:
                            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            await asyncio.sleep(0.5)

                            next_btn = page.locator('#pnnext, a:has-text("Next >"), a:has-text("Next"), a[aria-label="Next page"]').first
                            if await next_btn.count() > 0 and await next_btn.is_visible():
                                print(f"      👉 Clicking 'Next' page button on Google...")
                                await next_btn.click()
                                used_direct_search = True
                            else:
                                print(f"      🚫 No 'Next' page button on Google. Reached the end of results for this query.")
                                break
                        except Exception:
                            print(f"      🚫 Could not navigate to next page. Ending query.")
                            break

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
                        # Poll until user solves CAPTCHA or closes browser window
                        while True:
                            try:
                                if page.is_closed():
                                    print("      ⚠️ Browser closed by user. Exiting cleanly.")
                                    conn.close()
                                    return new_jobs_added

                                current_url = page.url
                                has_sorry = "sorry/index" in current_url or await page.locator('form[action*="/sorry/index"]').count() > 0

                                if not has_sorry:
                                    print("      ✅ CAPTCHA solved! Resuming scraping...")
                                    try:
                                        await page.wait_for_load_state("domcontentloaded", timeout=8000)
                                    except Exception:
                                        pass
                                    await asyncio.sleep(2)
                                    if "q=" not in page.url and "/search" not in page.url:
                                        print("      🔄 Returning to search results...")
                                        url_params = [f"q={encoded_query}"]
                                        if active_time_filter:
                                            url_params.append(f"tbs={active_time_filter}")
                                        url_params.append("hl=en")
                                        await page.goto(f"https://www.google.com/search?{'&'.join(url_params)}")
                                        await asyncio.sleep(2)
                                    break
                            except Exception as e:
                                if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                                    print("      ⚠️ Browser closed by user. Exiting cleanly.")
                                    conn.close()
                                    return new_jobs_added

                            await asyncio.sleep(1)

                    # Wait for search results to render in DOM
                    try:
                        await page.wait_for_selector('a:has(h3), div.yuRUbf, div.g', timeout=10000)
                    except Exception:
                        pass

                    # HUMAN SCROLLING AFTER LOAD
                    await human_scroll_and_move(page)

                    # PAGE-TO-PAGE GAP (realistic human reading pace so DOM is fully rendered)
                    sleep_time = random.uniform(*page_gap_range)
                    print(f"      ⏳ Page {int(start / 10) + 1} loaded. Pausing {sleep_time:.1f}s...")
                    await asyncio.sleep(sleep_time)

                    try:
                        # Comprehensive multi-strategy extraction: queries result cards and heading anchors
                        results = await page.evaluate(
                            """() => {
                                let list = [];
                                let seenUrls = new Set();

                                function cleanCite(citeText) {
                                    if (!citeText) return '';
                                    let cleaned = citeText.split('·')[0].split('...')[0].trim();
                                    let parts = cleaned.replace(/›/g, '/').replace(/>/g, '/').split('/').map(p => p.trim()).filter(Boolean);
                                    if (parts.length >= 2) {
                                        if (parts[0] === 'http:' || parts[0] === 'http') parts[0] = 'https:';
                                        else if (parts[0] !== 'https:' && parts[0] !== 'https') parts.unshift('https:');
                                        let domain = parts[1];
                                        let path = parts.slice(2).join('/');
                                        return 'https://' + domain + (path ? '/' + path : '');
                                    }
                                    return '';
                                }

                                function addResult(url, title, citeUrl, rawUrl) {
                                    if (!url || typeof url !== 'string') return;
                                    let clean = url.trim();
                                    if (!clean.startsWith('http')) return;
                                    if (clean.includes('google.com/search') ||
                                        clean.includes('google.com/preferences') ||
                                        clean.includes('google.com/advanced_search') ||
                                        clean.includes('accounts.google.com') ||
                                        clean.includes('support.google.com')) {
                                        return;
                                    }

                                    // Prefer direct reconstructed ATS URL over Google /goto redirect
                                    let finalUrl = clean;
                                    if ((clean.includes('google.com/goto') || clean.includes('google.com/url')) && citeUrl) {
                                        finalUrl = citeUrl;
                                    }

                                    if (!seenUrls.has(finalUrl)) {
                                        seenUrls.add(finalUrl);
                                        list.push({
                                            url: finalUrl,
                                            title: title && title.trim() ? title.trim() : 'Unknown Title',
                                            cite_url: citeUrl || '',
                                            raw_url: rawUrl || clean
                                        });
                                    }
                                }

                                // 1. Scan primary organic result containers
                                let cards = document.querySelectorAll('div.MjjYud, div.g, div.tF2Cxc, div.yuRUbf');
                                for (let card of cards) {
                                    let a = card.querySelector('div.yuRUbf a[href^="http"]') ||
                                            card.querySelector('a:has(h3)') ||
                                            card.querySelector('h3 a[href^="http"]') ||
                                            card.querySelector('a[href^="http"]:has(h3)') ||
                                            card.querySelector('a[href^="http"]');
                                    let h3 = card.querySelector('h3, div[role="heading"]');
                                    let cite = card.querySelector('cite');
                                    let citeUrl = cite ? cleanCite(cite.innerText) : '';
                                    if (a && a.href) {
                                        let title = h3 && h3.innerText ? h3.innerText : (a.innerText || 'Unknown Title');
                                        addResult(a.href, title, citeUrl, a.href);
                                    }
                                }

                                // 2. Also scan all h3 elements to catch any non-standard card structures
                                let h3s = document.querySelectorAll('h3');
                                for (let h3 of h3s) {
                                    let a = h3.closest('a') || h3.querySelector('a') || (h3.parentElement ? h3.parentElement.querySelector('a') : null);
                                    let cite = h3.closest('div.MjjYud, div.g')?.querySelector('cite');
                                    let citeUrl = cite ? cleanCite(cite.innerText) : '';
                                    if (a && a.href) {
                                        addResult(a.href, h3.innerText || a.innerText, citeUrl, a.href);
                                    }
                                }

                                return list;
                            }"""
                        )

                        if not results:
                            print("      🚫 No more results for this query. Moving to next.")
                            break

                        print(f"      📊 Extracted {len(results)} search results from Google page.")
                        new_jobs_found = 0
                        new_companies_found = 0
                        valid_ats_count_on_page = 0

                        # Batch resolve all Google redirects on the page in parallel
                        raw_urls = [res.get('url', '') for res in results]
                        cite_urls = [res.get('cite_url', '') for res in results]
                        resolved_urls = await asyncio.gather(
                            *(resolve_url(u, context, c) for u, c in zip(raw_urls, cite_urls)),
                            return_exceptions=True
                        )

                        for res, resolved in zip(results, resolved_urls):
                            if isinstance(resolved, Exception) or not resolved:
                                print(f"      ⚠️ Skipped (unresolved URL): {res.get('title', '')[:35]} -> {res.get('url', '')[:50]}")
                                continue
                            final_url = resolved
                            title = res.get('title', 'Unknown Title')
                            cite_url = res.get('cite_url', '')

                            # STRICT VALIDATION: Ensure URL belongs to target site or recognized ATS, rejecting YouTube/Reddit/Junk
                            if not is_valid_job_url(final_url, clean_site):
                                print(f"      ⏭️ Skipped (non-ATS domain): {title[:35]} -> {final_url[:50]}")
                                continue

                            valid_ats_count_on_page += 1

                            # 1. ALWAYS parse and save ATS company for EVERY valid ATS URL found on Google
                            # (Fills in company pool even if URL is a career hub, non-tech role, or already-scraped job)
                            ats_info = parse_ats_info(final_url, title=title, resolve_google=False)
                            if not ats_info and cite_url:
                                ats_info = parse_ats_info(cite_url, title=title, resolve_google=False)

                            comp_name = ats_info.get("company_name") if ats_info else None
                            plat = ats_info.get("ats_platform") if ats_info else None

                            # Fallback: extract clean company name from job title if pattern match was missing
                            if not comp_name and title:
                                comp_name = clean_company_name_from_title(title)
                                if comp_name and not plat:
                                    plat = clean_site.replace("boards.", "").replace("jobs.", "").replace("apply.", "").split(".")[0]

                            if ats_info:
                                try:
                                    saved = save_company(
                                        name=comp_name,
                                        ats_platform=plat,
                                        identifier=ats_info["identifier"],
                                        career_url=ats_info["career_url"],
                                        conn=conn
                                    )
                                    if saved:
                                        new_companies_found += 1
                                        print(f"      🏢 [New Company Saved] {comp_name or ats_info['identifier']} ({plat}) -> {ats_info['career_url']}")
                                except Exception:
                                    pass
                            elif comp_name and plat:
                                try:
                                    ident = comp_name.lower().replace(" ", "-")
                                    career_url = f"https://{clean_site}/{ident}"
                                    saved = save_company(
                                        name=comp_name,
                                        ats_platform=plat,
                                        identifier=ident,
                                        career_url=career_url,
                                        conn=conn
                                    )
                                    if saved:
                                        new_companies_found += 1
                                        print(f"      🏢 [New Company Saved] {comp_name} ({plat}) -> {career_url}")
                                except Exception:
                                    pass

                            # 2. Skip career hubs from being inserted as individual job listings
                            hub, _ = is_career_hub(title)
                            if hub:
                                print(f"      ⏭️ Skipped job row (career hub): {title[:45]}")
                                continue

                            # 3. Quick sanity filter: skip obvious non-tech roles
                            non_tech, non_reason = is_non_tech(title)
                            if non_tech:
                                print(f"      ⏭️ Skipped job row (non-tech: {non_reason}): {title[:45]}")
                                continue

                            # 4. Insert new unique job into SQLite
                            if final_url not in unique_jobs:
                                unique_jobs.add(final_url)

                                inserted = insert_or_ignore_job(
                                    final_url, title, conn,
                                    status="New",
                                    rank="UNKNOWN",
                                    reason="",
                                    company=comp_name,
                                    ats_platform=plat
                                )
                                if inserted:
                                    new_jobs_found += 1
                                    new_jobs_added += 1
                                    print(f"      💾 Found: {title[:40]} -> {final_url[:65]}")
                            else:
                                print(f"      ⏭️ Skipped (duplicate): {title[:35]} -> {final_url[:55]}")

                        # If an entire page had ZERO valid target ATS results (Google relaxed), break out cleanly
                        if valid_ats_count_on_page == 0:
                            print(f"      🚫 0 valid {clean_site} results on page (Google relaxed results). Moving to next query.")
                            break

                        # Commit the batch to disk once per page
                        if new_jobs_found > 0 or new_companies_found > 0:
                            conn.commit()
                            status_items = []
                            if new_jobs_found > 0:
                                status_items.append(f"{new_jobs_found} new jobs")
                            if new_companies_found > 0:
                                status_items.append(f"{new_companies_found} new companies")
                            print(f"      💾 Saved {' and '.join(status_items)} to DB.")
                            consecutive_duplicate_pages = 0
                        else:
                            consecutive_duplicate_pages += 1
                            if consecutive_duplicate_pages >= 4:
                                print(f"      ⏩ 4 consecutive duplicate pages (0 new jobs/companies). Fast-forwarding to next query.")
                                break

                    except Exception as e:
                        if "Target page, context or browser has been closed" in str(e) or isinstance(e, TargetClosedError):
                            print("      ⚠️ Browser closed by user. Exiting cleanly.")
                            conn.close()
                            return new_jobs_added
                        print(f"      ❌ Error extracting links: {e}")

                # QUERY-TO-QUERY GAP (interruptible)
                query_pause = random.uniform(*query_gap_range)
                print(f"   🛑 Finished query block. Resting for {query_pause:.0f}s...")
                for _ in range(int(query_pause)):
                    if os.path.exists("stop.flag"):
                        print("   🛑 Stop flag detected! Stopping Google scraper cleanly.")
                        clear_stop_flag()
                        conn.close()
                        return new_jobs_added
                    await asyncio.sleep(1)

            # SITE-TO-SITE GAP (interruptible)
            site_pause = random.uniform(*site_gap_range)
            print(f"☕ Finished {clean_site}. Taking a {site_pause:.0f}s break...")
            for _ in range(int(site_pause)):
                if os.path.exists("stop.flag"):
                    print("🛑 Stop flag detected! Stopping Google scraper cleanly.")
                    clear_stop_flag()
                    conn.close()
                    return new_jobs_added
                await asyncio.sleep(1)

        await context.close()

    conn.close()
    return new_jobs_added


if __name__ == "__main__":
    clear_stop_flag()
    import argparse
    parser = argparse.ArgumentParser(description="Fast Google ATS Job Scraper")
    parser.add_argument("--pages", type=int, default=None, help=f"Max pages to scrape per query (default: {MAX_PAGES})")
    parser.add_argument("--site", type=str, default=None, help="Target specific ATS site (e.g. boards.greenhouse.io)")
    parser.add_argument("--time-filter", type=str, default=None, choices=["all", "day", "week", "month", "qdr:d", "qdr:w", "qdr:m"], help="Google time filter (default: day)")
    args = parser.parse_args()

    tf_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "all": ""}
    tf_val = tf_map.get(args.time_filter.lower(), args.time_filter) if args.time_filter else None
    sites_to_run = [args.site] if args.site else None

    count = asyncio.run(scrape_google_jobs(max_pages=args.pages, sites_override=sites_to_run, time_filter_override=tf_val))
    print("\n" + "=" * 50)
    print(f"🎯 Scrape complete! {count} new jobs added to jobs_db.sqlite")
    print("=" * 50)
