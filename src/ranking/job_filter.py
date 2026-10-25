"""
job_filter.py — 100% Deterministic Pre-Filtering Engine (Zero AI Tokens).

Implements 3-Tier Job Categorization:
  - Tier 1 (High Priority): Early Career, Internships, Junior, Apprentice, Graduate, Associate SWE.
            Pre-ranked as 'HIGH', status='New' (instant priority in GUI, 0 tokens wasted).
  - Tier 2 (General Tech / SWE): Entry-level / general software engineering & developer roles.
            Ranked as 'UNKNOWN', status='New' (ready for Groq LLM evaluation).
  - Tier 3 (Deterministic IGNORE): Senior/Lead/Manager/Supervisor/Pleno, Non-Tech (Sales, HR, Helpdesk, Assembly),
            Non-tech Interns (Marketing Intern), US-based locations, and Career Hub landing pages.
            Ranked as 'IGNORE', status='NA' (0 tokens wasted).
"""
import re
import sys
import os
import json
import unicodedata
from datetime import datetime, timezone

_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ROOT_DIR = os.path.abspath(os.path.join(_SRC_DIR, ".."))
for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from src.core.db import get_conn, init_db, get_all_companies
except ImportError:
    from db import get_conn, init_db, get_all_companies


def normalize_text(text: str) -> str:
    """Strips accents/diacritics and normalizes whitespace/punctuation."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"[_/\\]+", " ", stripped).strip()


# ──────────────────────────────────────────────
#  Patterns for Senior / Non-Entry Level
# ──────────────────────────────────────────────
SENIOR_TITLE_PATTERNS = [
    # Explicit seniority & leadership
    r"\bsenior\b",
    r"\bsr\.?\b",
    r"\blead(?:er|ership)?\b",
    r"\bteam\s*lead(?:er)?\b",
    r"\bprincipal\b",
    r"\bstaff\b",
    r"\bexpert\b",
    r"\bchief\b",
    r"\barchitect\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice\s+president\b",
    r"\bhead\s+of\b",
    r"\bmanager\b",
    r"\bmanagement\b",
    r"\bconsultant\b",

    # Supervisors & Coordinators
    r"\bsupervisor\b",
    r"\bsupervising\b",
    r"\bsupervision\b",
    r"\bcoordinator\b",

    # International mid-to-senior levels (LATAM / Europe)
    r"\bplen[oa]\b",          # Mid-level in Brazil/Portugal/LATAM (2-5 yrs)
    r"\bsemi[- ]senior\b",    # Mid-level in Spanish LATAM
    r"\bssr\b",               # Semi-Senior abbreviation
    r"\bmedior\b",            # Mid-level in Netherlands/Europe
    r"\bmid[- ]level\b",
    r"\bmid[- ]senior\b",
    r"\bexperienced\b",

    # Numerical levels (II, III, IV, V, 2, 3, 4, 5, etc.)
    r"\b(?:sde|swe|engineer|developer|architect|consultant|analyst)\s*[-_ ]*(?:ii|iii|iv|v|2|3|4|5)\b",
    r"\b(?:software\s+)?(?:development\s+)?engineer\s*[-_ ]*(?:ii|iii|iv|v|2|3|4|5)\b",
    r"\b(?:level|lvl)\s*[23456]\b",
    r"\b[l|e][2345678]\b",

    # Multi-year experience directly in title (e.g. "3+ years", "5+ yrs", "3-5 years")
    r"\b(?:[2-9]|\d{2,})\+?\s*(?:years?|yrs?)(?:\s+exp(?:erience)?)?\b",
    r"\b(?:[2-9]|\d{2,})\s*[-–]\s*\d+\s*(?:years?|yrs?)\b",
]

SENIOR_TITLE_REGEX = re.compile("|".join(SENIOR_TITLE_PATTERNS), re.I)

# Experience requirement patterns in JD body
EXPERIENCE_PATTERNS = [
    r"\b(?:[3-9]|\d{2,})\+?\s*(?:years?|yrs?)\b",
    r"\b(?:at\s+least|minimum\s+of|more\s+than)\s+[2-9]\s*(?:years?|yrs?)\b",
    r"\b(?:3|4|5|6|7|8|9|10)\+?\s*years?\s+of\s+(?:software|engineering|relevant|professional)\s+experience\b",
]
EXPERIENCE_REGEX = re.compile("|".join(EXPERIENCE_PATTERNS), re.I)

# Explicit experience threshold patterns (Disqualifies roles requiring >= 1 year)
EXPERIENCE_EXCEEDED_PATTERNS = [
    # Explicit 1+ or 2+ or more years: e.g. "1+ years", "1+ yr", "2+ years", "5+ yrs", "10+ years"
    r"\b(?:1\s*\+|[2-9]\+?|\d{2,}\+?)\s*(?:years?|yrs?)(?:\s+(?:of\s+)?(?:experience|exp|software|development|professional|relevant|industry|work))?\b",

    # Ranges starting at 1 or more: "1-2 years", "1 to 3 yrs", "2-5 years" (safeguard against "0-1" or "0 to 1")
    r"(?:^|[^\d\-])(?:[1-9]|\d{2,})\s*(?:[-–]|to)\s*\d+\s*(?:years?|yrs?)\b",

    # Minimum / at least: "at least 1 year", "minimum 1 year", "min 1 yr", "min. 1 year", "minimum of 1 year"
    r"\b(?:at\s+least|minimum(?:\s+of)?|min\.?)\s*(?:1|one|[2-9]|\d+)\s*(?:\+)?\s*(?:years?|yrs?)\b",

    # "Requires 1 year of experience" / "1 year minimum"
    r"\b(?:requires?|required)\s+(?:a\s+minimum\s+of\s+)?(?:1|one|[2-9]|\d+)\s*(?:years?|yrs?)\b",
    r"\b(?:1|one|[2-9]|\d+)\s*(?:years?|yrs?)\s+minimum\b",

    # Textual numbers: "one+ years", "two years of experience", etc.
    r"\b(?:one\s*\+|two|three|four|five|six|seven|eight|nine|ten)\s*(?:\+)?\s*(?:years?|yrs?)(?:\s+(?:of\s+)?(?:experience|exp|software|development|professional|relevant))?\b",
]
EXPERIENCE_EXCEEDED_REGEX = re.compile("|".join(EXPERIENCE_EXCEEDED_PATTERNS), re.I)

# Safeguard regex for 0-experience/entry-level/intern expressions (e.g. "0-1 years", "0 to 1 year", "fresher")
EXPERIENCE_ENTRY_SAFEGUARD_REGEX = re.compile(
    r"\b(?:0\s*(?:[-–]|to)\s*[12]\s*(?:years?|yrs?)|no\s+experience|0\s+years?|fresh\s*(?:grad(?:uate)?|er)?|intern(?:ship)?|entry\s*level|student)\b",
    re.I
)



# ──────────────────────────────────────────────
#  Patterns for Non-Tech Roles
# ──────────────────────────────────────────────
NON_TECH_PATTERNS = [
    # Business, Sales, Account Management
    r"\b(?:sales|account executive|account manager|business development|bdr|sdr|account partner|partner manager|commercial)\b",

    # HR, Talent, Recruiting
    r"\b(?:recruiter|recruiting|talent acquisition|human resources|\bhr\b|sourcer|headhunter|people operations)\b",

    # Marketing, Content, Copywriting, Social Media, PR
    r"\b(?:marketing|social media|copywriter|content creator|seo specialist|communications specialist|public relations|\bpr\b)\b",

    # Finance, Accounting, Audit, Tax
    r"\b(?:finance|accountant|accounting|financial analyst|payroll|billing|auditor|controller|treasury|transfer pricing)\b",

    # Legal, Compliance, Regulatory
    r"\b(?:legal|attorney|counsel|lawyer|paralegal|compliance officer|regulatory affairs|tax advisor)\b",

    # Healthcare, Medical, Nursing
    r"\b(?:nurse|nursing|physician|doctor|medical assistant|dental|healthcare assistant|pharmacist|veterinar(?:y|ian))\b",

    # Customer Service, Helpdesk, Technical Support
    r"\b(?:customer service|customer support|client support|call center|help\s*desk|support specialist|service desk)\b",
    r"\b(?:desktop support|it support|technical support|support technique|repr[eé]sentant(?:\s+support)?)\b",

    # Administration, Reception, Secretarial
    r"\b(?:receptionist|office manager|executive assistant|administrative assistant|front desk|office coordinator|clerk)\b",

    # Retail, Food Service, Hospitality, Warehouse (English & French)
    r"\b(?:warehouse|store associate|driver|delivery|barista|cook|chef|cashier|retail|barman|waiter|hotel)\b",
    r"\b(?:caissier|caissi[eè]re|commis(?:\s+[aà]\s+l['’]inventaire)?|journalier|magasinier)\b",

    # Physical / Civil / Mechanical Engineering & Trades
    r"\b(?:civil engineer|mechanical engineer|structural engineer|construction|technician|electrician|facilities|hvac)\b",
    r"\b(?:building\s+(?:maintenance\s+)?mechanic|heavy\s+equipment|repair\s+mechanic|maintenance\s+mechanic)\b",
    r"\b(?:sewage|wastewater|water\s+treatment|treatment\s+plant|plumbing|plumber)\b",
    r"\b(?:nuclear\s+facility|nuclear\s+cognizant|power\s+plant\s+operator)\b",
    r"\b(?:maritime\s+technical\s+operations|vessel\s+operations)\b",
    r"\b(?:geolog(?:y|ist|ical)|geotechnical|asbestos|metallurg(?:y|ical)|mining\s+engineer|environmental|hydrolog(?:y|ist))\b",
    r"\b(?:fire\s+engineer|fire\s+safety|safety\s+officer|security\s+officer|security\s+guard|health\s+and\s+safety|ehs)\b",
    r"\b(?:traffic\s+engineer|process\s+engineer|field\s+service|warranty|surveyor|surveying)\b",

    # Manufacturing, Assembly, Shift Operations
    r"\b(?:subassembl(?:y|ies)|assembl(?:y|er)|shift\s+supervisor|operator|machine\s+operator|fabrication|welder|machinist|maintenance\s+technician)\b",

    # Creative / Visual non-coding design
    r"\b(?:graphic\s+designer|visual\s+designer|video\s+editor|animat(?:or|ion)|fashion|interior\s+designer)\b",

    # Supply chain & Logistics & Administration
    r"\b(?:logistics?|supply\s+chain|procurement|inventory|logisztika|shipping|purchasing)\b",
    r"\bgestionnaire(?:\s*,\s*(?:comptes|approvisionnement|produits|relation|incidents|clients?))?\b",
    r"\bsecr[eé]taire(?:\s+m[eé]dical[e]?)?\b",
    r"\bcomptabilit[eé]\b",
    r"\b(?:sourcing\s+buyer|quotation\s+specialist|paid\s+search\s+analyst|ppc\s+analyst)\b",

    # Foreign language industrial / mechanical terms
    r"\b(?:contrôleur|esthétique|habillage|horloger|mouvement|industrialisation|simulation\s+des\s+chocs)\b",
]

NON_TECH_REGEX = re.compile("|".join(NON_TECH_PATTERNS), re.I)

TECH_SAFEGUARD_REGEX = re.compile(
    r"\b(?:software|developer|programmer|full\s*stack|front\s*end|back\s*end|web\s+developer|mobile|ios|android|ai\s+engineer|ml\s+engineer|machine\s+learning|data\s+scientist|data\s+engineer|devops|sre|site\s+reliability|cloud\s+engineer|cyber|security\s+engineer|systems\s+engineer|qa\s+engineer|sdet|test\s+automation|embedded\s+software|firmware)\b",
    re.I
)

HARD_NON_TECH_REGEX = re.compile(
    r"\b(?:caissier|caissi[eè]re|commis|journalier|magasinier|secr[eé]taire|mechanic|sewage|wastewater|sourcing\s+buyer|quotation\s+specialist)\b",
    re.I
)


# ──────────────────────────────────────────────
#  Patterns for Generic Career Hubs / Landing Pages
# ──────────────────────────────────────────────
CAREER_HUB_PATTERNS = [
    r"^(?:[A-Za-z0-9\s,\.&'’\-_/]+?\s+)?(?:careers|career\s+opportunities|jobs|job\s+openings|open\s+positions)\s*$",
    r"^(?:careers|jobs|working)\s+at\s+.*$",
    r"^(?:join\s+(?:our\s+team|us)|work\s+with\s+us)\s*$",
    r"^(?:search\s+jobs|job\s+search|current\s+openings)\s*$",
]
CAREER_HUB_REGEX = re.compile("|".join(CAREER_HUB_PATTERNS), re.I)

# Specific role keyword safeguard: if title includes any of these, it's a specific role, not a generic career hub
TECH_ROLE_KEYWORDS = re.compile(
    r"\b(?:engineer|developer|programmer|architect|analyst|scientist|intern|designer|specialist|technician|consultant|coder|qa|sdet|swe|sde)\b",
    re.I
)


# ──────────────────────────────────────────────
#  Patterns for Early Career (Tier 1)
# ──────────────────────────────────────────────
EARLY_CAREER_PATTERNS = [
    # Explicit internship keywords (with strict word boundaries to avoid 'internal')
    r"\bintern(?:ship)?s?\b",
    r"\bstagiaire\b",       # French intern
    r"\bpraktikant\b",      # German intern
    r"\bestagi[aá]rio\b",   # Portuguese intern
    r"\bpasant[eí]a?\b",    # Spanish intern
    r"\bco-?ops?\b",
    r"\bapprentice(?:ship)?s?\b",

    # Junior / Entry / Graduate keywords
    r"\b(?:junior|jr\.?)\b",
    r"\bentry\s*[- ]?level\b",
    r"\b(?:sde|swe|engineer|developer)\s*[-_ ]*1\b",
    r"\b(?:sde|swe|engineer|developer)\s*[-_ ]*i\b",
    r"\b(?:level|lvl)\s*1\b",
    r"\b[l|e]1\b",

    # Graduate & University / Student roles
    r"\b(?:new\s*)?grad(?:uate)?s?\b",
    r"\bcampus(?:\s+hire)?\b",
    r"\btrainee\b",
    r"\bwerkstudent\b",
    r"\bworking\s+student\b",

    # Associate Software / Tech roles
    r"\bassociate\s+(?:software\s+)?(?:engineer|developer|programmer|analyst|data|qa|sdet)\b",
    r"\bassociate\s+sde\b",
]

EARLY_CAREER_REGEX = re.compile("|".join(EARLY_CAREER_PATTERNS), re.I)


# ──────────────────────────────────────────────
#  Patterns for US Locations (Country, States, Tech Hubs)
# ──────────────────────────────────────────────
US_COUNTRY_PATTERNS = [
    r"\bunited\s+states\b",
    r"\bu\.s\.a?\b",
    r"\busa\b",
    r"\bus\s+remote\b",
    r"\bremote\s*[-–(]\s*us\b",
    r"\bremote\s+in\s+the\s+us\b",
    r"\bus\s+only\b",
    r"\bwithin\s+the\s+us\b",
    r"\bus\s+citizenship\b",
    r"\bus\s+work\s+authori[zs]ation\b",
]

US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas",
    "Utah", "Vermont", "Virginia", "Washington", "West Virginia",
    "Wisconsin", "Wyoming", "District of Columbia"
]

US_STATE_ABBRS = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
]

US_CITIES = [
    "San Francisco", "Silicon Valley", "San Jose", "Bay Area", "Los Angeles",
    "San Diego", "Seattle", "Redmond", "Bellevue", "Austin", "Dallas",
    "Houston", "New York City", "NYC", "Manhattan", "Brooklyn", "Boston",
    "Cambridge", "Chicago", "Atlanta", "Denver", "Boulder", "Portland",
    "Salt Lake City", "Miami", "Pittsburgh", "Philadelphia", "Washington DC",
    "Phoenix", "Raleigh", "Charlotte", "Minneapolis", "Detroit", "Sunnyvale",
    "Palo Alto", "Mountain View", "Menlo Park", "Cupertino", "Santa Clara",
    "Omaha", "Des Moines", "Lincoln", "Sioux Falls", "Milwaukee", "Indianapolis",
    "Nashville", "Memphis", "Louisville", "Oklahoma City", "Kansas City",
    "Cleveland", "Columbus", "Cincinnati", "Tampa", "Orlando", "Jacksonville",
    "St. Louis", "Saint Louis", "San Antonio", "Honolulu", "Anchorage",
    "Las Vegas", "Sacramento", "Baltimore", "Richmond", "Providence", "Hartford",
    "Jonestown", "Eden Prairie", "Maple Grove"
]

# Explicit Non-US indicators to prevent false positives with US state abbreviations
# (e.g. Bangalore, IN is India; Toronto, CA is Canada; Berlin, DE is Germany)
NON_US_REGIONS = [
    # India tech hubs & regions
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "noida", "gurgaon", "gurugram",
    "mumbai", "delhi", "chennai", "kolkata", "ahmedabad", "vadodara", "jaipur", "indore",
    "chandigarh", "coimbatore", "kochi", "kerala", "karnataka", "telangana", "haryana",

    # UK, Europe, Commonwealth
    "united kingdom", "london", "manchester", "birmingham", "scotland", "edinburgh",
    "germany", "berlin", "munich", "frankfurt", "hamburg",
    "france", "paris", "lyon",
    "netherlands", "amsterdam", "rotterdam",
    "poland", "warsaw", "krakow",
    "singapore", "australia", "sydney", "melbourne",
    "ireland", "dublin", "switzerland", "zurich", "geneva",
    "spain", "madrid", "barcelona", "sweden", "stockholm",
    "brazil", "sao paulo", "japan", "tokyo"
]

# Unambiguous US state abbreviations (strictly uppercase, preceded by comma)
# "IN" is omitted because it is the country code for India (e.g. Bangalore, IN)
# "CA" is checked separately because CA is Canada (e.g. Toronto, CA)
# "DE" is omitted because DE is Germany (e.g. Berlin, DE)
SAFE_US_STATE_ABBRS = [
    "AK", "AL", "AR", "AZ", "CO", "CT", "DC", "FL", "GA", "HI",
    "IA", "ID", "IL", "KS", "KY", "LA", "MA", "MD", "ME", "MI",
    "MN", "MO", "MS", "MT", "NC", "ND", "NE", "NH", "NJ", "NM",
    "NV", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY"
]

US_COUNTRY_REGEX = re.compile("|".join(US_COUNTRY_PATTERNS), re.I)
US_STATES_REGEX = re.compile(r"\b(" + "|".join(US_STATES) + r")\b", re.I)
US_CITIES_REGEX = re.compile(r"\b(" + "|".join(US_CITIES) + r")\b", re.I)
US_STATE_ABBR_REGEX = re.compile(r",\s*(" + "|".join(SAFE_US_STATE_ABBRS) + r")(?:\s*,\s*(?:USA|United States|US)\b|\s*$|\s*[,\)\-])")
NON_US_REGEX = re.compile(r"\b(" + "|".join(NON_US_REGIONS) + r")\b", re.I)

# ──────────────────────────────────────────────
#  Patterns for Canadian Locations
# ──────────────────────────────────────────────
CANADA_COUNTRY_PATTERNS = [
    r"\bcanada\b",
    r"\bcanadian\b",
    r"\bca\s+remote\b",
    r"\bremote\s*[-–(]\s*(?:canada|can)\b",
    r"\bremote\s+in\s+canada\b",
    r"\bwithin\s+canada\b",
    r"\bcanada\s+only\b",
    r"\beligib(?:le|ility)\s+to\s+work\s+in\s+canada\b",
    r"\bauthori[zs]ed\s+to\s+work\s+in\s+canada\b",
]

CANADA_PROVINCES = [
    "Ontario", "Quebec", "Québec", "British Columbia", "Alberta",
    "Manitoba", "Saskatchewan", "Nova Scotia", "New Brunswick",
    "Newfoundland", "Prince Edward Island", "Northwest Territories",
    "Yukon", "Nunavut"
]

CANADA_PROVINCE_ABBRS = [
    "ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "YT"
]

CANADA_CITIES = [
    "Toronto", "Vancouver", "Montreal", "Montréal", "Ottawa", "Calgary",
    "Edmonton", "Quebec City", "Ville de Québec", "Winnipeg", "Mississauga",
    "Brampton", "Hamilton", "Waterloo", "Kitchener", "Halifax", "Surrey",
    "Markham", "Richmond Hill", "Laval", "Gatineau", "Longueuil", "Burnaby",
    "Saskatoon", "Regina", "St. John's", "Saint John", "Oakville", "Burlington",
    "Oshawa", "Barrie", "Guelph", "Kingston", "Dartmouth", "Kelowna", "Fredericton",
    "Moncton", "Charlottetown", "Whitehorse", "Yellowknife", "Lévis", "Levis"
]

CANADA_COUNTRY_REGEX = re.compile("|".join(CANADA_COUNTRY_PATTERNS), re.I)
CANADA_PROVINCES_REGEX = re.compile(r"\b(" + "|".join(CANADA_PROVINCES) + r")\b", re.I)
CANADA_CITIES_REGEX = re.compile(r"\b(" + "|".join(CANADA_CITIES) + r")\b", re.I)
CANADA_PROVINCE_ABBR_REGEX = re.compile(r"(?:,\s*)(" + "|".join(CANADA_PROVINCE_ABBRS) + r")(?:\s*,\s*Canada|\s*$|[,\s\)\-])", re.I)
CANADA_VICTORIA_REGEX = re.compile(r"\bVictoria,\s*(?:BC|Canada|British\s+Columbia)\b", re.I)

# ──────────────────────────────────────────────
#  Patterns for UK Locations
# ──────────────────────────────────────────────
UK_COUNTRY_PATTERNS = [
    r"\bunited\s+kingdom\b",
    r"\bu\.k\.?\b",
    r"\bgreat\s+britain\b",
    r"\bbritain\b",
    r"\bengland\b",
    r"\bscotland\b",
    r"\bwales\b",
    r"\bnorthern\s+ireland\b",
    r"\buk\s+remote\b",
    r"\bremote\s*[-–(]\s*(?:uk|u\.k\.|united\s+kingdom|gb)\b",
    r"\bremote\s+(?:in|from)\s+(?:the\s+)?uk\b",
    r"\buk\s+only\b",
    r"\bwithin\s+(?:the\s+)?uk\b",
    r"\buk\s+citizenship\b",
    r"\buk\s+work\s+authori[zs]ation\b",
    r"\bright\s+to\s+work\s+in\s+(?:the\s+)?uk\b",
    r"\beligib(?:le|ility)\s+to\s+work\s+in\s+(?:the\s+)?uk\b",
]

UK_CITIES = [
    "London", "Manchester", "Birmingham", "Edinburgh", "Glasgow", "Bristol",
    "Leeds", "Sheffield", "Liverpool", "Newcastle", "Nottingham", "Cardiff",
    "Belfast", "Southampton", "Oxford", "Brighton", "Leicester", "Coventry",
    "Aberdeen", "Exeter", "Norwich", "Swindon", "Milton Keynes", "Slough",
    "Guildford", "Portsmouth", "Derby", "Dundee", "Bournemouth", "Plymouth",
    "Swansea", "Middlesbrough", "Sunderland", "Warrington", "Huddersfield",
    "Poole", "Ipswich", "Blackpool", "Bolton", "Peterborough", "Stockport",
    "Greater London", "Greater Manchester"
]

UK_SPECIAL_CITIES = [
    # Cambridge (guard against Cambridge, MA)
    r"\bCambridge,\s*(?:UK|United\s+Kingdom|England|Cambridgeshire|GB)\b",
    # Reading (guard against common English verb "reading")
    r"\bReading,\s*(?:UK|United\s+Kingdom|England|Berkshire|GB)\b",
    # Bath (guard against common noun "bath")
    r"\bBath,\s*(?:UK|United\s+Kingdom|England|Somerset|GB)\b",
    # York (guard against "New York")
    r"(?<!New\s)\bYork,\s*(?:UK|United\s+Kingdom|England|North\s+Yorkshire|GB)\b",
]

UK_COUNTRY_REGEX = re.compile("|".join(UK_COUNTRY_PATTERNS), re.I)
UK_CITIES_REGEX = re.compile(r"\b(" + "|".join(UK_CITIES) + r")\b", re.I)
UK_SPECIAL_CITIES_REGEX = re.compile("|".join(UK_SPECIAL_CITIES), re.I)


# ──────────────────────────────────────────────
#  Patterns for Paris / Île-de-France Locations
# ──────────────────────────────────────────────
PARIS_PATTERNS = [
    r"\bparis\b",
    r"\b(?:île-de-france|ile-de-france)\b",
    r"\br[ée]gion\s+parisienne\b",
    r"\bgreater\s+paris\b",
    r"\bparis\s+area\b",
    r"\bla\s+d[ée]fense\b",
    r"\bparis\s+remote\b",
    r"\bremote\s*[-–(]\s*paris\b",
    r"\bparis,\s*(?:france|fr)\b",
    # Key Paris tech & business suburbs
    r"\bboulogne[- ]billancourt\b",
    r"\bissy[- ]les[- ]moulineaux\b",
    r"\blevallois[- ]perret\b",
    r"\bneuilly[- ]sur[- ]seine\b",
    r"\bcourbevoie\b",
    r"\bsaint[- ]denis\b",
    r"\bmassy\b",
    r"\bsaclay\b",
    r"\bvélizy[- ]villacoublay|velizy[- ]villacoublay\b",
    r"\bmontrouge\b",
    r"\bclichy\b",
]

PARIS_REGEX = re.compile("|".join(PARIS_PATTERNS), re.I)


# ──────────────────────────────────────────────
#  Deterministic Evaluation Functions
# ──────────────────────────────────────────────

def is_senior_or_lead(title: str, jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is Senior/Lead/Manager/Supervisor/Pleno/SDE 2+ from title."""
    if title:
        norm_title = normalize_text(title)
        m = SENIOR_TITLE_REGEX.search(title) or SENIOR_TITLE_REGEX.search(norm_title)
        if m:
            return True, f"Senior/Lead level detected in title: '{m.group(0)}'"

    return False, ""


