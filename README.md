# ATS Job Scraper & Groq AI Application Tracker

An autonomous web scraper and job application tracking system that searches for fresh internships and early-career tech roles across 30+ Applicant Tracking Systems (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, etc.), automatically extracts and ranks them against your unique candidate profile using **Groq Cloud API** (high-speed LPU inference), and provides a modern GTK4 GUI dashboard to manage your applications.

---

## Key Features

- **High-Speed ATS Direct Scraper**: Directly queries company career boards across Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Breezy, Workable, BambooHR, Jobvite, and Recruitee—bypassing search engine rate limits and CAPTCHAs.
- **Google Discovery Scraper**: Scrapes Google Search with Playwright and anti-bot stealth measures to discover new companies and job postings, automatically expanding your company pool.
- **3-Tier 0-Token Deterministic Pre-Filter**: Instantly disqualifies Senior/Lead/Manager roles, non-tech positions (sales, marketing, HR, retail), and ineligible locations (US, Canada) in <1 second—**using 0 AI tokens** to conserve your quotas.
- **Groq AI Multi-Key Rotation**: High-speed AI inference using **Qwen 2.5 32B / Qwen 3.8 27B** with automatic fallback to **GPT-OSS 20B**. Seamlessly rotates across multiple Groq API keys (e.g. 7+ keys) with per-minute TPM cooldown management and daily quota rollover.
- **3-Stage Ranking Pipeline**:
  1. *Stage 1 (Extract JDs)*: Uses Playwright to load job pages (including child `<iframe>` support for iCIMS/Taleo) and Groq LLM to extract clean, structured JSON schemas.
  2. *Stage 2 (Deep JD Filter)*: Deterministic 0-token filter checking years of experience (>=1 yr), security clearance, and trade roles.
  3. *Stage 3 (AI Rank)*: Evaluates surviving clean postings against your candidate resume/criteria (HIGH, MEDIUM, LOW, IGNORE)—**100% in Python + AsyncGroq with 0 browser overhead**.
- **Modern Linux GTK4 GUI**:
  - Live filter by AI Rank (ALL, HIGH, MEDIUM, LOW, UNKNOWN, IGNORE).
  - **Keyword Search**: Instant debounced search for keywords (e.g. `intern`, `internship`, `python`, `remote`, company names).
  - One-click application tracking: `Fresh`, `Applied`, `Ongoing`, `Rejected`, and `NA`.
  - Saved Companies Manager with platform filtering and seeding.
  - Live Groq API Quotas & Rate Limits dialog.
- **High-Performance SQLite Storage**: Jobs and companies stored in `jobs_db.sqlite` with WAL mode for concurrent read/write and deleted URL tombstoning to prevent re-scraping dismissed jobs.

---

## Setup & Installation

### 1. Environment Setup

Ensure you have Python 3.10+ installed.

```bash
# Clone the repository and navigate into the project directory
git clone https://github.com/SawanTeja/web-scraper-job-search.git
cd web-scraper-job-search

# Create a virtual environment
python3 -m venv scraper_env

# Activate the virtual environment
source scraper_env/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browser binaries
playwright install chromium
```

> **Note**: The desktop GUI (`main.py` / `src/gui/gui.py`) requires Linux GTK4 libraries (`PyGObject`). All scraping and ranking features can also be run entirely via the CLI on headless environments.

---

### 2. Groq API Configuration

This project uses the Groq Cloud API for ultra-fast structured extraction and job fit evaluation. You can configure a single key or multiple keys for automatic circular rotation:

1. Create a `.env` file in the project root:
```bash
cp .env.example .env  # or create a new .env file
```

2. Add your Groq API key(s):

```env
# Single key:
GROQ_API_KEY="gsk_your_primary_key_here"

# OR Multi-Key Circular Rotation (Recommended to maximize daily quotas):
GROQ_API_KEY_1="gsk_key_one..."
GROQ_API_KEY_2="gsk_key_two..."
GROQ_API_KEY_3="gsk_key_three..."
GROQ_API_KEY_4="gsk_key_four..."
GROQ_API_KEY_5="gsk_key_five..."
GROQ_API_KEY_6="gsk_key_six..."
GROQ_API_KEY_7="gsk_key_seven..."

# OR comma-separated:
# GROQ_API_KEYS="gsk_key1,gsk_key2,gsk_key3"
```

You can verify your configured keys and live token quotas at any time:
```bash
python main.py --limits
```

---

## How to Use

### 1. Launch the Desktop GUI Dashboard
```bash
python main.py
# or:
python main.py --gui
```
From the GUI you can:
- **Search Keywords**: Use the top-right search box in *Fresh Jobs* to search `intern`, `internship`, `backend`, `react`, etc.
- **Filter by Rank**: View only `HIGH`, `MEDIUM`, or `LOW` priority listings.
- **🏢 Scrape Companies**: Run high-speed ATS board scraping across all saved companies.
- **📋 Saved Companies**: Browse, search, add, or seed top tech company career boards.
- **⚡ Smart Tier Filter**: Instant 0-token pre-filter on scraped jobs.
- **🔑 API Limits**: Inspect live remaining tokens and requests across all Groq keys.
- **Priority Sorting Tab**: Run Stages 1, 2, and 3 or the Full Auto Pipeline.

