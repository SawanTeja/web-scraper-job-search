import asyncio
import re
import json
import os
import random
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

import sys
import os

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT_DIR, ".env"))

from groq import AsyncGroq
import groq

try:
    from src.core.db import get_conn, init_db, load_db, update_job
except ImportError:
    from db import get_conn, init_db, load_db, update_job

try:
    from src.ranking.job_filter import evaluate_deep_jd_filter
except ImportError:
    from job_filter import evaluate_deep_jd_filter


PRIMARY_MODEL = "qwen/qwen3.8-27b"
FALLBACK_MODEL = "openai/gpt-oss-20b"

# --- GROQ ROTATION MANAGER ---
class GroqManager:
    def __init__(self):
        keys = []
        # Support numbered keys: GROQ_API_KEY_1 to GROQ_API_KEY_100
        for i in range(1, 101):
            k = os.environ.get(f"GROQ_API_KEY_{i}")
            if k and k.strip():
                clean_k = k.strip().strip("'\"")
                if clean_k and clean_k not in keys:
                    keys.append(clean_k)

        # Support comma-separated GROQ_API_KEYS
        if os.environ.get("GROQ_API_KEYS"):
            for k in os.environ.get("GROQ_API_KEYS").split(","):
                clean_k = k.strip().strip("'\"")
                if clean_k and clean_k not in keys:
                    keys.append(clean_k)

        # Fallback to standard GROQ_API_KEY if no numbered ones exist
        if not keys and os.environ.get("GROQ_API_KEY"):
            clean_k = os.environ.get("GROQ_API_KEY").strip().strip("'\"")
            if clean_k and clean_k not in keys:
                keys.append(clean_k)

        if not keys:
            print("❌ ERROR: No GROQ_API_KEY_1, GROQ_API_KEY_2, etc. found in .env!")
            exit(1)

        self.keys = keys
        self.clients = [AsyncGroq(api_key=k) for k in keys]
        self.current_idx = 0
        self.total_keys = len(self.clients)
        self.primary_model = PRIMARY_MODEL
        self.fallback_model = FALLBACK_MODEL
        self.active_model = PRIMARY_MODEL
        # Set of key numbers (1-indexed) that have exhausted their DAILY quota on the active model
        self.daily_exhausted_keys = set()
        print(f"🔑 Loaded {self.total_keys} Groq API keys for circular rotation (Primary: {self.primary_model}, Fallback: {self.fallback_model}).")

    def get_client(self):
        # Automatically skip any keys that have already exhausted their DAILY quota on the active model
        if len(self.daily_exhausted_keys) < self.total_keys:
            attempts = 0
            while (self.current_idx + 1) in self.daily_exhausted_keys and attempts < self.total_keys:
                self.current_idx = (self.current_idx + 1) % self.total_keys
                attempts += 1
        return self.clients[self.current_idx]

    def rotate(self, reason: str = ""):
        self.current_idx = (self.current_idx + 1) % self.total_keys
        # Skip daily exhausted keys during rotation as long as at least one key still has daily quota
        if len(self.daily_exhausted_keys) < self.total_keys:
            attempts = 0
            while (self.current_idx + 1) in self.daily_exhausted_keys and attempts < self.total_keys:
                self.current_idx = (self.current_idx + 1) % self.total_keys
                attempts += 1
        msg = f"🔄 Swapped to Groq API Key #{self.current_idx + 1}/{self.total_keys}"
        if reason:
            msg += f" ({reason})"
        print(msg)

    async def chat_completion(self, messages, temperature=0, retries=None, model=None):
        max_attempts = retries if retries is not None else max(self.total_keys * 4, 25)

        for attempt in range(max_attempts):
            target_model = model or self.active_model
            client = self.get_client()
            key_num = self.current_idx + 1
            current_key = self.keys[self.current_idx]
            try:
                response = await client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    temperature=temperature
                )
                # Record real tokens used locally for live GUI quota tracking
                if hasattr(response, "usage") and response.usage:
                    toks = getattr(response.usage, "total_tokens", 0)
                    try:
                        from src.ranking.groq_tracker import record_usage
                        record_usage(current_key, toks, target_model)
                    except Exception:
                        pass

                # Successful call: advance circular index so next request uses next key in ring
                self.current_idx = (self.current_idx + 1) % self.total_keys
                return response
            except groq.RateLimitError as e:
                err_str = str(e).lower()
                # 1. Distinguish between rolling per-minute limits (TPM / RPM) and genuine DAILY limits (TPD / RPD)
                # Check DAILY limits first (TPD / RPD) since Groq daily errors also contain "please try again in XmYs"
                is_daily = any(x in err_str for x in ["tokens_per_day", "tokens per day", "tpd", "requests_per_day", "requests per day", "rpd", "daily"])
                is_per_minute = not is_daily and (any(x in err_str for x in ["tokens per minute", "tokens_per_minute", "tpm", "requests per minute", "requests_per_minute", "rpm"]) or "please try again in" in err_str)

                if is_daily:
                    try:
                        from src.ranking.groq_tracker import mark_key_exhausted
                        mark_key_exhausted(current_key, self.active_model)
                    except Exception:
                        pass
                    self.daily_exhausted_keys.add(key_num)
                    exhausted_count = len(self.daily_exhausted_keys)
                    print(f"   ⚠️ Key #{key_num}/{self.total_keys} reached DAILY quota limit ({exhausted_count}/{self.total_keys} keys exhausted on {self.active_model}).")

                    # STRICT RULE: ONLY switch to fallback when ALL keys have hit daily rate limit
                    # Even if 1 key hasn't hit daily limit, DO NOT switch model!
                    if exhausted_count >= self.total_keys:
                        if self.active_model == self.primary_model:
                            self.active_model = self.fallback_model
                            self.daily_exhausted_keys.clear()
                            print(f"\n🔀 ALL {self.total_keys} keys exhausted daily quota on {self.primary_model}!")
                            print(f"🔄 Switched to Fallback Model: {self.fallback_model} (Fresh 200,000 daily tokens per key!)\n")
                            await asyncio.sleep(1)
                            continue
                        else:
                            print(f"❌ ALL {self.total_keys} keys have exhausted daily limits on both primary and fallback models!")
                            return None
                    else:
                        # At least one key has daily tokens left -> keep using active model and rotate to next key
                        print(f"   ↪️ Other keys still have daily quota on {self.active_model}. Rotating...")
                        self.rotate(reason="daily-limit-rotate")
                        await asyncio.sleep(1)
                else:
                    # Rolling per-minute rate limit (TPM/RPM) - NEVER switch models on per-minute limits!
                    print(f"   ⚠️ Per-minute TPM cooldown on Key #{key_num}/{self.total_keys}. Rotating...")
                    self.rotate(reason="rate-limit")
                    # If all active keys have been tried within this minute, pause for rolling token bucket to refill
                    active_keys_count = max(1, self.total_keys - len(self.daily_exhausted_keys))
                    if (attempt + 1) % active_keys_count == 0:
                        print(f"   ⏳ All active keys in rolling TPM cooldown. Pausing 60s for token bucket to refill...")
                        await asyncio.sleep(60)
                    else:
                        await asyncio.sleep(1) # Small pause before retrying on next key
            except Exception as e:
                print(f"   ⚠️ API Error on Key #{key_num}/{self.total_keys}: {e}. Retrying {attempt+1}/{max_attempts}...")
                self.rotate(reason="api-error")
                await asyncio.sleep(2)
        return None

