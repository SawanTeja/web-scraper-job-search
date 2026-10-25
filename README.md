# ATS Job Scraper & Groq AI Application Tracker

An autonomous web scraper and job application tracking system that searches for fresh internships and early-career tech roles across 30+ Applicant Tracking Systems (MyWorkday, Greenhouse, Lever, Ashby, SmartRecruiters, Keka, Workable, BambooHR, etc.), automatically extracts and ranks them against your candidate profile using **Groq Cloud API** (high-speed LPU inference), and provides a modern GTK4 GUI dashboard to manage applications.

---

## Key Features

- **High-Speed ATS Direct Scrapers & Keka Engine**: Directly queries company career boards across Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Keka, Breezy, Workable, BambooHR, Jobvite, and Recruitee—bypassing search engine rate limits and CAPTCHAs.
- **ATS Company Discovery Crawler**: Dedicated CLI crawler (`--crawl-companies`) discovering and verifying company career portals across MyWorkday and all major ATS networks, expanding your company pool with zero job spam.
- **Google Discovery Scraper**: Scrapes Google Search with Playwright and anti-bot stealth measures to discover new companies and job postings.
- **Zero-Token Deterministic Pre-Filters**:
  - Automatically filters Senior, Lead, Manager, Staff, and Principal roles.
  - Disqualifies non-tech positions (sales, marketing, HR, operations, retail, trade).
  - Filters out ineligible geographic locations: **United States, Canada, United Kingdom (London, UK), and Paris (France)**.
  - Executes in seconds using **0 AI tokens**, categorizing roles into **Tier 1 (Early Career / Intern)**, **Tier 2 (General SWE)**, and **Tier 3 (Ignored / NA)**.
- **Decoupled Scraping & Filtering**: Scrapers collect and preserve all unique tech job postings in `jobs_db.sqlite`, allowing you to inspect raw postings and run deterministic pre-filtering and AI ranking on demand.
- **Groq AI Multi-Key Rotation (Up to 100 Keys)**: High-speed AI inference using **Qwen 3.8 27B / Qwen 2.5 32B** with automatic fallback to **GPT-OSS 20B**. Seamlessly rotates across up to 100 Groq API keys with per-minute TPM cooldown management and persistent daily token quota tracking (`groq_daily_usage.json`).
- **3-Stage Ranking Pipeline with Stream Processing**:
  - *Stage 1 (Extract JDs)*: Playwright loads job pages (including child `<iframe>` support for iCIMS/Taleo) and Groq LLM extracts clean, structured JSON schemas.
  - *Stage 2 (Deep JD Filter)*: Deterministic 0-token filter checking years of experience (>=1 yr), security clearances, and trade roles.
  - *Stage 3 (AI Rank)*: Evaluates surviving clean postings against your candidate criteria (HIGH, MEDIUM, LOW, IGNORE)—**100% in Python + AsyncGroq with 0 browser overhead**.
  - *Configurable Scope*: Rank **Tier 1 Only** (Early Career / Intern to save tokens) or **Tier 1 & Tier 2** (All Tech / SWE Roles).
- **Modern Linux GTK4 GUI**:
  - Live filter tabs: `ALL`, `HIGH`, `MEDIUM`, `LOW`, `UNKNOWN`, `IGNORE`.
  - **Instant Keyword Search**: Debounced search across titles, companies, locations, and descriptions.
  - One-click application status management: `Fresh`, `Applied`, `Ongoing`, `Rejected`, and `NA`.
  - **Saved Companies Manager**: Browse, search, add, platform filter, or seed top tech company career boards.
  - **Live API Quotas Dialog**: Inspect remaining tokens, requests, and reset timers across all Groq keys.
  - Batch rendering for smooth, responsive scrolling across thousands of jobs.
- **High-Performance SQLite Storage**: Local data stored in `jobs_db.sqlite` with WAL mode for concurrent operations and deleted URL tombstoning to prevent re-scraping dismissed jobs. Database files are fully git-ignored.

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

> **Note**: The desktop GUI (`main.py` / `src/gui/gui.py`) requires Linux GTK4 libraries (`PyGObject`). All scraping, crawling, and ranking features can also be run entirely via the CLI on headless/server environments.

---

### 2. Groq API Configuration

Configure one or more Groq API keys for automatic circular rotation:

1. Create a `.env` file in the project root:
```bash
cp .env.example .env  # or create a new .env file
```

2. Add your Groq API key(s) (supports up to 100 numbered keys):

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

