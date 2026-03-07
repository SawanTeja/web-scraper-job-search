import json
import urllib.parse
import time
import random
import os
from datetime import datetime, timezone
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def scrape_indeed_page_one_only(location="India"):
    keywords = [
        "Backend", "Full Stack", "MERN", "SDE",
        "React.js", "React Native", "Node.js", "Express.js", 
        "MongoDB", "Frontend", "C++", "JavaScript", "Android"
    ]
    
    filter_string = "0kf%3Aattr%2875GKK%7CT9BXE%7CVDTG7%7CZG59D%252COR%29%3B"
    
    filename = "indeed_db.json"
    
    # Load existing JSON database
    db_jobs = {}
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            try:
                db_jobs = json.load(f)
            except json.JSONDecodeError:
                db_jobs = {}
                
    session_new_jobs = 0 
    
    print("[*] Booting up undetected-chromedriver (v145)...")
    options = uc.ChromeOptions()
    # Ensure it matches your Fedora Chrome version
    driver = uc.Chrome(options=options, version_main=145) 
    driver.set_window_size(1920, 1080)
    
    for keyword in keywords:
        q = urllib.parse.quote(keyword)
        l = urllib.parse.quote(location)
        
        print(f"\n[*] Searching for: '{keyword}' (Page 1 Only)")
        
        # The URL without any pagination parameters
        url = f"https://in.indeed.com/jobs?q={q}&l={l}&fromage=1&sc={filter_string}"
        
        driver.get(url)
        
        # Wait for Cloudflare / initial load
        time.sleep(random.uniform(5, 8)) 
        
        try:
            # Wait for the job cards to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".job_seen_beacon"))
            )
            
            jobs = driver.find_elements(By.CSS_SELECTOR, ".job_seen_beacon")
            
            if len(jobs) == 0:
                print("  -> No jobs found for this keyword.")
                continue
                
            page_new_jobs = 0
            
            for job in jobs:
                try:
                    # Extract Link
                    link_el = job.find_element(By.CSS_SELECTOR, "h2.jobTitle a")
                    job_link = link_el.get_attribute("href")
                    
                    # Extract Job Key (jk) from URL for deduplication 
                    parsed_url = urllib.parse.urlparse(job_link)
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    jk = query_params.get("jk", [""])[0]
                    
                    if not jk:
                        jk = job_link[-16:]  # fallback
                    
                    # Clean, unique URL using just the job key
                    job_link_key = f"https://in.indeed.com/viewjob?jk={jk}"
                    
                    # Skip if already in database
                    if job_link_key in db_jobs:
                        continue 
                        
                    # Extract details
                    title_el = job.find_element(By.CSS_SELECTOR, "h2.jobTitle span[title]")
                    title = title_el.get_attribute("title") if title_el else "N/A"
                    
                    company_el = job.find_element(By.CSS_SELECTOR, "span[data-testid='company-name']")
                    company = company_el.text if company_el else "N/A"
                    
                    location_el = job.find_element(By.CSS_SELECTOR, "div[data-testid='text-location']")
                    job_location = location_el.text if location_el else "N/A"
                    
                    # Extract JD by clicking the job card title
                    jd = "N/A"
                    try:
                        # Grab old JD element reference BEFORE clicking (if it exists)
                        old_jd_el = None
                        try:
                            old_jd_el = driver.find_element(By.ID, "jobDescriptionText")
                        except:
                            pass
                        
                        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", job)
                        time.sleep(0.5)
                        
                        # Click the title span (not the <a> which navigates, not the container which may not trigger)
                        driver.execute_script("arguments[0].click();", title_el)
                        
                        # If we had an old JD element, wait for it to go stale (= DOM refreshed with new content)
                        if old_jd_el:
                            try:
                                WebDriverWait(driver, 5).until(EC.staleness_of(old_jd_el))
                            except:
                                pass  # might not go stale if same element is reused, that's ok
                            time.sleep(1)
                        else:
                            time.sleep(2)
                        
                        # Check if we accidentally navigated away
                        if "viewjob" in driver.current_url:
                            driver.back()
                            time.sleep(2)
                        else:
                            # Now grab the fresh JD element
                            jd_el = WebDriverWait(driver, 5).until(
                                EC.presence_of_element_located((By.ID, "jobDescriptionText"))
                            )
                            jd = jd_el.text
                    except Exception as e:
                        print(f"       [!] Could not extract JD for {title}")
                        if "viewjob" in driver.current_url:
                            driver.back()
                            time.sleep(2)
                    
                    # Save into GUI-compatible dict using the clean key
                    db_jobs[job_link_key] = {
                        "title": title,
                        "status": "New",
                        "added_at": datetime.now(timezone.utc).isoformat(),
                        "details": {
                            "company": company,
                            "location": job_location,
                            "job_type": "Indeed Search",
                            "salary": "N/A",
                            "keyword_used": keyword,
                            "jd": jd
                        }
                    }
                    
                    # Save JSON continuously to prevent data loss
                    with open(filename, 'w', encoding='utf-8') as f:
                        json.dump(db_jobs, f, indent=4)
                        
                    page_new_jobs += 1
                    session_new_jobs += 1
                
                except Exception as e:
                    continue
                
            print(f"     Found {len(jobs)} total | Added {page_new_jobs} brand new jobs")
                
        except Exception as e:
            print("     Hit a block or no jobs appeared. Moving to next keyword.")
        
        # Sleep between keywords to stay under Indeed's radar
        sleep_time = random.uniform(15, 25)
        print(f"[*] Finished '{keyword}'. Sleeping for {int(sleep_time)} seconds...\n")
        time.sleep(sleep_time)
    
    driver.quit()
        
    print(f"\nScraping complete! Added {session_new_jobs} brand new jobs today. Results saved in: {filename}")

if __name__ == "__main__":
    scrape_indeed_page_one_only()
