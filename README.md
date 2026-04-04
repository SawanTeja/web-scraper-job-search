# ATS Internship Scraper & Local AI Tracker

An autonomous web scraper and job application tracking system that searches for fresh internships across various ATS platforms (Greenhouse, Lever, Workday, etc.), automatically ranks them against your unique candidate profile using a local LLM, and provides a modern GTK4 GUI to manage your applications.

## Features

- **Automated Job Scraping**: Uses Playwright to search Google for roles across 31+ popular Applicant Tracking Systems (ATS) and tech career portals.
- **Anti-Bot Mechanisms**: Built-in human-like scrolling, mouse movements, and random navigation to bypass CAPTCHAs while scraping Google search results.
- **Local AI Ranking Engine**: Integrates with an offline `Ollama` instance running **Llama 3.1** to privately evaluate fetched job descriptions against your personal resume/skills and classify them (HIGH, MEDIUM, LOW, IGNORE).
- **GTK4 Graphical Interface**: A sleek, modern dashboard built with Python GTK4 to manage fresh leads, track ongoing applications, and sort job postings by AI priority.
- **Offline Data Storage**: Job details are seamlessly managed in a lightweight local JSON database (`jobs_db.json`).

---

## Setup & Installation

Ensure you have Python 3.x installed.

### 1. Environment Setup

```bash
# Clone the repository and navigate into the folder
cd web-scraper

# Create a virtual environment
python3 -m venv scraper_env

# Activate the virtual environment
source scraper_env/bin/activate

# Install dependencies
pip install playwright asyncio groq python-dotenv playwright-stealth PyGObject

# Install Playwright browser binaries
playwright install chromium
```

> **Note**: To run the GUI (`gui.py`), you must be on a Linux system with GTK4 libraries available, or have the relevant GObject/GTK4 bindings installed for your OS. For Windows users, the GUI is unsupported out-of-the-box unless you use WSL (Windows Subsystem for Linux) with GUI support enabled, or use the command-line scripts.

### 2. Groq AI Setup

This project uses the Groq Cloud API for fast model inference.

1. Create a `.env` file in the root of your project folder.
2. Add your Groq API key:
```bash
GROQ_API_KEY=your_actual_api_key_here
```

---

## How to Use

### 1. Using the Scraper ONLY (No ML / No GPU Required!)
If you just want to automate the job search and fetch links without AI ranking, you can simply run the scraper script! It requires zero heavy ML hardware and works on any basic laptop.

```bash
python intern_scraper.py
```
This will silently crawl for new roles and save all links directly into the `jobs_db.json` file. You can then open this JSON file to view your fresh job leads!

### 2. The Dashboard (GUI)
Run the graphical interface to manage everything from one place:

```bash
python gui.py
```
- **Scrape Jobs**: Click the "Scrape Jobs" button to start fetching new roles in the background.
- **AI Rank/Sort Jobs**: Once jobs are found, head to the "Priority Sorting" tab and click the AI Rank button to evaluate new listings using Llama 3.1.
- **Track Status**: Move jobs between `New`, `Applied`, `Ongoing`, `Rejected`, and `NA`.

### 2. Running Scripts Manually
Alternatively, you can run the sub-scripts via terminal:
- **Scraper**: `python intern_scraper.py`
- **Ranker**: `python rank_internships.py`

---

## Customizing for YOUR Job Search (Important!)

To make this tool find jobs tailored strictly for you, you need to edit a few specific variables in the scripts. **Do this before running the scraper!**

### 1. Editing What to Search For (`intern_scraper.py`)

Open `intern_scraper.py` and modify the following lists at the top of the file:

- **`SITES`**: This contains the domains the scraper restricts its Google searches to. You can add or remove ATS domains or career portals (e.g., `jobs.apple.com`, `careers.google.com`).
- **`SEARCH_QUERIES`**: This dictates exactly what keywords you want to search for. Update this to match your desired graduation year, role, and tech stack.
  
  *Example:*
  ```python
  SEARCH_QUERIES = [
      '(data scientist OR machine learning) (intern OR internship) 2026',
      '(python OR pytorch OR tensorflow) (intern OR internship)'
  ]
  ```

### 2. Personalizing the AI Evaluator (`rank_internships.py`)

The AI ranks jobs based on a strict set of rules mapped to your resume. Open `rank_internships.py` and modify the following constants:

#### A. Your Candidate Profile
Update the `CANDIDATE_PROFILE` string to accurately reflect your education, skills, prior internships, and career goals. **The AI uses this text to decide if a job is a good fit.**

*Example:*
```python
CANDIDATE_PROFILE = """
Candidate Background:
Education: B.S. Computer Science
Core Skills: Python, AWS, Docker, React...
...
"""
```

#### B. The Ranking Rules Prompt
Scroll down to the `prompt` string inside the `evaluate_job_with_llm` function. Here you dictate exactly what roles you love and what roles you hate.

- **`IGNORE (HARD VETO)`**: Add keywords and roles here that you absolutely NEVER want to see (e.g., "UI/UX, IT Support, unpaid internships, required clearance"). The AI will filter these out instantly.
- **`HIGH`**: Define your dream roles (e.g., "AI infrastructure roles, Python backend engineering, Quant roles").
- **`MEDIUM / LOW`**: Define acceptable but non-ideal roles.

By tweaking these guardrails, the AI will accurately isolate your best-fit job opportunities!

---

## File Structure

- `gui.py`: The GTK4 application dashboard tying the database, scraper, and AI together.
- `intern_scraper.py`: The Playwright bot that scrapes Google searches for job board links.
- `rank_internships.py`: Uses Llama 3.1 via Ollama to download job URLs, read textual descriptions, and rank them.
- `jobs_db.json`: The local SQLite-like JSON database where all your applications and URLs are stored. (Auto-generated).
- `setup.md`: Original quickstart instructions.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'distutils'` or `AttributeError: 'Version' object has no attribute 'version'`
If you are using Python 3.12 or newer, you might encounter an error when running `indeed_scraper.py` because the `distutils` module has been removed from the standard library, which breaks the `undetected_chromedriver` package.

**To fix this:**
Open the patcher file inside your virtual environment. The path usually looks like this:
`scraper_env/lib64/python3.1X/site-packages/undetected_chromedriver/patcher.py` or `scraper_env/lib/python3.1X/site-packages/undetected_chromedriver/patcher.py`

Replace this import at the top of the file:
```python
from distutils.version import LooseVersion
```
With this mock class:
```python
class LooseVersion:
    def __init__(self, vstring):
        self.vstring = str(vstring)
        self.version = [int(x) if x.isdigit() else x for x in self.vstring.split('.')]
    def __str__(self):
        return self.vstring
```
Save the file, and the scraper should now boot up without crashing!
