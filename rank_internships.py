import asyncio
import csv
import re
import ollama
from playwright.async_api import async_playwright

INPUT_CSV = "fresh_internships.csv"
OUTPUT_CSV = "ranked_internships.csv"

# The updated profile based strictly on your resume
CANDIDATE_PROFILE = """
Candidate Background:
- Degree: Pursuing an Integrated M.Sc. in Mathematics and Computing at Birla Institute of Technology, Mesra.
- Core Languages: C, C++, JavaScript, SQL.
- Systems & Backend: Boost.Asio, TCP/UDP Sockets, Multithreading, Node.js, Express.js, REST APIs, OAuth 2.0.
- Frontend & Mobile: React.js, React Native, Tailwind CSS, Expo.
- Databases & Tools: MongoDB, PostgreSQL, Git, Linux, Google Cloud APIs.
- Experience: Currently an SDE Intern building real-time full-stack AI applications with Node.js, React, and audio pipelines (FFmpeg/WebRTC).
- Key Projects: 
  1. FluxDrop (Cross-platform P2P file transfer in C++ using Boost.Asio).
  2. Plannify (Full-stack React Native productivity app with offline-first sync).
  3. Forever (MERN stack e-commerce platform).
- Target Roles: Software Engineering Intern (Backend, Full-Stack, Mobile/React Native, or Systems/C++).
"""

def clean_text(text):
    """Removes excessive whitespace and limits the text length to save LLM processing time."""
    text = re.sub(r'\s+', ' ', text).strip()
    # Limit to roughly the first 1500 words to prevent overflowing the LLM's context window
    words = text.split()[:1500] 
    return " ".join(words)

async def evaluate_job_with_llm(job_title, jd_text):
    """Sends the job description to the local GPU-powered LLM for ranking."""
    
    prompt = f"""
    You are an expert tech recruiter. Evaluate the following internship job description against the candidate's profile.
    
    {CANDIDATE_PROFILE}
    
    Job Title: {job_title}
    Job Description: {jd_text}
    
    Rank the match as HIGH, MEDIUM, or LOW based on how well the candidate's skills align with the requirements. 
    Provide your response in exactly this format:
    RANK: [HIGH/MEDIUM/LOW]
    REASON: [One short sentence explaining why.]
    """

    try:
        # Call the local model running on your NVIDIA GPU
        response = ollama.chat(model='llama3.1', messages=[
            {'role': 'user', 'content': prompt}
        ])
        
        result = response['message']['content'].strip()
        
        # Parse the output
        rank_match = re.search(r'RANK:\s*(HIGH|MEDIUM|LOW)', result, re.IGNORECASE)
        reason_match = re.search(r'REASON:\s*(.*)', result, re.IGNORECASE)
        
        rank = rank_match.group(1).upper() if rank_match else "UNKNOWN"
        reason = reason_match.group(1) if reason_match else result
        
        return rank, reason

    except Exception as e:
        return "ERROR", str(e)

async def process_and_rank_jobs():
    jobs = []
    
    # Read the scraped jobs
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
        # Running headless since we are just reading ATS pages
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        for index, job in enumerate(jobs):
            title = job["Job Title / Company"]
            url = job["Application Link"]
            print(f"[{index + 1}/{len(jobs)}] Extracting: {title}")
            
            try:
                # Visit the page and wait for the network to idle
                await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                
                # Extract all visible text from the body element
                raw_text = await page.evaluate("document.body.innerText")
                jd_text = clean_text(raw_text)
                
                if len(jd_text) < 50:
                    print("   ⚠️ Not enough text found. Might be a dead link or heavy JS wall.")
                    rank, reason = "ERROR", "Failed to extract meaningful text."
                else:
                    print("   🧠 Analyzing JD with Llama 3...")
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
