import asyncio
import re
import json
import os
import random
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from dotenv import load_dotenv
load_dotenv()

from groq import AsyncGroq
import groq

# SQLite DB helper
from db import get_conn, init_db, load_db, update_job

# --- GROQ ROTATION MANAGER ---
class GroqManager:
    def __init__(self):
        keys = []
        for i in range(1, 10):
            k = os.environ.get(f"GROQ_API_KEY_{i}")
            if k:
                keys.append(k)
        
        # Fallback to standard GROQ_API_KEY if no numbered ones exist
        if not keys and os.environ.get("GROQ_API_KEY"):
            keys.append(os.environ.get("GROQ_API_KEY"))

        if not keys:
            print("❌ ERROR: No GROQ_API_KEY_1, GROQ_API_KEY_2, etc. found in .env!")
            exit(1)
            
        self.clients = [AsyncGroq(api_key=k) for k in keys]
        self.current_idx = 0
        print(f"🔑 Loaded {len(self.clients)} Groq API keys for rotation.")

    def get_client(self):
        return self.clients[self.current_idx]

    def rotate(self):
        self.current_idx = (self.current_idx + 1) % len(self.clients)
        print(f"🔄 Swapped to Groq API Key #{self.current_idx + 1}")

    async def chat_completion(self, model, messages, temperature=0, retries=3):
        for attempt in range(retries):
            client = self.get_client()
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature
                )
                return response
            except groq.RateLimitError as e:
                print(f"   ⚠️ Rate limit exceeded on Key #{self.current_idx + 1}. Rotating...")
                self.rotate()
                await asyncio.sleep(1) # Small pause before retrying on new key
            except Exception as e:
                print(f"   ⚠️ API Error: {e}. Retrying {attempt+1}/{retries}...")
                await asyncio.sleep(2)
        return None

groq_manager = GroqManager()

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
    """Extracts structured data from the job description using Llama-3.1-8B-Instant."""
    prompt = f"""
If you produce anything other than valid JSON, the system will crash.
Do not explain anything.
Only return JSON.

You are a technical recruiter. Your task is to extract job details from the following Job Description (JD).

====================
JOB
====================
Title: {job_title}

Description:
{jd_text}
====================

Respond ONLY with a valid JSON object matching EXACTLY this schema. Ensure you use arrays instead of paragraphs for skills, responsibilities, requirements, and nice_to_haves.
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

    response = await groq_manager.chat_completion(
        model='qwen/qwen3.8-27b',
        messages=[{'role': 'user', 'content': prompt}]
    )

    if not response:
        return None

    result = response.choices[0].message.content.strip()
    log_prompt_to_file(job_title, "extraction", prompt, result)
    data = extract_json_from_text(result)

    if data and validate_job_json(data):
        return data
    else:
        print(f"   ⚠️ JSON invalid or missing fields during extraction.")
        return None

async def evaluate_job_with_llm(job_details_json, jd_text, job_title):
    """Evaluates the extracted job details or raw text against the candidate profile using Llama-3.3-70B-Versatile."""
    
    if job_details_json:
        job_info_str = json.dumps(job_details_json, indent=2)
        info_type = "JSON Details"
    else:
        job_info_str = jd_text
        info_type = "Raw Description"

    prompt = f"""
You are an expert technical recruiter matching candidates to jobs.
Evaluate the job against the candidate profile.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

====================
JOB ({info_type})
====================
Title: {job_title}
{job_info_str}
====================

INSTRUCTIONS:
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

Respond exactly in this format on two lines:
RANK: <HIGH|MEDIUM|LOW|IGNORE>
REASON: One short sentence explaining the decision.
"""

    response = await groq_manager.chat_completion(
        model='qwen/qwen3.8-27b',
        messages=[{'role': 'user', 'content': prompt}]
    )

    if not response:
        return "ERROR", "Failed to get ranking from API."

    result = response.choices[0].message.content.strip()
    log_prompt_to_file(job_title, "ranking", prompt, result)

    rank = "UNKNOWN"
    reason = ""
    for line in result.split('\n'):
        if line.startswith("RANK:"):
            rank = line.replace("RANK:", "").strip().upper()
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    if rank not in ["HIGH", "MEDIUM", "LOW", "IGNORE"]:
        rank = "UNKNOWN"

    return rank, reason

async def process_and_rank_jobs():
    if os.path.exists("stop.flag"):
        try:
            os.remove("stop.flag")
        except Exception:
            pass

    conn = get_conn()
    init_db(conn)

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

            if index > 0 and index % 50 == 0:
                await context.close()
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                
            title = data.get("title", "Unknown")
            print(f"[{index + 1}/{len(fresh_jobs)}] Extracting: {title}")

            if is_senior_role(title):
                print(f"   ⏭️ Skipped (Senior/SDE II/III detected in title)\n")
                update_job(url, conn, rank="IGNORE", reason="Senior/SDE II/III role detected in title.")
                conn.commit()
                continue
                
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
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

                        print("   🧠 Extracting structured details with Groq (qwen3.8-27b)...")
                        details = await extract_job_details_with_llm(title, jd_text)

                        if details:
                            print("   🧠 Ranking Extracted JSON with Groq (qwen3.8-27b)...")
                        else:
                            print("   ⚠️ Extraction failed. Ranking RAW Job Description with Groq (qwen3.8-27b)...")

                        rank, reason = await evaluate_job_with_llm(details, jd_text, title)
                        return rank, reason, details

                rank, reason, details = await asyncio.wait_for(process_single_job(), timeout=90)

                print(f"   📊 Result: {rank} - {reason}\n")
                update_job(url, conn, rank=rank, reason=reason,
                           **({"details": details} if details else {}))
                conn.commit()

            except asyncio.TimeoutError:
                print(f"   ⚠️ Timeout error: Took too long to process.\n")
                update_job(url, conn, rank="ERROR", reason="Timeout while processing job page.")
                conn.commit()
            except Exception as e:
                print(f"   ⚠️ Error: {e}\n")
                update_job(url, conn, rank="ERROR", reason=f"Exception: {str(e)}")
                conn.commit()
            finally:
                await page.close()

        await context.close()
        await browser.close()
    
    conn.close()
    print("✅ All jobs processed!")

if __name__ == "__main__":
    asyncio.run(process_and_rank_jobs())