def is_non_tech(title: str, jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is non-technical (Sales, HR, Helpdesk, Assembly, Cashier, etc.) from title."""
    if title:
        norm_title = normalize_text(title)

        # 1. Hard non-tech roles never get exempted by tech words (e.g. Cashier, Mechanic, Sewage)
        hm = HARD_NON_TECH_REGEX.search(title) or HARD_NON_TECH_REGEX.search(norm_title)
        if hm:
            return True, f"Non-tech role detected in title: '{hm.group(0)}'"

        # 2. General non-tech pattern check
        m = NON_TECH_REGEX.search(title) or NON_TECH_REGEX.search(norm_title)
        if m:
            # Safeguard: if title explicitly mentions software engineering / development, keep it
            if TECH_SAFEGUARD_REGEX.search(title) or TECH_SAFEGUARD_REGEX.search(norm_title):
                return False, ""
            return True, f"Non-tech role detected in title: '{m.group(0)}'"

    return False, ""


def is_career_hub(title: str) -> tuple[bool, str]:
    """Returns (True, reason) if title is a generic career portal landing page (e.g. 'Duolingo Careers')."""
    if not title:
        return False, ""
    clean = title.strip()
    # Guard: genuine tech job postings contain role nouns and are NOT landing pages
    if TECH_ROLE_KEYWORDS.search(clean):
        return False, ""
    clean_norm = normalize_text(clean)
    if CAREER_HUB_REGEX.match(clean) or CAREER_HUB_REGEX.match(clean_norm):
        return True, f"Career hub landing page detected: '{clean}'"
    return False, ""


def is_experience_exceeded(exp_text: str) -> tuple[bool, str]:
    """
    Evaluates whether an experience string requires 1 or more years of experience.
    Returns (True, reason) if experience >= 1 year is required.
    Allows 0-experience, '0-1 year', 'None', freshers, interns, and students.
    """
    if not exp_text:
        return False, ""

    clean = normalize_text(str(exp_text)).strip()
    clean_lower = clean.lower()

    if clean_lower in ["none", "null", "n/a", "no", "not specified", "0", "0 years", "0-1", "0-1 year", "0-1 years"]:
        return False, ""

    # Safeguards: 0-1 year, freshers, students, internships
    if re.search(r"\b0\s*(?:[-–]|to)\s*1\s*(?:years?|yrs?)", clean, re.I):
        return False, ""
    if any(k in clean_lower for k in ["no experience", "fresher", "fresh graduate", "intern", "student"]) and not re.search(r"\b(?:1\s*\+|[1-9]|\d{2,})\s*(?:years?|yrs?)", clean, re.I):
        return False, ""

    # Check for >= 1 year requirement (e.g. "1+ years", "2+ years", "1-2 years", "min 1 year")
    m = EXPERIENCE_EXCEEDED_REGEX.search(clean)
    if m:
        return True, f"Requires experience >= 1 year: '{m.group(0).strip()}'"

    # Simple "1 year" check (e.g. "1 year of experience" or "1 year")
    m_simple = re.search(r"\b1\s*(?:year|yr)\b", clean_lower)
    if m_simple and not re.search(r"\b0\s*(?:[-–]|to)\s*1\b", clean_lower):
        return True, "Requires 1 year of experience"

    return False, ""



def is_early_career(title: str, jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role matches explicit Early Career / Intern / Junior / Apprentice patterns."""
    if title:
        norm_title = normalize_text(title)
        m = EARLY_CAREER_REGEX.search(title) or EARLY_CAREER_REGEX.search(norm_title)
        if m:
            return True, f"Early-career keyword: '{m.group(0)}'"

    return False, ""


def clean_location_input(loc) -> str:
    """Normalize any location representation (str, list, dict, None) into a clean string."""
    if not loc:
        return ""
    if isinstance(loc, str):
        return loc.strip()
    if isinstance(loc, (list, tuple, set)):
        return " ".join(clean_location_input(x) for x in loc if x).strip()
    if isinstance(loc, dict):
        return " ".join(clean_location_input(v) for v in loc.values() if v).strip()
    return str(loc).strip()


def is_us_based(title: str = "", location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is located in the USA."""
    clean_loc = clean_location_input(location)
    loc_combined = f"{title} {clean_loc}".strip()
    norm_combined = normalize_text(loc_combined)

    # 1. Non-US Override check: avoid false positives with state abbreviations (e.g. Bangalore, IN or Toronto, CA)
    has_non_us = bool(
        NON_US_REGEX.search(loc_combined) or 
        NON_US_REGEX.search(norm_combined) or 
        CANADA_CITIES_REGEX.search(loc_combined) or 
        CANADA_COUNTRY_REGEX.search(loc_combined)
    )
    has_us_city = bool(US_CITIES_REGEX.search(loc_combined) or US_CITIES_REGEX.search(norm_combined))

    # If it has an explicit Non-US location and no specific US city, it is NOT US
    if has_non_us and not has_us_city:
        return False, ""

    # 2. Check title explicitly for US country code (e.g. "2027 US AI Engineering", "(US)", "[US]", "US Remote")
    if title:
        m = re.search(r"(?:\b|_)(?:US|USA|U\.S\.A?)(?:\b|_)", title)
        if m:
            return True, f"US country code detected in title: '{m.group(0)}'"

    # 3. Check location string directly
    if clean_loc:
        if re.search(r"\b(?:US|USA|U\.S\.A?)\b", clean_loc, re.I):
            return True, f"US country code in location: '{clean_loc}'"

    # 4. Check combined title + location with standard patterns
    if loc_combined:
        m = US_COUNTRY_REGEX.search(loc_combined)
        if m:
            return True, f"US country identifier detected: '{m.group(0)}'"
        m = US_CITIES_REGEX.search(loc_combined)
        if m:
            return True, f"US tech city detected: '{m.group(0)}'"
        m = US_STATES_REGEX.search(loc_combined)
        if m:
            return True, f"US state detected: '{m.group(0)}'"
        
        # State abbreviations check only if not a known non-US region
        if not has_non_us:
            m = US_STATE_ABBR_REGEX.search(loc_combined)
            if m:
                return True, f"US state postal code detected: '{m.group(1)}'"

    # 5. Check full JD text header
    if jd_text:
        jd_header = jd_text[:800]
        m = US_COUNTRY_REGEX.search(jd_header)
        if m:
            return True, f"US country identifier in JD: '{m.group(0)}'"
        m = US_CITIES_REGEX.search(jd_header)
        if m:
            return True, f"US city in JD: '{m.group(0)}'"
        m = US_STATES_REGEX.search(jd_header)
        if m:
            return True, f"US state in JD: '{m.group(0)}'"

    return False, ""


def is_canada_based(title: str = "", location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is located in Canada."""
    clean_loc = clean_location_input(location)
    loc_combined = f"{title} {clean_loc}".strip()
    norm_combined = normalize_text(loc_combined)

    # 1. Non-Canada Override: e.g. London, UK or Melbourne, Australia
    has_other_country = bool(NON_US_REGEX.search(loc_combined) or NON_US_REGEX.search(norm_combined))
    has_can_city = bool(CANADA_CITIES_REGEX.search(loc_combined) or CANADA_CITIES_REGEX.search(norm_combined))
    has_can_country = bool(CANADA_COUNTRY_REGEX.search(loc_combined) or CANADA_COUNTRY_REGEX.search(norm_combined))

    # If it specifies another country and does NOT mention Canada or a Canadian city, do not filter
    if has_other_country and not has_can_city and not has_can_country:
        return False, ""

    # 2. Check title explicitly for Canada or Canadian tech hubs
    if title:
        m = CANADA_COUNTRY_REGEX.search(title)
        if m:
            return True, f"Canadian identifier detected in title: '{m.group(0)}'"
        m = CANADA_CITIES_REGEX.search(title)
        if m:
            return True, f"Canadian city in title: '{m.group(0)}'"
        m = CANADA_PROVINCES_REGEX.search(title)
        if m:
            return True, f"Canadian province in title: '{m.group(0)}'"

    # 3. Check location string directly
    if clean_loc:
        m = CANADA_COUNTRY_REGEX.search(clean_loc)
        if m:
            return True, f"Canadian country identifier in location: '{clean_loc}'"
        m = CANADA_CITIES_REGEX.search(clean_loc)
        if m:
            return True, f"Canadian city detected in location: '{m.group(0)}'"
        m = CANADA_PROVINCES_REGEX.search(clean_loc)
        if m:
            return True, f"Canadian province in location: '{m.group(0)}'"
        m = CANADA_VICTORIA_REGEX.search(clean_loc)
        if m:
            return True, f"Victoria, BC (Canada) in location: '{m.group(0)}'"
        m = CANADA_PROVINCE_ABBR_REGEX.search(clean_loc)
        if m:
            return True, f"Canadian province postal code in location: '{m.group(1)}'"

    # 4. Check full JD text header (first 800 chars)
    if jd_text:
        jd_header = jd_text[:800]
        m = CANADA_COUNTRY_REGEX.search(jd_header)
        if m:
            return True, f"Canadian identifier in JD: '{m.group(0)}'"
        m = CANADA_CITIES_REGEX.search(jd_header)
        if m:
            return True, f"Canadian city in JD: '{m.group(0)}'"
        m = CANADA_PROVINCES_REGEX.search(jd_header)
        if m:
            return True, f"Canadian province in JD: '{m.group(0)}'"

    return False, ""


def is_uk_based(title: str = "", location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is located in the United Kingdom."""
    clean_loc = clean_location_input(location)
    loc_combined = f"{title} {clean_loc}".strip()

    # 1. Non-UK Override: e.g. London, Ontario (Canada)
    if re.search(r"\bLondon,\s*(?:ON|Ontario|Canada)\b", loc_combined, re.I):
        return False, ""

    # 2. Check title explicitly for UK country code / country / city
    if title:
        m = re.search(r"(?:\b|_)(?:UK|U\.K\.)(?:\b|_)", title)
        if m:
            return True, f"UK country code detected in title: '{m.group(0)}'"
        m = UK_COUNTRY_REGEX.search(title)
        if m:
            return True, f"UK country identifier detected in title: '{m.group(0)}'"
        m = UK_CITIES_REGEX.search(title)
        if m:
            return True, f"UK city detected in title: '{m.group(0)}'"
        m = UK_SPECIAL_CITIES_REGEX.search(title)
        if m:
            return True, f"UK tech hub detected in title: '{m.group(0)}'"

    # 3. Check location string directly
    if clean_loc:
        m = re.search(r"(?:\b|_)(?:UK|U\.K\.)(?:\b|_)", clean_loc)
        if m:
            return True, f"UK country code in location: '{clean_loc}'"
        m = UK_COUNTRY_REGEX.search(clean_loc)
        if m:
            return True, f"UK country identifier in location: '{clean_loc}'"
        m = UK_CITIES_REGEX.search(clean_loc)
        if m:
            return True, f"UK city detected in location: '{m.group(0)}'"
        m = UK_SPECIAL_CITIES_REGEX.search(clean_loc)
        if m:
            return True, f"UK tech hub detected in location: '{m.group(0)}'"
        if re.search(r"\b(?:Oxford|Cambridge)\b", clean_loc, re.I) and not re.search(r"\b(?:MA|USA|United States)\b", clean_loc, re.I):
            return True, f"UK tech hub detected in location: '{clean_loc}'"

    # 4. Check combined title + location
    if loc_combined:
        m = UK_COUNTRY_REGEX.search(loc_combined)
        if m:
            return True, f"UK country identifier detected: '{m.group(0)}'"
        m = UK_CITIES_REGEX.search(loc_combined)
        if m:
            return True, f"UK city detected: '{m.group(0)}'"
        m = UK_SPECIAL_CITIES_REGEX.search(loc_combined)
        if m:
            return True, f"UK tech hub detected: '{m.group(0)}'"

    # 5. Check full JD text header (first 800 chars)
    if jd_text:
        jd_header = jd_text[:800]
        m = UK_COUNTRY_REGEX.search(jd_header)
        if m:
            return True, f"UK country identifier in JD: '{m.group(0)}'"
        m = UK_CITIES_REGEX.search(jd_header)
        if m:
            return True, f"UK city in JD: '{m.group(0)}'"
        m = UK_SPECIAL_CITIES_REGEX.search(jd_header)
        if m:
            return True, f"UK tech hub in JD: '{m.group(0)}'"

    return False, ""


def is_paris_based(title: str = "", location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """Returns (True, reason) if role is located in Paris or the Paris region (Île-de-France)."""
    clean_loc = clean_location_input(location)
    loc_combined = f"{title} {clean_loc}".strip()
    norm_combined = normalize_text(loc_combined)

    # Guard: Avoid Paris, TX or Paris, Texas (US city)
    if re.search(r"\bParis,\s*(?:TX|Texas)\b", loc_combined, re.I):
        return False, ""

    # 1. Check title
    if title:
        m = PARIS_REGEX.search(title) or PARIS_REGEX.search(normalize_text(title))
        if m:
            return True, f"Paris location detected in title: '{m.group(0)}'"

    # 2. Check location string directly
    if clean_loc:
        m = PARIS_REGEX.search(clean_loc) or PARIS_REGEX.search(normalize_text(clean_loc))
        if m:
            return True, f"Paris location detected: '{m.group(0)}'"

    # 3. Check combined title + location
    if loc_combined:
        m = PARIS_REGEX.search(loc_combined) or PARIS_REGEX.search(norm_combined)
        if m:
            return True, f"Paris location detected: '{m.group(0)}'"

    # 4. Check full JD text header (first 800 chars)
    if jd_text:
        jd_header = jd_text[:800]
        m = PARIS_REGEX.search(jd_header) or PARIS_REGEX.search(normalize_text(jd_header))
        if m:
            return True, f"Paris location in JD: '{m.group(0)}'"

    return False, ""


def categorize_job_tier(title: str, location: str = "", jd_text: str = "") -> dict:
    """
    Evaluates a job into one of 3 Smart Tiers:
      - Tier 3: Deterministic IGNORE (Senior, Non-Tech, US/Canada/UK/Paris Location, Career Hub).
                Rank: IGNORE, Status: NA. (0 AI tokens)
      - Tier 1: High-Priority Early Career (Intern, Junior, Apprentice, New Grad, Assoc SWE).
                Rank: HIGH, Status: New. (0 AI tokens - instant high priority in GUI)
      - Tier 2: General Software / Tech Role (Entry-level / general SWE).
                Rank: UNKNOWN, Status: New. (Ready for Groq LLM ranking)
    """
    clean_title = (title or "").strip()
    clean_loc = clean_location_input(location)

    # --- Tier 3 Checks (Deterministic Ignore) ---
    # 1. Senior / Lead / Manager / Supervisor / Pleno / 3+ yrs
    is_sr, sr_reason = is_senior_or_lead(clean_title, jd_text)
    if is_sr:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {sr_reason}",
            "is_early_career": False,
        }

    # 2. Non-tech role (Sales, HR, Marketing, Helpdesk, Assembly, Non-tech Intern)
    is_nt, nt_reason = is_non_tech(clean_title, jd_text)
    if is_nt:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {nt_reason}",
            "is_early_career": False,
        }

    # 3. US Location
    is_us, us_reason = is_us_based(title=clean_title, location=clean_loc, jd_text=jd_text)
    if is_us:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {us_reason}",
            "is_early_career": False,
        }

    # 4. Canada Location
    is_can, can_reason = is_canada_based(title=clean_title, location=clean_loc, jd_text=jd_text)
    if is_can:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {can_reason}",
            "is_early_career": False,
        }

    # 5. UK Location
    is_uk, uk_reason = is_uk_based(title=clean_title, location=clean_loc, jd_text=jd_text)
    if is_uk:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {uk_reason}",
            "is_early_career": False,
        }

    # 6. Paris Location
    is_paris, paris_reason = is_paris_based(title=clean_title, location=clean_loc, jd_text=jd_text)
    if is_paris:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {paris_reason}",
            "is_early_career": False,
        }

    # 7. Career Hub Landing Page (e.g. "Duolingo Careers")
    is_hub, hub_reason = is_career_hub(clean_title)
    if is_hub:
        return {
            "tier": 3,
            "rank": "IGNORE",
            "status": "NA",
            "reason": f"[Deterministic] {hub_reason}",
            "is_early_career": False,
        }

    # --- Tier 1 Check (Early Career & Internships) ---
    is_ec, ec_reason = is_early_career(clean_title, jd_text)
    if is_ec:
        return {
            "tier": 1,
            "rank": "UNKNOWN",
            "status": "New",
            "reason": f"[Tier 1: Early Career] {ec_reason}",
            "is_early_career": True,
        }

    # --- Tier 2 Check (General SWE / Tech role) ---
    return {
        "tier": 2,
        "rank": "UNKNOWN",
        "status": "New",
        "reason": "[Tier 2: Tech / SWE Role]",
        "is_early_career": False,
    }


def evaluate_deterministic_ignore(title: str, location: str = "", jd_text: str = "") -> tuple[bool, str]:
    """
    Master pre-filter: Returns (True, reason) if job should be deterministically IGNORED (Tier 3).
    Maintains 100% backward compatibility with all callers.
    """
    res = categorize_job_tier(title=title, location=location, jd_text=jd_text)
    if res["tier"] == 3:
        return True, res["reason"]
    return False, ""


# ──────────────────────────────────────────────
#  Database Pre-Filter Batch Runner
# ──────────────────────────────────────────────

def sync_ats_locations_to_db(conn=None) -> int:
    """
    Directly fetches locations from saved ATS companies and backfills jobs whose
    details field is missing or lacks location. Runs concurrently in seconds (0 AI tokens).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        from src.scrapers.ats_scraper import PLATFORM_FETCHERS
    except ImportError:
        from ats_scraper import PLATFORM_FETCHERS

    close_after = conn is None
    if conn is None:
        conn = get_conn()

    # Fast exit: if no active New jobs lack location, skip network scraping completely
    try:
        missing_loc = conn.execute(
            "SELECT 1 FROM jobs WHERE status = 'New' AND (details IS NULL OR details NOT LIKE '%location%') LIMIT 1"
        ).fetchone()
        if not missing_loc:
            if close_after:
                conn.close()
            return 0
    except Exception:
        pass

    companies = get_all_companies(conn)
    if not companies:
        if close_after:
            conn.close()
        return 0

    url_to_loc = {}
    def fetch_comp(c):
        p, i = c.get('ats_platform'), c.get('identifier')
        f = PLATFORM_FETCHERS.get(p)
        if not f:
            return []
        try:
            return f(i)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=25) as ex:
        futs = [ex.submit(fetch_comp, c) for c in companies]
        for fut in as_completed(futs):
            for j in fut.result():
                u, loc = j.get('url'), clean_location_input(j.get('location'))
                if u and loc:
                    url_to_loc[u] = loc

    updated = 0
    for u, loc in url_to_loc.items():
        cur = conn.execute(
            "UPDATE jobs SET details=? WHERE url=? AND (details IS NULL OR details NOT LIKE '%location%')",
            (json.dumps({'location': loc}), u)
        )
        updated += cur.rowcount

    conn.commit()
    if close_after:
        conn.close()
    return updated


def apply_deterministic_filter_to_db(conn=None, only_unknown: bool = False, move_to_na: bool = True, sync_locations: bool = False) -> dict:
    """
    Runs smart 3-tier deterministic pre-filtering over SQLite database in bulk.
    - Tier 3: Instantly marks jobs as rank='IGNORE', status='NA'.
    - Tier 1: Marks early career / internships as rank='HIGH', status='New' (Priority).
    - Tier 2: Keeps general tech / SWE roles as rank='UNKNOWN', status='New' (for Groq LLM).
    Returns stats dict.
    """
    import sqlite3
    close_after = conn is None
    if conn is None:
        conn = get_conn()
        init_db(conn)
    elif not conn.row_factory:
        conn.row_factory = sqlite3.Row

    # Sync missing location details from ATS platforms if needed
    if sync_locations:
        try:
            sync_ats_locations_to_db(conn)
        except Exception as e:
            print(f"Location sync note: {e}")

    # Move any existing IGNORE jobs that are still in 'New' to 'NA'
    cur = conn.execute("UPDATE jobs SET status='NA' WHERE rank='IGNORE' AND status='New'")
    already_ignored_moved = cur.rowcount

    if only_unknown:
        query = "SELECT url, title, details, reason, rank FROM jobs WHERE status = 'New' AND rank = 'UNKNOWN'"
    else:
        query = "SELECT url, title, details, reason, rank FROM jobs WHERE status = 'New'"
    rows = conn.execute(query).fetchall()

    stats = {
        "scanned": len(rows),
        "senior_ignored": 0,
        "non_tech_ignored": 0,
        "us_ignored": 0,
        "canada_ignored": 0,
        "uk_ignored": 0,
        "paris_ignored": 0,
        "career_hub_ignored": 0,
        "total_ignored": already_ignored_moved,
        "tier1_early_career": 0,
        "tier2_general_swe": 0,
        "passed": 0
    }

    status_target_ignore = "NA" if move_to_na else "New"

    for r in rows:
        if isinstance(r, (tuple, list)):
            url, title, det_raw = r[0], r[1] or "", r[2]
            reason_existing = r[3] or ""
        else:
            url = r["url"]
            title = r["title"] or ""
            det_raw = r["details"]
            reason_existing = r["reason"] or ""

        location = ""
        jd_snippet = ""
        if det_raw:
            try:
                det = json.loads(det_raw)
                location = clean_location_input(det.get("location"))
                jd_snippet = det.get("about_job") or ""
            except Exception:
                pass

        tier_res = categorize_job_tier(title=title, location=location, jd_text=jd_snippet)
        tier = tier_res["tier"]

        if tier == 3:
            conn.execute(
                "UPDATE jobs SET rank='IGNORE', status=?, reason=? WHERE url=?",
                (status_target_ignore, tier_res["reason"], url)
            )
            stats["total_ignored"] += 1
            if "Senior" in tier_res["reason"]:
                stats["senior_ignored"] += 1
            elif "Non-tech" in tier_res["reason"]:
                stats["non_tech_ignored"] += 1
            elif "US" in tier_res["reason"]:
                stats["us_ignored"] += 1
            elif "Canad" in tier_res["reason"] or "Victoria, BC" in tier_res["reason"]:
                stats["canada_ignored"] += 1
            elif "UK" in tier_res["reason"] or "United Kingdom" in tier_res["reason"]:
                stats["uk_ignored"] += 1
            elif "Paris" in tier_res["reason"]:
                stats["paris_ignored"] += 1
            elif "Career hub" in tier_res["reason"]:
                stats["career_hub_ignored"] += 1

        elif reason_existing.startswith("[AI Verified]") or reason_existing.startswith("[AI-Evaluated]") or reason_existing.startswith("AI:"):
            stats["passed"] += 1
            continue

        elif tier == 1:
            conn.execute(
                "UPDATE jobs SET rank='UNKNOWN', status='New', reason=? WHERE url=?",
                (tier_res["reason"], url)
            )
            stats["tier1_early_career"] += 1
            stats["passed"] += 1

        else:
            # Tier 2
            conn.execute(
                "UPDATE jobs SET rank='UNKNOWN', status='New', reason=? WHERE url=?",
                (tier_res["reason"], url)
            )
            stats["tier2_general_swe"] += 1
            stats["passed"] += 1

    conn.commit()
    if close_after:
        conn.close()

    return stats


def evaluate_deep_jd_filter(title: str, jd_text: str = "", details: dict = None) -> tuple[bool, str]:
    """
    Second-phase deterministic deep filter operating on JD text and structured details.
    Disqualifies:
      1. Experience requirement >= 1 year (from details['experience_required'] or JD text)
      2. Senior / Lead / Manager / Staff roles (from title or structured job_name)
      3. Non-tech roles (both title and detailed responsibilities/skills)
      4. US or Canada location (from details['location'] or JD body)
      5. Security clearance or US citizenship requirements
    Returns: (should_ignore: bool, reason: str)
    """
    clean_title = (title or "").strip()
    details = details or {}

    # 1. Experience check on details["experience_required"]
    exp_field = details.get("experience_required")
    if exp_field:
        is_exp, exp_reason = is_experience_exceeded(str(exp_field))
        if is_exp:
            return True, exp_reason

    # 2. Experience check on raw/clean JD text
    if jd_text:
        has_safeguard = bool(EXPERIENCE_ENTRY_SAFEGUARD_REGEX.search(jd_text))
        m_exp = EXPERIENCE_EXCEEDED_REGEX.search(jd_text)
        if m_exp and not (has_safeguard and "0" in m_exp.group(0)):
            matched_str = m_exp.group(0).lower()
            if not any(dur in matched_str for dur in ["contract", "duration", "program", "placement"]):
                return True, f"Requires experience >= 1 year in JD: '{m_exp.group(0)}'"

    # 3. Senior / Lead check on title or structured job_name
    is_sr, sr_reason = is_senior_or_lead(clean_title)
    if is_sr:
        return True, sr_reason
    if details.get("job_name"):
        is_sr_det, sr_det_reason = is_senior_or_lead(details["job_name"])
        if is_sr_det:
            return True, sr_det_reason

    # 4. Location check on structured location or JD
    loc_field = clean_location_input(details.get("location"))
    if loc_field:
        is_us, us_reason = is_us_based(title=clean_title, location=loc_field, jd_text=jd_text)
        if is_us:
            return True, us_reason
        is_can, can_reason = is_canada_based(title=clean_title, location=loc_field, jd_text=jd_text)
        if is_can:
            return True, can_reason
        is_uk, uk_reason = is_uk_based(title=clean_title, location=loc_field, jd_text=jd_text)
        if is_uk:
            return True, uk_reason
        is_paris, paris_reason = is_paris_based(title=clean_title, location=loc_field, jd_text=jd_text)
        if is_paris:
            return True, paris_reason
    elif jd_text:
        is_us, us_reason = is_us_based(title=clean_title, location="", jd_text=jd_text)
        if is_us:
            return True, us_reason
        is_can, can_reason = is_canada_based(title=clean_title, location="", jd_text=jd_text)
        if is_can:
            return True, can_reason
        is_uk, uk_reason = is_uk_based(title=clean_title, location="", jd_text=jd_text)
        if is_uk:
            return True, uk_reason
        is_paris, paris_reason = is_paris_based(title=clean_title, location="", jd_text=jd_text)
        if is_paris:
            return True, paris_reason

    # 5. Non-tech check on title
    is_nt, nt_reason = is_non_tech(clean_title)
    if is_nt:
        return True, nt_reason

    # 6. Non-tech check on structured fields (skills_required / responsibilities)
    skills = details.get("skills_required") or []
    skills_str = " ".join(str(s) for s in skills) if isinstance(skills, list) else str(skills)
    resps = details.get("responsibilities") or []
    resps_str = " ".join(str(r) for r in resps) if isinstance(resps, list) else str(resps)
    combined_det_text = f"{skills_str} {resps_str}".strip()

    if combined_det_text:
        hm = HARD_NON_TECH_REGEX.search(combined_det_text)
        if hm and not TECH_SAFEGUARD_REGEX.search(combined_det_text):
            return True, f"Non-tech responsibilities/skills: '{hm.group(0)}'"

    # 7. Security clearance / US citizenship in JD
    if jd_text:
        if re.search(r"\b(?:active\s+)?(?:secret|top\s+secret|ts/sci|polygraph)\s+(?:security\s+)?clearance\b", jd_text, re.I):
            return True, "Security clearance required in JD"
        if re.search(r"\bmust\s+be\s+a\s+(?:u\.?s\.?|united\s+states)\s+citizen\b", jd_text, re.I):
            return True, "US citizenship required in JD"

    return False, ""


def apply_deep_jd_filter_to_db(conn=None, move_to_na: bool = True) -> dict:
    """
    Applies the second-phase deterministic deep filter across jobs in SQLite database.
    Evaluates jobs in status='New' or rank='UNKNOWN' that have details or raw JD text.
    Moves disqualified jobs to rank='IGNORE', status='NA'.
    Returns stats dict.
    """
    import sqlite3
    close_after = conn is None
    if conn is None:
        conn = get_conn()
        init_db(conn)
    elif not conn.row_factory:
        conn.row_factory = sqlite3.Row

    query = "SELECT url, title, details, reason, rank FROM jobs WHERE status = 'New'"
    rows = conn.execute(query).fetchall()

    stats = {
        "scanned": len(rows),
        "exp_ignored": 0,
        "senior_ignored": 0,
        "non_tech_ignored": 0,
        "us_ignored": 0,
        "canada_ignored": 0,
        "uk_ignored": 0,
        "paris_ignored": 0,
        "clearance_ignored": 0,
        "total_ignored": 0,
        "passed": 0
    }

    status_target_ignore = "NA" if move_to_na else "New"

    for r in rows:
        if isinstance(r, (tuple, list)):
            url, title, det_raw = r[0], r[1] or "", r[2]
            reason_existing = r[3] or ""
        else:
            url = r["url"]
            title = r["title"] or ""
            det_raw = r["details"]
            reason_existing = r["reason"] or ""

        # NEVER overwrite or re-evaluate jobs that have already been evaluated by AI
        if reason_existing.startswith("[AI Verified]") or reason_existing.startswith("[AI-Evaluated]") or reason_existing.startswith("AI:"):
            stats["passed"] += 1
            continue

        det = None
        if det_raw:
            try:
                det = json.loads(det_raw)
            except Exception:
                det = None

        should_ignore, reason = evaluate_deep_jd_filter(title=title, jd_text="", details=det)
        if should_ignore:
            ignore_reason = f"[Deep JD Filter] {reason}"
            conn.execute(
                "UPDATE jobs SET rank='IGNORE', status=?, reason=? WHERE url=?",
                (status_target_ignore, ignore_reason, url)
            )
            stats["total_ignored"] += 1
            reason_lower = reason.lower()
            if "experience" in reason_lower:
                stats["exp_ignored"] += 1
            elif "senior" in reason_lower or "lead" in reason_lower or "principal" in reason_lower or "manager" in reason_lower:
                stats["senior_ignored"] += 1
            elif "non-tech" in reason_lower:
                stats["non_tech_ignored"] += 1
            elif "us" in reason_lower or "united states" in reason_lower:
                stats["us_ignored"] += 1
            elif "canada" in reason_lower or "canad" in reason_lower:
                stats["canada_ignored"] += 1
            elif "uk" in reason_lower or "united kingdom" in reason_lower:
                stats["uk_ignored"] += 1
            elif "paris" in reason_lower:
                stats["paris_ignored"] += 1
            elif "clearance" in reason_lower or "citizen" in reason_lower:
                stats["clearance_ignored"] += 1
        else:
            stats["passed"] += 1

    conn.commit()
    if close_after:
        conn.close()

    return stats


if __name__ == "__main__":
    print("="*60)
    print("⚡ RUNNING SMART TIER DETERMINISTIC FILTER (0 AI TOKENS)")
    print("="*60)
    res = apply_deterministic_filter_to_db(only_unknown=True)
    print(f"📊 Jobs scanned:              {res['scanned']}")
    print(f"   🚫 Senior / Supervisor:     {res['senior_ignored']}")
    print(f"   🚫 Non-tech / Helpdesk:     {res['non_tech_ignored']}")
    print(f"   🚫 US-based location:       {res['us_ignored']}")
    print(f"   🚫 Canada-based location:   {res['canada_ignored']}")
    print(f"   🚫 UK-based location:       {res.get('uk_ignored', 0)}")
    print(f"   🚫 Paris-based location:    {res.get('paris_ignored', 0)}")
    print(f"   🚫 Career hub landing pages:{res['career_hub_ignored']}")
    print(f"   ----------------------------------------")
    print(f"   🎯 Tier 3 (Ignored / NA):   {res['total_ignored']} ({res['total_ignored']/(res['scanned'] or 1)*100:.1f}%)")
    print(f"   ⭐ Tier 1 (High Priority):  {res['tier1_early_career']} (Early career / Intern)")
    print(f"   💻 Tier 2 (General SWE):    {res['tier2_general_swe']} (Ready for Groq LLM)")
    print(f"   ✅ Total Active Jobs:       {res['passed']}")
    print("="*60)
