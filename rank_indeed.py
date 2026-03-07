import json
import ollama
import re
import os

DBINPUT_FILE = "indeed_db.json"
OUTPUT_FILE = "indeed_db.json"

def process_and_rank_jobs():
    try:
        with open(DBINPUT_FILE, 'r', encoding='utf-8') as f:
            jobs_db = json.load(f)
    except FileNotFoundError:
        print(f"❌ Could not find {DBINPUT_FILE}. Run the scraper first!")
        return

    # Filter: only jobs with status "New" AND rank "UNKNOWN" or "ERROR"
    fresh_jobs = {link: data for link, data in jobs_db.items()
                  if data.get("status") == "New" and data.get("rank", "UNKNOWN") in ["UNKNOWN", "ERROR"]}
    
    if not fresh_jobs:
        print("ℹ️  No fresh jobs to rank. All jobs have already been processed.")
        return

    print(f"🚀 Starting simple JD checking pipeline for {len(fresh_jobs)} Indeed jobs...\n")

    for index, (url, data) in enumerate(fresh_jobs.items()):
        title = data.get("title", "Unknown")
        print(f"[{index + 1}/{len(fresh_jobs)}] Evaluating: {title}")
        
        details = data.get("details", {})
        jd = details.get("jd", "")
        
        if not jd or jd == "N/A":
            print("   ⚠️ No JD found. Skipping.")
            jobs_db[url]["rank"] = "ERROR"
            jobs_db[url]["reason"] = "No Job Description extracted."
            continue
            
        prompt = f"""
Analyze the following Job Description and answer three simple questions:
1. Is this an "unpaid" role or internship?
2. Is the salary or stipend clearly stated to be less than 10,000 (e.g. INR 5,000/month, 8k)?
3. If the salary is in range ( eg 8k-15k) then check if the lower bound is less than 10k.
4. Does the role explicitly require prior work experience (e.g. "1+ years experience required", "Must have 2 years of experience")?
5. Does the job have .NET, PHP, Wordpress developer role ?
6. Does the job have Flutter Development ?

If the answer to ANY of those three is YES, you must output:
RANK: IGNORE
REASON: [Briefly state why - e.g. "Unpaid internship", "Salary is 5000", or "Requires experience"]

If the answer to ABOVE QUESTIONS are NO (or if salary information is simply not mentioned), you must output:
RANK: HIGH
REASON: Meets minimum criteria.

Job Description:
{jd[:4000]}
"""
        
        try:
            response = ollama.chat(model='llama3.1', messages=[
                {'role': 'user', 'content': prompt}
            ], options={"temperature": 0})
            
            result = response['message']['content'].strip()
            
            rank_match = re.search(r'RANK:\s*(HIGH|IGNORE)', result, re.IGNORECASE)
            reason_match = re.search(r'REASON:\s*(.*)', result, re.IGNORECASE)
            
            rank = rank_match.group(1).upper() if rank_match else "HIGH"
            reason = reason_match.group(1) if reason_match else result[:100]
            
            print(f"   📊 Result: {rank} - {reason}\n")
            jobs_db[url]["rank"] = rank
            jobs_db[url]["reason"] = reason[:200]
            
        except Exception as e:
            print(f"   ❌ Failed: {e}\n")
            jobs_db[url]["rank"] = "ERROR"
            jobs_db[url]["reason"] = str(e)[:100]
            
        # Save progress securely after every job
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(jobs_db, f, indent=4)

    print("==================================================")
    print(f"✅ Finished! Ranked {len(fresh_jobs)} Indeed jobs using simple pipeline.")
    print("==================================================")

if __name__ == "__main__":
    process_and_rank_jobs()
