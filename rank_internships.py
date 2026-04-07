import asyncio
import re
import json
import time
import os
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from dotenv import load_dotenv
load_dotenv()

from groq import AsyncGroq

groq_client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))

# SQLite DB helper
from db import get_conn, init_db, load_db, update_job

PROMPTS_LOG_FILE = "prompts.json"

def log_prompt_to_file(job_title, prompt_type, prompt_text, result_text):
    """Appends the sent prompt and received result to prompts.json for manual review."""
    log_entry = {
        "job_title": job_title,
        "type": prompt_type,
        "prompt": prompt_text,
        "result": result_text
    }

    logs = []
    if os.path.exists(PROMPTS_LOG_FILE):
        try:
            with open(PROMPTS_LOG_FILE, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        except Exception:
            pass

    logs.append(log_entry)

    with open(PROMPTS_LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=4)

CANDIDATE_PROFILE = """
Candidate Background:

Education:
- Integrated M.Sc. Mathematics and Computing

Core Skills:
- Languages: C, C++, JavaScript, SQL
- Backend: Node.js, Express.js, REST APIs
- Frontend/Mobile: React.js, React Native
- Systems & Networking: Boost.Asio, TCP/UDP sockets, multithreading
- Databases: MongoDB, PostgreSQL
- Tools: Git, Linux, FFmpeg

Project Experience:
- Built peer-to-peer networking systems in C++ using Boost.Asio
- Developed full-stack applications using MERN stack
- Built Android/mobile apps with React Native
- Designed REST APIs and backend services

Internship Experience:
- Software Engineering Intern building AI interviewer platform using Node.js backend and React frontend.

Career Goals:
Looking for internships in:
- Software Engineering
- Backend Development
- Systems Programming
- Full-Stack Development
"""

def clean_text(text):
    """Remove excessive whitespace and non-printable characters."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def is_senior_role(text):
    """Check if the text contains keywords for senior or mid-level roles."""
    text_lower = text.lower()
    patterns = [r'\bsenior\b', r'\bsde\s*ii\b', r'\bsde\s*iii\b', r'\bsde\s*2\b', r'\bsde\s*3\b']
    for p in patterns:
        if re.search(p, text_lower):
            return True
    return False

def is_us_job(text):
    """Deterministically detect US-only jobs before hitting the LLM."""
    text = text.lower()
    us_patterns = [
        "united states", " usa", "u.s.", "u.s.a",
        "new york", "california", "texas", "washington",
        "san francisco", "seattle", "los angeles",
        "boston", "chicago", "austin",
        # Visa / work-auth keywords
        "work authorization in the us", "us work permit",
        "only us candidates", "authorized to work in the us",
        # Remote-but-US-only patterns
        "remote (us", "remote - us", "remote us only", "us remote only",
    ]
    return any(p in text for p in us_patterns)

def extract_json_from_text(text):
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        return None
    return None

def validate_job_json(data):
    required_fields = [
        "job_name",
        "company",
        "location",
        "salary",
        "job_type",
        "experience_required",
        "match_rank",
        "match_reason",
        "skills_required",
        "skills_preferred",
        "about_job",
        "responsibilities",
        "requirements",
        "nice_to_have"
    ]

    if not isinstance(data, dict):
        return False

    for field in required_fields:
        if field not in data:
            return False

    return True

async def extract_job_details_with_llm(job_title, jd_text, retries=3):
    """Extracts structured job details AND ranks the job in a single LLM call."""

    prompt = f"""
If you produce anything other than valid JSON, the system will crash.
Do not explain anything.
Only return JSON.

You are an expert technical recruiter. Do TWO things at once:
1. Extract the job details from the Job Description below.
2. Rank how well this job matches the Candidate Profile using the Ranking Rules.

====================
CANDIDATE PROFILE
====================
{CANDIDATE_PROFILE}

====================
JOB
====================
Title: {job_title}

Description:
{jd_text}
====================

RANKING RULES (apply to match_rank and match_reason fields):

CRITICAL: Evaluate the IGNORE list FIRST. If any IGNORE rule matches, set match_rank to "IGNORE" immediately.

IGNORE (HARD VETO):
- Transportation engineering, Urban planning, Traffic operations
- Civil, Mechanical, Construction engineering
- Architecture roles
- HR / Marketing / Sales
- Technical support
- QA / manual testing
- Roles requiring AutoCAD, MicroStation, SketchUp, GIS
- Titles containing: Senior, Staff, Lead, Principal, Architect, Manager
- Roles requiring more than 2 years of experience
- Roles that are NOT one of: internship, entry-level, fresher, new grad, junior, or graduate hire

HIGH:
- Software Engineering internships OR entry-level/fresher full-time roles
- Backend Engineering internships OR entry-level/fresher full-time roles
- Full Stack Development internships OR entry-level/fresher full-time roles
- Systems Programming, C / C++ roles (intern or entry-level)
- Node.js / JavaScript backend roles (intern or entry-level)
- Networking / distributed systems roles (intern or entry-level)
- Mobile app development roles (intern or entry-level)

LOW:
- General developer roles (intern or entry-level)
- Web development internships or fresher web roles
- Platform engineering, DevOps roles involving programming (entry-level)
- AI / ML engineering roles (intern or entry-level)
- Data engineering, Data analyst roles (intern or entry-level)
- Cloud infrastructure, DevOps focused mostly on operations (entry-level)

CATCH-ALL: If the job does NOT clearly match HIGH or LOW — set match_rank to "IGNORE".

====================

Respond ONLY with a valid JSON object matching EXACTLY this schema.
Use arrays for skills, responsibilities, requirements, nice_to_have.
If a field is not found, use null. Never use "" or [].

{{
  "job_name": null,
  "company": null,
  "location": null,
  "salary": null,
  "job_type": null,
  "experience_required": null,
  "match_rank": "HIGH / LOW / IGNORE",
  "match_reason": "One short sentence explaining the rank decision.",
  "skills_required": null,
  "skills_preferred": null,
  "about_job": null,
  "responsibilities": null,
  "requirements": null,
  "nice_to_have": null
}}
"""

    for attempt in range(retries):
        try:
            response = await groq_client.chat.completions.create(
                model='llama-3.1-8b-instant',
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0
            )

            result = response.choices[0].message.content.strip()

            if attempt == 0:
                log_prompt_to_file(job_title, "extraction+ranking", prompt, result)

            data = extract_json_from_text(result)

            if data and validate_job_json(data):
                return data

            print(f"   ⚠️ JSON invalid or missing fields, retry {attempt+1}/{retries}")

        except Exception as e:
            err_str = str(e)
            print(f"   ⚠️ Attempt {attempt+1} failed: {e}")

            if attempt < retries - 1:
                # Parse wait time from Groq 429 message if available
                # e.g. "Please try again in 2.55s"
                wait_seconds = None
                match = re.search(r'try again in ([\d.]+)s', err_str)
                if match:
                    wait_seconds = float(match.group(1)) + 0.5  # a small buffer

                if wait_seconds is None:
                    # Fallback: exponential backoff (5s, 10s, 20s ...)
                    wait_seconds = 5 * (2 ** attempt)

                print(f"   ⏳ Rate limited — waiting {wait_seconds:.1f}s before retry...")
                await asyncio.sleep(wait_seconds)
            continue

    return None

async def process_and_rank_jobs():
    # Open one persistent connection for the whole run
    conn = get_conn()
    init_db(conn)

    # Load only fresh unranked jobs — no need to pull the whole DB into RAM
    jobs_db = load_db(conn)
    fresh_jobs = {link: data for link, data in jobs_db.items()
                  if data.get("status") == "New" and data.get("rank", "UNKNOWN") in ["UNKNOWN", "ERROR"]}

    if not fresh_jobs:
        print("ℹ️  No fresh jobs to rank. All jobs have already been processed.")
        conn.close()
        return

    print(f"🚀 Starting JD extraction and AI ranking for {len(fresh_jobs)} fresh jobs...\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await Stealth().apply_stealth_async(page)
        page.set_default_timeout(30000)

        for index, (url, data) in enumerate(fresh_jobs.items()):
            title = data.get("title", "Unknown")
            print(f"[{index + 1}/{len(fresh_jobs)}] Extracting: {title}")

            if is_senior_role(title):
                print(f"   ⏭️ Skipped (Senior/SDE II/III detected in title)\n")
                # Write only the changed columns — no full JSON dump
                update_job(url, conn, rank="IGNORE", reason="Senior/SDE II/III role detected in title.")
                conn.commit()
                continue

            try:
                async def process_single_job():
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=20000)
                    except Exception:
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                        except Exception:
                            await asyncio.sleep(2)

                    await page.wait_for_timeout(3000)
                    raw_text = await page.locator("body").inner_text()
                    jd_text = clean_text(raw_text)[:3000]

                    if len(jd_text) < 100:
                        fallback_text = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('p, li, div, span'))
                                .map(el => el.innerText)
                                .join(' ');
                        }""")
                        jd_text = clean_text(fallback_text)[:3000]

                    if len(jd_text) < 100:
                        return "ERROR", "Failed to extract meaningful text.", None
                    else:
                        if is_senior_role(jd_text):
                            return "IGNORE", "Senior/SDE II/III role detected in job description.", None

                        combined_text = f"{title} {jd_text}"
                        if is_us_job(combined_text):
                            return "IGNORE", "US-based job.", None

                        print("   🧠 Extracting + ranking with Groq (single call)...")
                        details = await extract_job_details_with_llm(title, jd_text)

                        if details:
                            rank = details.get("match_rank", "UNKNOWN").upper()
                            reason = details.get("match_reason", "No reason provided.")
                        else:
                            rank, reason = "ERROR", "LLM extraction failed after all retries."

                        return rank, reason, details

                rank, reason, details = await asyncio.wait_for(process_single_job(), timeout=90)

                print(f"   📊 Result: {rank} - {reason}\n")
                # Targeted column update — no full-dict serialisation
                update_job(url, conn, rank=rank, reason=reason,
                           **({"details": details} if details else {}))
                conn.commit()

            except asyncio.TimeoutError:
                print(f"   ⏰ TIMEOUT after 90s. Skipping with ERROR tag.\n")
                update_job(url, conn, rank="ERROR", reason="Timed out after 90s.")
                conn.commit()

            except Exception as e:
                print(f"   ❌ Failed: {e}. Skipping.\n")
                update_job(url, conn, rank="ERROR", reason=str(e)[:100])
                conn.commit()

        await browser.close()

    conn.close()

    print("==================================================")
    print(f"✅ Finished! Ranked {len(fresh_jobs)} fresh jobs.")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(process_and_rank_jobs())