Verify your keys and live token quotas at any time:
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
- **Search Keywords**: Use the top search box in *Fresh Jobs* to search `intern`, `backend`, `react`, company name, etc.
- **Filter by Rank**: Toggle between `HIGH`, `MEDIUM`, `LOW`, `UNKNOWN`, and `IGNORE`.
- **🏢 Scrape Companies**: Run high-speed ATS board scraping across your saved company pool.
- **📋 Saved Companies**: Browse, search, add, or seed 45+ top tech company career boards.
- **⚡ Smart Tier Filter**: Run instant 0-token pre-filtering with live terminal logs.
- **🔑 API Limits**: Inspect live remaining tokens, requests, and daily usage across all keys.
- **Priority Sorting Tab**: Run Stage 1, Stage 2, Stage 3, or the Full Auto Pipeline with your chosen AI Ranking Scope.

---

### 2. Command-Line Workflows

All components can be triggered directly via `main.py`:

| Command | Action | Description |
| :--- | :--- | :--- |
| `python main.py --ats` | **Direct ATS Scraper** | High-speed direct scraping across all saved company ATS boards |
| `python main.py --crawl-companies` | **ATS Company Crawler** | Discovers valid corporate career portals across MyWorkday & ATS platforms |
| `python main.py --scrape` | **Google Scraper** | Playwright discovery scraper across Google search queries |
| `python main.py --filter` | **0-Token Pre-Filter** | Pre-filters Senior, non-tech, US, Canada, UK, and Paris locations (0 tokens) |
| `python main.py --extract` | **Stage 1: Extract JDs** | Headless browser fetch + Groq structured JSON extraction |
| `python main.py --jd-filter` | **Stage 2: Deep JD Filter** | Deep qualification filter on extracted requirements (0 tokens) |
| `python main.py --ai-rank` | **Stage 3: AI Rank** | Evaluates clean jobs against your resume using Groq LLM |
| `python main.py --rank` | **Full Pipeline** | Runs Stage 1 ➔ Stage 2 ➔ Stage 3 in a continuous stream pipeline |
| `python main.py --rank --tier1-only` | **Tier 1 Only Pipeline** | Restricts AI ranking to Tier 1 early career / intern roles to save tokens |
| `python main.py --limits` | **Groq Quota Inspector** | Live RPD / TPM / TPD quota monitor across all configured Groq keys |
| `python main.py --seed` | **Seed Companies** | Populates ATS company pool with top tech company boards |
| `python main.py --backfill` | **Backfill Companies** | Discovers and adds ATS companies from existing database jobs |
| `python main.py --test` | **Run Test Suite** | Runs automated test suite (35 unit tests) |

#### Advanced Crawler Options:
```bash
# Target a specific platform (workday, greenhouse, lever, ashby, smartrecruiters, etc.)
python main.py --crawl-companies --crawl-platform workday

# Set max limit of companies to discover
python main.py --crawl-companies --crawl-limit 100

# Choose crawler mode: all, feeds, or google
python main.py --crawl-companies --crawl-mode feeds
```

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
│   │   ├── company_crawler.py      # ATS company discovery crawler (Workday, Greenhouse, etc.)
│   │   ├── keka_scraper.py         # Keka and Indian tech startup ATS scraper
│   │   ├── intern_scraper.py       # Google Playwright scraper (Standard)
│   │   ├── intern_scraper_fast.py  # Google Playwright scraper (Fast)
│   │   └── intern_scraper_overnight.py # Google Playwright scraper (Overnight batch)
│   └── gui/
│       ├── __init__.py
│       └── gui.py                  # Linux GTK4 GUI dashboard (Search, Ranks, ATS Manager)
├── tests/
│   ├── test_ats_parser.py          # Unit tests for ATS parser
│   ├── test_company_crawler.py     # Unit tests for company discovery crawler
│   ├── test_db.py                  # Unit tests for SQLite DB operations & tombstones
│   ├── test_groq_limits.py         # Unit tests for Groq key rotation & fallback model
│   ├── test_gui_search.py          # Unit tests for GUI keyword search matching
│   └── test_job_filter.py          # Unit tests for deterministic 0-token filters
├── main.py                         # Unified entrypoint (CLI & GUI launcher)
├── jobs_db.sqlite                  # SQLite database file (local & git-ignored)
├── groq_daily_usage.json           # Daily token quota tracking (local & git-ignored)
├── prompts.json                    # Groq LLM prompt templates
├── requirements.txt                # Python package dependencies
└── README.md                       # Documentation
```

---

## Running the Automated Tests

Run the full automated test suite (35 tests covering database operations, ATS parsing, company crawler, Groq multi-key rotation, deterministic filters, and GUI search logic):

```bash
python main.py --test
```