groq_manager = GroqManager()

PROMPTS_LOG_FILE = os.path.join(_ROOT_DIR, "prompts.json")

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
   - Requires 1 or more years of experience (must strictly be fresh graduate, 0-1 year, intern, or student).
   - Non-software roles (e.g., Civil, Mechanical, HR, QA, Tech Support, Transportation, Cashier, Sales).
   - Jobs located in the USA (California, CA, New York, NY, Texas, TX, Seattle, SF, etc. Filter these out!).
   - Jobs located in Canada (Toronto, Vancouver, Montreal, Ontario, Quebec, Alberta, etc. Filter these out!).
2. HIGH: Software Engineering, Backend, Full Stack, C/C++, Node.js, Systems, Mobile.
3. MEDIUM: General Web Dev, Platform, AI/ML, DevOps (programming-focused).
4. LOW: Data Analyst, Data Engineering, Cloud/IT operations.
5. If none of the above match, default to IGNORE.

Respond exactly in this format on two lines:
RANK: <HIGH|MEDIUM|LOW|IGNORE>
REASON: One short sentence explaining the decision.
"""

    response = await groq_manager.chat_completion(
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


def is_already_ai_ranked(reason: str = "", details: dict = None) -> bool:
    """Returns True if this job has already been evaluated by the Groq AI."""
    clean_reason = (reason or "").strip()
    if clean_reason.startswith("[AI Verified]") or clean_reason.startswith("[AI-Evaluated]") or clean_reason.startswith("AI:"):
        return True
    if details and isinstance(details, dict) and details.get("ai_evaluated"):
        return True
    return False


def has_structured_jd(details) -> bool:
    """Returns True if the job has full structured JD schema extracted by LLM."""
    if not details:
        return False
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except Exception:
            return False
    if not isinstance(details, dict):
        return False
    schema_fields = ("experience_required", "skills_required", "responsibilities", "job_name", "requirements", "about_job")
    return any(k in details for k in schema_fields)


async def extract_structured_jds(conn=None):
    """
    Stage 1 Pipeline:
    Uses Playwright to fetch web pages for active jobs that lack structured JD details.
    Runs Pre-Extraction Guard to eliminate non-tech/senior roles with 0 tokens.
    Extracts structured schema with Groq LLM, runs Post-Extraction Guard,
    and saves JSON details into the database.
    """
    if os.path.exists("stop.flag"):
        try:
            os.remove("stop.flag")
        except Exception:
            pass

    close_conn = False
    if conn is None:
        conn = get_conn()
        init_db(conn)
        close_conn = True

    try:
        from src.ranking.job_filter import apply_deterministic_filter_to_db, evaluate_deterministic_ignore, evaluate_deep_jd_filter
    except ImportError:
        from job_filter import apply_deterministic_filter_to_db, evaluate_deterministic_ignore, evaluate_deep_jd_filter

    filter_stats = apply_deterministic_filter_to_db(conn=conn, only_unknown=True, sync_locations=False)
    if filter_stats["total_ignored"] > 0:
        print(f"🧹 Deterministic Pre-Filter: Auto-ignored {filter_stats['total_ignored']} jobs ({filter_stats['senior_ignored']} Senior, {filter_stats['non_tech_ignored']} Non-tech, {filter_stats['us_ignored']} US) - 0 tokens used!")

    jobs_db = load_db(conn)

    candidate_items = []
    skipped_count = 0
    already_extracted_count = 0

    for link, data in jobs_db.items():
        if data.get("status") != "New":
            continue
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")
        details = data.get("details")

        if is_already_ai_ranked(reason, details):
            skipped_count += 1
            continue

        if has_structured_jd(details):
            already_extracted_count += 1
            continue

        # Auto-ignore modal popup URLs that have no job descriptions
        if "oneclick-ui" in link:
            update_job(link, conn, rank="IGNORE", status="NA", reason="[Invalid URL] SmartRecruiters modal popup")
            continue

        # If previous extraction already failed to find text, move to NA to unblock queue
        if rank == "ERROR" and "Failed to extract meaningful text." in reason:
            update_job(link, conn, status="NA")
            continue

        is_tier1 = "[Tier 1:" in reason or (rank == "HIGH")
        prio = 0 if is_tier1 else 1
        candidate_items.append((prio, link, data))

    print("\n" + "="*60)
    print("📥 STAGE 1: EXTRACT STRUCTURED JDs (Playwright + Groq LLM)")
    print("="*60)
    if already_extracted_count > 0:
        print(f"ℹ️  {already_extracted_count} jobs already have structured JDs extracted in DB.")
    if skipped_count > 0:
        print(f"🔒 Skipped {skipped_count} jobs already evaluated by AI.")

    if not candidate_items:
        print("ℹ️  No candidate jobs needing JD extraction.")
        if close_conn:
            conn.close()
        return

    candidate_items.sort(key=lambda x: x[0])
    tier1_count = sum(1 for p, _, _ in candidate_items if p == 0)
    tier2_count = len(candidate_items) - tier1_count

    print(f"🚀 Extracting JDs for {len(candidate_items)} jobs:")
    print(f"   ⭐ Priority 1: {tier1_count} Early Career / Intern jobs")
    print(f"   💻 Priority 2: {tier2_count} General Software / Tech jobs\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        for index, (prio, url, data) in enumerate(candidate_items):
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
            location = (data.get("details") or {}).get("location", "") if isinstance(data.get("details"), dict) else ""
            tier_badge = "⭐ [Tier 1 Intern/Junior]" if prio == 0 else "💻 [Tier 2 Tech/SWE]"
            print(f"[{index + 1}/{len(candidate_items)}] {tier_badge} Extracting: {title}")

            # Deterministic pre-check on title/location
            should_ignore, ignore_reason = evaluate_deterministic_ignore(title=title, location=location)
            if should_ignore:
                print(f"   ⏭️ Skipped ([Deterministic] {ignore_reason})\n")
                update_job(url, conn, rank="IGNORE", status="NA", reason=f"[Deterministic] {ignore_reason}")
                conn.commit()
                continue

            page = await context.new_page()
            await Stealth().apply_stealth_async(page)
            page.set_default_timeout(30000)

            try:
                async def fetch_and_extract():
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=20000)
                    except Exception:
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                        except Exception:
                            await asyncio.sleep(2)

                    await page.wait_for_timeout(3000)
                    raw_text = await page.inner_text("body", timeout=10000)
                    jd_text = clean_text(raw_text)[:2800]

                    if len(jd_text) < 100:
                        fallback_text = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('p, li, div, span'))
                                .map(el => el.innerText)
                                .join(' ');
                        }""")
                        jd_text = clean_text(fallback_text)[:2800]

                    # Check child iframes (e.g. iCIMS, Oracle Taleo, Workday embeds)
                    if len(jd_text) < 100:
                        for frame in page.frames:
                            if frame != page.main_frame:
                                try:
                                    frame_text = await frame.inner_text("body", timeout=3000)
                                    frame_clean = clean_text(frame_text)
                                    if len(frame_clean) > len(jd_text):
                                        jd_text = frame_clean[:2800]
                                except Exception:
                                    pass

                    if len(jd_text) < 100:
                        print(f"   ⚠️ Could not extract text from page (<100 chars). Skipped.\n")
                        return "ERROR", "Failed to extract meaningful text.", None

                    # 1. Pre-Extraction Guard: Check raw JD text before spending extraction LLM tokens
                    should_drop_raw, raw_reason = evaluate_deep_jd_filter(title, jd_text=jd_text)
                    if should_drop_raw:
                        print(f"   🛑 Filtered by Pre-Extraction Guard: {raw_reason} (0 AI tokens used!)")
                        return "IGNORE", f"[Deep JD Filter] {raw_reason}", None

                    active_model_name = groq_manager.active_model.split('/')[-1]
                    print(f"   🧠 Extracting structured details with Groq ({active_model_name})...")
                    details = await extract_job_details_with_llm(title, jd_text)

                    # 2. Post-Extraction Guard: Check structured details before saving
                    if details:
                        should_drop_det, det_reason = evaluate_deep_jd_filter(title, jd_text=jd_text, details=details)
                        if should_drop_det:
                            print(f"   🛑 Filtered by Post-Extraction Guard: {det_reason} (Saved AI ranking tokens!)")
                            return "IGNORE", f"[Deep JD Filter] {det_reason}", details

                        print(f"   ✅ Structured JD Extracted and saved to DB.")
                        return "UNKNOWN", "[JD Extracted] Ready for Deep Filter / AI Ranking", details
                    else:
                        print(f"   ⚠️ Extraction failed to parse valid JSON.")
                        return "ERROR", "Failed to parse structured JD JSON.", None

                rank, reason, details = await asyncio.wait_for(fetch_and_extract(), timeout=90)
                new_status = "NA" if rank in ("IGNORE", "ERROR") else "New"
                update_job(url, conn, rank=rank, reason=reason, status=new_status,
                           **({"details": details} if details else {}))
                conn.commit()
                print()

            except asyncio.TimeoutError:
                print(f"   ⚠️ Timeout error: Took too long to process.\n")
                update_job(url, conn, rank="ERROR", reason="Timeout while processing job page.", status="NA")
                conn.commit()
            except Exception as e:
                print(f"   ⚠️ Error: {e}\n")
                update_job(url, conn, rank="ERROR", reason=f"Exception: {str(e)}")
                conn.commit()
            finally:
                await page.close()

        await context.close()
        await browser.close()

    if close_conn:
        conn.close()
    print("✅ Stage 1 JD Extraction finished!")


