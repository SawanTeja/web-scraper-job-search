import asyncio
import re
import ollama
import json
import os
from playwright.async_api import async_playwright

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
        "nice_to_have",
        "ranking_reasoning",
        "final_rank"
    ]

    if not isinstance(data, dict):
        return False

    for field in required_fields:
        if field not in data:
            return False

    return True

async def process_and_rank_job_with_llm(job_title, jd_text, retries=3):
    """Extracts structured job details and ranks the job using the local LLM in a single pass."""

    prompt = f"""
If you produce anything other than valid JSON, the system will crash.
Do not explain anything.
Only return JSON.

You are a technical recruiter. Your task is to extract job details AND rank the job against the CANDIDATE PROFILE in a single pass.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

====================
JOB
====================
Title: {job_title}

Description:
{jd_text}
====================

INSTRUCTIONS for Ranking:
Assign exactly ONE of the following ranks based on the job requirements. Evaluate in this order:

1. IGNORE:
   - Senior, Lead, Manager, or non-internship/non-entry level roles.
   - Requires >2 years of experience.
   - Non-software roles (e.g., Civil, Mechanical, HR, QA, Tech Support, Transportation).
   - Jobs located in the USA (this includes ANY US state or city like California, CA, New York, NY, Texas, TX, Seattle, SF, etc. You must filter these out!).
2. HIGH: Software Engineering, Backend, Full Stack, C/C++, Node.js, Systems, Mobile.
3. MEDIUM: General Web Dev, Platform, AI/ML, DevOps (programming-focused).
4. LOW: Data Analyst, Data Engineering, Cloud/IT operations.
5. If none of the above match, default to IGNORE.

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
  "nice_to_have": null,
  "ranking_reasoning": "...",
  "final_rank": "HIGH|MEDIUM|LOW|IGNORE"
}}
"""

    for attempt in range(retries):
        try:
            response = ollama.chat(model='gemma2:9b', messages=[
                {'role': 'user', 'content': prompt}
            ], format='json', options={"temperature": 0, "num_ctx": 8192})

            result = response['message']['content'].strip()

            if attempt == 0:
                log_prompt_to_file(job_title, "extraction_and_ranking", prompt, result)

            data = extract_json_from_text(result)

            if data and validate_job_json(data):
                return data

            print(f"   ⚠️ JSON invalid or missing fields, retry {attempt+1}/{retries}")

        except Exception as e:
            print(f"   ⚠️ Attempt {attempt+1} failed: {e}")

    return None

async def process_and_rank_jobs():
    if os.path.exists("stop.flag"):
        try:
            os.remove("stop.flag")
        except Exception:
            pass

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

        for index, (url, data) in enumerate(fresh_jobs.items()):
            if os.path.exists("stop.flag"):
                print("🛑 Pause requested. Exiting gracefully...")
                try:
                    os.remove("stop.flag")
                except Exception:
                    pass
                break

            # Recreate context periodically to prevent memory leaks from cache/storage accumulation
            if index > 0 and index % 50 == 0:
                await context.close()
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                
            title = data.get("title", "Unknown")
            print(f"[{index + 1}/{len(fresh_jobs)}] Extracting: {title}")

            if is_senior_role(title):
                print(f"   ⏭️ Skipped (Senior/SDE II/III detected in title)\n")
                # Write only the changed columns — no full JSON dump
                update_job(url, conn, rank="IGNORE", reason="Senior/SDE II/III role detected in title.")
                conn.commit()
                continue
                
            page = await context.new_page()
            page.set_default_timeout(30000)

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
                    raw_text = await page.inner_text("body", timeout=10000)
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

                        print("   🧠 Extracting details and ranking with gemma2:9b...")
                        result_data = await process_and_rank_job_with_llm(title, jd_text)

                        if result_data:
                            reason = result_data.pop("ranking_reasoning", "No reason provided")
                            rank = result_data.pop("final_rank", "UNKNOWN").upper()
                            details = result_data
                        else:
                            print("   ⚠️ LLM failed to return valid JSON.")
                            return "ERROR", "Failed to extract and rank job correctly.", None

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
                
            finally:
                # Ensure the page is closed to free memory
                await page.close()

        await context.close()
        await browser.close()

    conn.close()

    print("==================================================")
    print(f"✅ Finished! Ranked {len(fresh_jobs)} fresh jobs.")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(process_and_rank_jobs())
