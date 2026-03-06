import asyncio
import csv
import re
import ollama
import json
from playwright.async_api import async_playwright

DB_FILE = "jobs_db.json"

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

async def evaluate_job_with_llm(job_title, jd_text):
    """Sends the job description to the local GPU-powered LLM for ranking."""
    
    prompt = f"""
You are an experienced technical recruiter.

Evaluate how well this internship matches the candidate profile.

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
RANKING RULES
====================
CRITICAL INSTRUCTION: You MUST evaluate the IGNORE list first. If the job matches ANY of the IGNORE criteria, immediately rank it as IGNORE and stop matching.

IGNORE (HARD VETO):
- Transportation engineering, Urban planning, or Traffic operations
- Civil, Mechanical, or Construction engineering
- Architecture roles
- HR / Marketing / Sales
- Technical support
- QA / manual testing
- Roles asking for AutoCAD, MicroStation, SketchUp, or GIS.
- Senior, Staff, Principal, SDE II, or SDE III roles.
- IGNORE if it requies more than 2 years of experience.

If and ONLY if the job is NOT in the IGNORE list, rank it using the following:

HIGH:
- Software Engineering
- Backend Engineering
- Full Stack Development
- Systems Programming
- C/C++ roles
- Node.js / JavaScript backend roles
- Networking / distributed systems roles
- Mobile app development roles

MEDIUM:
- General developer roles
- Web development internships
- Platform engineering roles
- DevOps roles

LOW:
- Data engineering
- Data analyst roles
- DevOps heavy roles
- Cloud-only infrastructure roles

CATCH-ALL (DEFAULT):
- If the job does NOT explicitly fit into HIGH, MEDIUM, or LOW, you MUST rank it as IGNORE. Do not guess or create new categories.

====================

Respond ONLY in this format:

RANK: HIGH / MEDIUM / LOW / IGNORE
REASON: One short sentence explaining the decision.
"""

    try:
        response = ollama.chat(model='llama3.1', messages=[
            {'role': 'user', 'content': prompt}
        ], options={"temperature": 0})
        
        result = response['message']['content'].strip()
        
        rank_match = re.search(r'RANK:\s*(HIGH|MEDIUM|LOW|IGNORE)', result, re.IGNORECASE)
        reason_match = re.search(r'REASON:\s*(.*)', result, re.IGNORECASE)
        
        rank = rank_match.group(1).upper() if rank_match else "UNKNOWN"
        reason = reason_match.group(1) if reason_match else result
        
        return rank, reason

    except Exception as e:
        return "ERROR", str(e)

async def process_and_rank_jobs():
    # Load jobs from DB and only rank "New" (Fresh) ones
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            jobs_db = json.load(f)
    except FileNotFoundError:
        print(f"❌ Could not find {DB_FILE}. Run the scraper first!")
        return

    # Filter: only jobs with status "New" AND rank "UNKNOWN" (not yet ranked)
    fresh_jobs = {link: data for link, data in jobs_db.items()
                  if data.get("status") == "New" and data.get("rank", "UNKNOWN") == "UNKNOWN"}
    
    if not fresh_jobs:
        print("ℹ️  No fresh jobs to rank. All jobs have already been processed.")
        return

    print(f"🚀 Starting JD extraction and AI ranking for {len(fresh_jobs)} fresh jobs...\n")

    async with async_playwright() as p:
        # Keep headless=True, but add a real user-agent so Cloudflare doesn't block the extraction
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(30000)  # 30s max for any Playwright action

        for index, (url, data) in enumerate(fresh_jobs.items()):
            title = data.get("title", "Unknown")
            print(f"[{index + 1}/{len(fresh_jobs)}] Extracting: {title}")
            
            if is_senior_role(title):
                print(f"   ⏭️ Skipped (Senior/SDE II/III detected in title)\n")
                jobs_db[url]["rank"] = "IGNORE"
                jobs_db[url]["reason"] = "Senior/SDE II/III role detected in title."
                
                # Save DB after skipping so progress isn't lost
                with open(DB_FILE, 'w', encoding='utf-8') as f:
                    json.dump(jobs_db, f, indent=4)
                continue
            
            try:
                # Wrap entire job in a 45s timeout so nothing gets stuck
                async def process_single_job():
                    # 1. Try networkidle first, fall back to domcontentloaded
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=20000)
                    except Exception:
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                        except Exception:
                            await asyncio.sleep(2)
                    
                    # 2. Extract text and add a wait for render
                    await page.wait_for_timeout(3000)
                    raw_text = await page.locator("body").inner_text()
                    jd_text = clean_text(raw_text)[:6000]
                    
                    # 3. Fallback for stubborn JS walls
                    if len(jd_text) < 100:
                        fallback_text = await page.evaluate("""() => {
                            return Array.from(document.querySelectorAll('p, li, div, span'))
                                .map(el => el.innerText)
                                .join(' ');
                        }""")
                        jd_text = clean_text(fallback_text)[:6000]
                    
                    if len(jd_text) < 100:
                        return "ERROR", "Failed to extract meaningful text."
                    else:
                        if is_senior_role(jd_text):
                            return "IGNORE", "Senior/SDE II/III role detected in job description."
                            
                        print("   🧠 Analyzing JD with Llama 3.1...")
                        return await evaluate_job_with_llm(title, jd_text)

                rank, reason = await asyncio.wait_for(process_single_job(), timeout=45)
                
                print(f"   📊 Result: {rank} - {reason}\n")
                jobs_db[url]["rank"] = rank
                jobs_db[url]["reason"] = reason
                
            except asyncio.TimeoutError:
                print(f"   ⏰ TIMEOUT after 45s. Skipping with ERROR tag.\n")
                jobs_db[url]["rank"] = "ERROR"
                jobs_db[url]["reason"] = "Timed out after 45s."
                
            except Exception as e:
                print(f"   ❌ Failed: {e}. Skipping.\n")
                jobs_db[url]["rank"] = "ERROR"
                jobs_db[url]["reason"] = str(e)[:100]
            
            # Save DB after every job so progress isn't lost
            with open(DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(jobs_db, f, indent=4)
                
        await browser.close()

    # Save updated DB with ranks
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(jobs_db, f, indent=4)

    print("==================================================")
    print(f"✅ Finished! Ranked {len(fresh_jobs)} fresh jobs.")
    print(f"   Results saved to {DB_FILE}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(process_and_rank_jobs())