---

### 2. Command-Line Workflows

All components can be triggered via `main.py`:

| Command | Action | Description |
| :--- | :--- | :--- |
| `python main.py --ats` | **Direct ATS Scraper** | High-speed direct scraping across all saved company ATS boards |
| `python main.py --scrape` | **Google Scraper** | Playwright discovery scraper across Google search queries |
| `python main.py --filter` | **0-Token Pre-Filter** | Pre-filters Senior, non-tech, US/Canada locations (0 AI tokens) |
| `python main.py --extract` | **Stage 1: Extract JDs** | Headless browser fetch + Groq structured JSON extraction |
| `python main.py --jd-filter` | **Stage 2: Deep JD Filter** | Deep qualification filter on extracted requirements (0 tokens) |
| `python main.py --ai-rank` | **Stage 3: AI Rank** | Evaluates clean jobs against your resume using Groq LLM |
| `python main.py --rank` | **Full Pipeline** | Runs Stage 1 ➔ Stage 2 ➔ Stage 3 in a continuous pipeline |
| `python main.py --limits` | **Groq Quota Inspector** | Live RPD / TPM / TPD quota monitor across all Groq API keys |
| `python main.py --seed` | **Seed Companies** | Populates ATS company pool with 45+ top tech company boards |
| `python main.py --backfill` | **Backfill Companies** | Discovers and adds ATS companies from existing database jobs |
| `python main.py --test` | **Run Test Suite** | Runs automated test suite (database, filter, limits, search) |

---

## Customizing for YOUR Background & Preferences

### 1. Personalizing the Candidate Profile & AI Ranking
Open [`src/ranking/rank_internships.py`](file:///home/sawan/Tejashvi/web-scraper-job-search/src/ranking/rank_internships.py) and update `CANDIDATE_PROFILE` (around line 180):

```python
CANDIDATE_PROFILE = """
Candidate Background:
- Education: B.S. in Computer Science / Software Engineering
- Core Programming Languages: Python, C++, TypeScript, Go
- Frameworks & Technologies: React, Node.js, FastAPI, Docker
- Career Goals: Internships & Early Career Software Engineering
"""
```

The AI Evaluator reads this profile and scores jobs according to your prompt rules in `src/ranking/rank_internships.py` (`evaluate_job_with_llm`).

### 2. Customizing Search Queries
Open [`src/scrapers/intern_scraper_fast.py`](file:///home/sawan/Tejashvi/web-scraper-job-search/src/scrapers/intern_scraper_fast.py) to configure:
- `SEARCH_QUERIES`: Target roles, graduation years, tech stacks.
- `SITES`: Target ATS platforms or custom career portals.

---

## Project Structure

```
web-scraper-job-search/
├── src/
│   ├── core/
│   │   ├── __init__.py
│   │   └── db.py                   # SQLite database (WAL mode, migrations, tombstones)
│   ├── ranking/
│   │   ├── __init__.py
│   │   ├── groq_limits.py          # Parallel live Groq quota & rate-limit checker
│   │   ├── job_filter.py           # Deterministic 0-token pre-filter & deep JD filter
│   │   ├── rank_internships.py     # 3-Stage Groq LLM ranking pipeline & circular key rotation
│   │   └── reset_ranks.py          # Rank reset utility
│   ├── scrapers/
│   │   ├── __init__.py
│   │   ├── ats_parser.py           # ATS platform & company identifier detection
│   │   ├── ats_scraper.py          # Multi-threaded direct ATS scraping engine
│   │   ├── intern_scraper.py       # Google Playwright scraper (Standard)
│   │   ├── intern_scraper_fast.py  # Google Playwright scraper (Fast)
│   │   └── intern_scraper_overnight.py # Google Playwright scraper (Overnight batch)
│   └── gui/
│       ├── __init__.py
│       └── gui.py                  # Linux GTK4 GUI dashboard (Search, Ranks, ATS Manager)
├── tests/
│   ├── test_ats_parser.py          # Unit tests for ATS parser
│   ├── test_db.py                  # Unit tests for SQLite DB operations & tombstones
│   ├── test_groq_limits.py         # Unit tests for Groq key rotation & fallback model
│   ├── test_gui_search.py          # Unit tests for GUI keyword search matching
│   └── test_job_filter.py          # Unit tests for deterministic 0-token filters
├── main.py                         # Unified entrypoint (CLI & GUI launcher)
├── jobs_db.sqlite                  # SQLite database file (auto-created)
├── prompts.json                    # Groq LLM prompt templates
├── requirements.txt                # Python package dependencies
└── README.md                       # Documentation
```

---

## Running the Automated Tests

Verify that database handlers, ATS parsers, Groq key rotation, deterministic filters, and GUI search logic are working properly:

```bash
python main.py --test
```
