====================================================================
ATS INTERNSHIP SCRAPER & LOCAL AI RANKING PIPELINE
====================================================================

Follow these steps in order to scrape Google for fresh job postings 
and use your local NVIDIA GPU to rank them against your resume.

--------------------------------------------------------------------
PHASE 1: ENVIRONMENT SETUP (Do this once)
--------------------------------------------------------------------
Open your terminal and navigate to your project folder.

1. Create a new Python virtual environment:
   python3 -m venv scraper_env

2. Activate the virtual environment:
   source scraper_env/bin/activate

3. Install the required Python packages:
   pip install playwright asyncio ollama

4. Install the Playwright Chromium browser binary:
   playwright install chromium

--------------------------------------------------------------------
PHASE 2: LOCAL AI SETUP (Do this once)
--------------------------------------------------------------------
We need to install Ollama and download the Llama 3.1 model so your 
GPU can process the job descriptions locally.

1. Install Ollama:
   curl -fsSL https://ollama.com/install.sh | sh

2. Start the Ollama background service:
   # Note: If it says port already in use, it is already running.
   ollama serve &

3. Download the Llama 3.1 model (4.7GB download):
   ollama pull llama3.1

--------------------------------------------------------------------
PHASE 3: RUN THE WEB SCRAPER (Run this 1-2 times daily)
--------------------------------------------------------------------
This script will search 31+ ATS boards for tech roles posted in 
the last 24 hours. It takes about 2 hours to run fully and auto-saves.

1. Ensure your virtual environment is active:
   source scraper_env/bin/activate

2. Run the scraper:
   python advanced_intern_scraper.py

3. What to watch for:
   - A Chromium browser will open. DO NOT close it.
   - If Google throws a CAPTCHA, click the "I am human" checkbox 
     in that browser window. The terminal will wait for you.
   - You can stop the script at any time with Ctrl+C. The data 
     collected up to that point is safely saved in fresh_internships.csv.

--------------------------------------------------------------------
PHASE 4: RUN THE AI RANKER 
--------------------------------------------------------------------
This script visits the links in fresh_internships.csv, extracts the 
job descriptions, and ranks them against your technical profile.

1. Ensure Ollama is running in the background.

2. Run the ranking script:
   python rank_internships.py

3. What to watch for:
   - This runs invisibly (headless).
   - Your laptop fans will spin up as the NVIDIA GPU processes the text.
   - It will generate a file named ranked_internships.csv.

--------------------------------------------------------------------
PHASE 5: CLEAN UP THE RESULTS (Optional but recommended)
--------------------------------------------------------------------
To instantly remove all the "IGNORE" and "LOW" roles from your final 
CSV so you only see the good matches, run this quick bash command:

   egrep -v ",IGNORE|,LOW" ranked_internships.csv > high_priority_jobs.csv

Now, open high_priority_jobs.csv and start applying!
====================================================================