async def rank_clean_jobs(conn=None):
    """
    Stage 3 Pipeline:
    Evaluates clean surviving jobs from the database against candidate resume/criteria.
    Runs 100% in Python + AsyncGroq without Playwright or browser overhead!
    """
    if os.path.exists("stop.flag"):
        try:
            os.remove("stop.flag")
        except Exception:
            pass

    close_conn = False
    if conn is None:
        conn = get_conn()
        init_db(conn)
        close_conn = True

    try:
        from src.ranking.job_filter import evaluate_deep_jd_filter
    except ImportError:
        from job_filter import evaluate_deep_jd_filter

    jobs_db = load_db(conn)

    candidate_items = []
    skipped_ai_count = 0
    missing_details_count = 0

    for link, data in jobs_db.items():
        if data.get("status") != "New":
            continue
        rank = data.get("rank", "UNKNOWN")
        reason = data.get("reason", "")
        details = data.get("details")

        if is_already_ai_ranked(reason, details):
            skipped_ai_count += 1
            continue

        if not has_structured_jd(details):
            missing_details_count += 1
            continue

        is_tier1 = "[Tier 1:" in reason or (rank == "HIGH")
        prio = 0 if is_tier1 else 1
        candidate_items.append((prio, link, data))

    print("\n" + "="*60)
    print("⭐ STAGE 3: AI RANK CLEAN JOBS (0 Browser Overhead)")
    print("="*60)
    print(f"📊 Candidate jobs ready for AI ranking: {len(candidate_items)}")
    if missing_details_count > 0:
        print(f"ℹ️  {missing_details_count} active jobs lack structured JDs. Run Stage 1 (Extract JDs) first.")
    if skipped_ai_count > 0:
        print(f"🔒 Skipped {skipped_ai_count} jobs already evaluated by AI.")

    if not candidate_items:
        print("ℹ️  No fresh clean jobs to rank.")
        if close_conn:
            conn.close()
        return

    candidate_items.sort(key=lambda x: x[0])
    processed = 0

    for index, (prio, url, data) in enumerate(candidate_items):
        if os.path.exists("stop.flag"):
            print("🛑 Pause requested. Exiting gracefully...")
            try:
                os.remove("stop.flag")
            except Exception:
                pass
            break

        title = data.get("title", "Unknown")
        details = data.get("details")
        tier_badge = "⭐ [Tier 1 Intern/Junior]" if prio == 0 else "💻 [Tier 2 Tech/SWE]"
        print(f"[{index + 1}/{len(candidate_items)}] {tier_badge} AI Ranking: {title}")

        # Guard check before spending ranking tokens
        should_drop, drop_reason = evaluate_deep_jd_filter(title, details=details)
        if should_drop:
            print(f"   🛑 Filtered by Deep Guard: {drop_reason} (Saved AI ranking tokens!)\n")
            update_job(url, conn, rank="IGNORE", status="NA", reason=f"[Deep JD Filter] {drop_reason}", details=details)
            conn.commit()
            continue

        active_model_name = groq_manager.active_model.split('/')[-1]
        print(f"   🧠 Ranking candidate fit with Groq ({active_model_name})...")
        rank, reason = await evaluate_job_with_llm(details, "", title)

        print(f"   📊 AI Result: {rank} - {reason}\n")
        new_status = "NA" if rank == "IGNORE" else "New"
        ai_reason = f"[AI Verified] {reason}" if not (reason.startswith("[AI") or reason.startswith("AI:")) else reason
        if details and isinstance(details, dict):
            details["ai_evaluated"] = True
        update_job(url, conn, rank=rank, reason=ai_reason, status=new_status, details=details)
        conn.commit()
        processed += 1

    print(f"✅ AI Ranking completed for {processed} clean jobs!")
    if close_conn:
        conn.close()


