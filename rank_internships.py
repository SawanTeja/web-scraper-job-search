import asyncio
import csv
import re
import ollama
from playwright.async_api import async_playwright

INPUT_CSV = "fresh_internships.csv"
OUTPUT_CSV = "ranked_internships.csv"

CANDIDATE_PROFILE = """
- B.Tech Computer Science student
- Skills: Python, JavaScript, React, Node.js, C++, SQL, Git, Linux
- Interests: Software Development, Data Engineering, AI/ML, Full-Stack Development
- Looking for: Software Engineering / Data / AI internships
"""

def clean_text(text):
    """Remove excessive whitespace and non-printable characters."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

async def evaluate_job_with_llm(job_title, jd_text):
    """Sends the job description to the local GPU-powered LLM for ranking."""
    
    prompt = f"""
    You are an expert tech recruiter. Evaluate the following internship job description against the candidate's profile.
    
    {CANDIDATE_PROFILE}
    
    Job Title: {job_title}
    Job Description: {jd_text}
    
    RULES:
    1. If the job is clearly NOT a software development, data analysis, or core engineering role (e.g., HR, Marketing, Tech Sales, IT Support), immediately rank it as IGNORE.
    2. Otherwise, rank the match as HIGH, MEDIUM, or LOW based on how well the candidate's skills align with the requirements.
    
    Provide your response in exactly this format:
    RANK: [HIGH/MEDIUM/LOW/IGNORE]
    REASON: [One short sentence explaining why.]
    """

    try:
        response = ollama.chat(model='llama3.1', messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        result = response['message']['content'].strip()
        
        rank_match = re.search(r'RANK:\s*(HIGH|MEDIUM|LOW|IGNORE)', result, re.IGNORECASE)
        reason_match = re.search(r'REASON:\s*(.*)', result, re.IGNORECASE)
        
        rank = rank_match.group(1).upper() if rank_match else "UNKNOWN"
        reason = reason_match.group(1) if reason_match else result
        
        return rank, reason

    except Exception as e:
        return "ERROR", str(e)

async def process_and_rank_jobs():
    jobs = []
    
    try:
        with open(INPUT_CSV, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for row in reader:
                jobs.append(row)
    except FileNotFoundError:
        print(f"❌ Could not find {INPUT_CSV}. Run the scraper first!")
        return

    print(f"🚀 Starting JD extraction and AI ranking for {len(jobs)} jobs...\n")

    ranked_results = []

    async with async_playwright() as p:
        # Keep headless=True, but add a real user-agent so Cloudflare doesn't block the extraction
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        for index, job in enumerate(jobs):
            title = job["Job Title / Company"]
            url = job["Application Link"]
            print(f"[{index + 1}/{len(jobs)}] Extracting: {title}")
            
            try:
                # 1. Wait for 'networkidle' (API calls finished) with a longer 30s timeout
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                except Exception:
                    # If infinite tracking scripts prevent networkidle, catch the timeout 
                    # and manually wait 3 seconds before forcing the extraction anyway.
                    await asyncio.sleep(3)
                
                # 2. Use Playwright's native locator to reliably grab text
                raw_text = await page.locator("body").inner_text()
                jd_text = clean_text(raw_text)
                
                # 3. Fallback for extremely stubborn JS walls (like Workday)
                if len(jd_text) < 100:
                    fallback_text = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('p, li, div, span'))
                            .map(el => el.innerText)
                            .join(' ');
                    }""")
                    jd_text = clean_text(fallback_text)
                
                if len(jd_text) < 100:
                    print("   ⚠️ Not enough text found. Blocked by heavy JS wall or Captcha.")
                    rank, reason = "ERROR", "Failed to extract meaningful text."
                else:
                    print("   🧠 Analyzing JD with Llama 3.1...")
                    rank, reason = await evaluate_job_with_llm(title, jd_text)
                
                print(f"   📊 Result: {rank} - {reason}\n")
                
                ranked_results.append({
                    "Job Title": title,
                    "Link": url,
                    "Rank": rank,
                    "Reason": reason
                })
                
            except Exception as e:
                print(f"   ❌ Failed to process URL: {e}\n")
                ranked_results.append({
                    "Job Title": title,
                    "Link": url,
                    "Rank": "ERROR",
                    "Reason": "Page load or extraction failed."
                })
                
        await browser.close()

    # Save the ranked results
    with open(OUTPUT_CSV, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=["Job Title", "Link", "Rank", "Reason"])
        writer.writeheader()
        writer.writerows(ranked_results)

    print("==================================================")
    print(f"✅ Finished! Ranked jobs saved to {OUTPUT_CSV}")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(process_and_rank_jobs())
