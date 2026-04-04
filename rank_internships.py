import asyncio
import re
import json
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
    """Extracts structured job details using the local LLM."""

    prompt = f"""
If you produce anything other than valid JSON, the system will crash.
Do not explain anything.
Only return JSON.

You are a technical recruiter assistant. Extract the job details from the following Job Description.

====================
JOB
====================
Title: {job_title}

Description:
{jd_text}
====================

Respond ONLY with a valid JSON object matching EXACTLY this schema. Ensure you use arrays instead of paragraphs for skills, responsibilities, requirements, and nice_to_haves.
Do not include any other text or markdown formatting outside the JSON block.
If a field is not mentioned or you cannot find the data, you MUST use `null`. Do not use empty strings `""` or empty arrays `[]`.

{{
  "job_name": null,
  "company": null,
  "location": null,
  "salary": null,
  "job_type": null,
  "experience_required": null,
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
                model='llama-3.3-70b-versatile',
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0
            )

            result = response.choices[0].message.content.strip()

            if attempt == 0:
                log_prompt_to_file(job_title, "extraction", prompt, result)

            data = extract_json_from_text(result)

            if data and validate_job_json(data):
                return data

            print(f"   ⚠️ JSON invalid or missing fields, retry {attempt+1}/{retries}")

        except Exception as e:
            print(f"   ⚠️ Attempt {attempt+1} failed: {e}")

    return None

async def evaluate_job_with_llm(job_details_dict, raw_jd_text, job_title):
    """Sends the job description or structured details to the local LLM for ranking."""

    if job_details_dict:
        job_info = json.dumps(job_details_dict, indent=2)
    else:
        job_info = f"Title: {job_title}\n\nDescription:\n{raw_jd_text}"

    prompt = f"""
You are an experienced technical recruiter.

Evaluate how well this internship matches the candidate profile.

====================
CANDIDATE PROFILE
====================
{CANDIDATE_PROFILE}

====================
JOB DETAILS
====================
{job_info}

====================
RANKING RULES
====================

CRITICAL INSTRUCTION:
Evaluate the IGNORE list first. If the job matches ANY IGNORE rule,
immediately return IGNORE and stop evaluation.

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
- Roles that are NOT internships or entry-level student roles

HIGH:
- Software Engineering
- Backend Engineering
- Full Stack Development
- Systems Programming
- C / C++ roles
- Node.js / JavaScript backend roles
- Networking / distributed systems roles
- Mobile app development roles

MEDIUM:
- General developer roles
- Web development internships
- Platform engineering roles
- DevOps roles involving programming
- AI / ML engineering roles

LOW:
- Data engineering
- Data analyst roles
- Cloud infrastructure roles
- DevOps roles focused mostly on operations

CATCH-ALL (DEFAULT):
If the job does NOT clearly match HIGH, MEDIUM, or LOW,
you MUST return IGNORE.

====================

OUTPUT FORMAT:

RANK: HIGH / MEDIUM / LOW / IGNORE
REASON: One short sentence explaining the decision.
"""

    try:
        response = await groq_client.chat.completions.create(
            model='llama-3.3-70b-versatile',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0
        )

        result = response.choices[0].message.content.strip()

        log_prompt_to_file(job_title, "ranking", prompt, result)

        rank_match = re.search(r'RANK:\s*(HIGH|MEDIUM|LOW|IGNORE)', result, re.IGNORECASE)
        reason_match = re.search(r'REASON:\s*(.*)', result, re.IGNORECASE)

        rank = rank_match.group(1).upper() if rank_match else "UNKNOWN"
        reason = reason_match.group(1) if reason_match else result

        return rank, reason

    except Exception as e:
        return "ERROR", str(e)

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
                    jd_text = clean_text(raw_text)[:6000]

                    if len(jd_text) < 100:
                        fallback_text = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('p, li, div, span'))
                                .map(el => el.innerText)
                                .join(' ');
                        }""")
                        jd_text = clean_text(fallback_text)[:6000]

                    if len(jd_text) < 100:
                        return "ERROR", "Failed to extract meaningful text.", None
                    else:
                        if is_senior_role(jd_text):
                            return "IGNORE", "Senior/SDE II/III role detected in job description.", None

                        print("   🧠 Extracting structured details with Groq...")
                        details = await extract_job_details_with_llm(title, jd_text)

                        if details:
                            print("   🧠 Ranking Extracted JSON with Groq...")
                        else:
                            print("   ⚠️ Extraction failed. Ranking RAW Job Description with Groq...")

                        rank, reason = await evaluate_job_with_llm(details, jd_text, title)
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