async def process_and_rank_jobs(mode: str = "full"):
    """
    Main controller for the multi-stage ranking pipeline:
      - mode='extract': Only runs Stage 1 (Playwright JD Extraction & JSON parsing)
      - mode='rank':    Only runs Stage 3 (AI Ranking on clean DB jobs without browser)
      - mode='full':    Runs Stage 1 -> Stage 2 (Deep Filter) -> Stage 3 (AI Rank)
    """
    if mode == "extract":
        await extract_structured_jds()
    elif mode == "rank":
        await rank_clean_jobs()
    elif mode == "full":
        print("\n" + "="*60)
        print("🚀 RUNNING FULL END-TO-END PIPELINE (Extract ➔ Deep Filter ➔ AI Rank)")
        print("="*60)
        await extract_structured_jds()
        if os.path.exists("stop.flag"):
            return

        try:
            from src.ranking.job_filter import apply_deep_jd_filter_to_db
        except ImportError:
            from job_filter import apply_deep_jd_filter_to_db
        print("\n" + "="*60)
        print("🎯 RUNNING STAGE 2: DEEP JD FILTER (0 AI TOKENS)")
        print("="*60)
        apply_deep_jd_filter_to_db(move_to_na=True)
        if os.path.exists("stop.flag"):
            return

        await rank_clean_jobs()


def rank_jobs(mode: str = "full"):
    """Synchronous entry point to run process_and_rank_jobs with specified mode ('extract', 'rank', 'full')."""
    return asyncio.run(process_and_rank_jobs(mode=mode))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Groq AI JD Extraction & Ranking Pipeline")
    parser.add_argument("--extract", "--extract-only", dest="extract", action="store_true", help="Stage 1: Extract structured JDs via Playwright + Groq")
    parser.add_argument("--rank", "--rank-only", dest="rank", action="store_true", help="Stage 3: AI Rank clean jobs from DB (No browser required)")
    parser.add_argument("--full", action="store_true", help="Full Pipeline: Extract JDs -> Deep Filter -> AI Rank")
    args = parser.parse_args()

    if args.extract:
        rank_jobs(mode="extract")
    elif args.rank:
        rank_jobs(mode="rank")
    else:
        rank_jobs(mode="full")
