import os
import json
import datetime
from pathlib import Path

_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
USAGE_FILE = _ROOT_DIR / "groq_daily_usage.json"
DAILY_TOKENS_LIMIT = 200_000


def _get_utc_today_str() -> str:
    """Groq daily quotas reset at 00:00 UTC."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _load_data() -> dict:
    if not USAGE_FILE.exists():
        return {}
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_data(data: dict):
    try:
        temp_file = USAGE_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        temp_file.replace(USAGE_FILE)
    except Exception as e:
        print(f"⚠️ Failed to save groq usage: {e}")


def record_usage(api_key: str, tokens: int, model: str = "qwen/qwen3.8-27b"):
    """
    Records actual tokens consumed by a Groq API call.
    """
    if not api_key:
        return
    today = _get_utc_today_str()
    data = _load_data()

    # Clean old dates (keep last 7 days only)
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    data = {d: val for d, val in data.items() if d >= cutoff}

    if today not in data:
        data[today] = {}

    key_id = api_key[-16:] if len(api_key) >= 16 else api_key
    if key_id not in data[today]:
        data[today][key_id] = {
            "tokens_used": 0,
            "calls_count": 0,
            "daily_exhausted": False,
            "models": {}
        }

    entry = data[today][key_id]
    entry["tokens_used"] += max(0, tokens)
    entry["calls_count"] += 1
    entry["models"][model] = entry["models"].get(model, 0) + max(0, tokens)

    _save_data(data)


def mark_key_exhausted(api_key: str, model: str = "qwen/qwen3.8-27b"):
    """
    Marks a key as having exhausted its daily token quota (TPD 429).
    """
    if not api_key:
        return
    today = _get_utc_today_str()
    data = _load_data()
    if today not in data:
        data[today] = {}

    key_id = api_key[-16:] if len(api_key) >= 16 else api_key
    if key_id not in data[today]:
        data[today][key_id] = {
            "tokens_used": DAILY_TOKENS_LIMIT,
            "calls_count": 0,
            "daily_exhausted": True,
            "models": {}
        }
    else:
        entry = data[today][key_id]
        entry["daily_exhausted"] = True
        entry["tokens_used"] = max(entry["tokens_used"], DAILY_TOKENS_LIMIT)

    _save_data(data)


def get_key_daily_usage(api_key: str) -> dict:
    """
    Returns today's usage dict for a given key.
    """
    today = _get_utc_today_str()
    data = _load_data()
    today_data = data.get(today, {})
    key_id = api_key[-16:] if len(api_key) >= 16 else api_key

    info = today_data.get(key_id, {})
    return {
        "tokens_used": info.get("tokens_used", 0),
        "calls_count": info.get("calls_count", 0),
        "daily_exhausted": info.get("daily_exhausted", False)
    }
