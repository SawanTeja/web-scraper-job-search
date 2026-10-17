"""
Groq API Rate Limit & Quota Checker.

Queries live quota headers across all configured Groq API keys in parallel (<0.5s)
using 1-token health queries and parses:
- x-ratelimit-remaining-requests (RPD - Requests per day)
- x-ratelimit-limit-requests
- x-ratelimit-remaining-tokens (TPM - Tokens per minute)
- x-ratelimit-limit-tokens
- x-ratelimit-reset-requests (Time until daily reset)
- x-ratelimit-reset-tokens (Time until rolling minute window clears)
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

# Ensure root directory is in sys.path
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

load_dotenv(os.path.join(_ROOT_DIR, ".env"))


def get_all_groq_keys():
    """Extracts all Groq API keys configured in environment or .env file."""
    keys = []
    # 1. Numbered keys GROQ_API_KEY_1 .. 25
    for i in range(1, 26):
        k = os.environ.get(f"GROQ_API_KEY_{i}")
        if k and k.strip():
            clean = k.strip().strip("'\"")
            if clean and clean not in keys:
                keys.append(clean)

    # 2. Comma-separated GROQ_API_KEYS
    if os.environ.get("GROQ_API_KEYS"):
        for k in os.environ.get("GROQ_API_KEYS").split(","):
            clean = k.strip().strip("'\"")
            if clean and clean not in keys:
                keys.append(clean)

    # 3. Standard fallback GROQ_API_KEY
    if not keys and os.environ.get("GROQ_API_KEY"):
        clean = os.environ.get("GROQ_API_KEY").strip().strip("'\"")
        if clean and clean not in keys:
            keys.append(clean)

    return keys


def check_single_key(idx_and_key, model="qwen/qwen3.8-27b", timeout=6.0):
    """
    Checks rate limits for a single Groq key via a 1-token raw response.
    Returns structured stats dict.
    """
    from groq import Groq
    import groq

    idx, key = idx_and_key
    masked = f"{key[:7]}...{key[-4:]}" if len(key) >= 12 else key[:4] + "..."

    try:
        client = Groq(api_key=key, timeout=timeout)
        resp = client.chat.completions.with_raw_response.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1
        )
        headers = resp.headers

        rem_req = int(headers.get("x-ratelimit-remaining-requests", 0))
        lim_req = int(headers.get("x-ratelimit-limit-requests", 1000))
        rem_tok = int(headers.get("x-ratelimit-remaining-tokens", 0))
        lim_tok = int(headers.get("x-ratelimit-limit-tokens", 8000))
        rst_req = headers.get("x-ratelimit-reset-requests", "N/A")
        rst_tok = headers.get("x-ratelimit-reset-tokens", "N/A")

        return {
            "index": idx,
            "masked_key": masked,
            "status": "HEALTHY",
            "status_label": "🟢 Healthy",
            "remaining_requests": rem_req,
            "limit_requests": lim_req,
            "used_requests": max(0, lim_req - rem_req),
            "remaining_tokens": rem_tok,
            "limit_tokens": lim_tok,
            "reset_requests": rst_req,
            "reset_tokens": rst_tok,
            "error": None
        }

    except groq.RateLimitError as e:
        h = getattr(getattr(e, "response", None), "headers", {})
        rem_req = int(h.get("x-ratelimit-remaining-requests", 0)) if "x-ratelimit-remaining-requests" in h else 0
        lim_req = int(h.get("x-ratelimit-limit-requests", 1000)) if "x-ratelimit-limit-requests" in h else 1000
        rem_tok = int(h.get("x-ratelimit-remaining-tokens", 0)) if "x-ratelimit-remaining-tokens" in h else 0
        lim_tok = int(h.get("x-ratelimit-limit-tokens", 8000)) if "x-ratelimit-limit-tokens" in h else 8000
        rst_req = h.get("x-ratelimit-reset-requests", "N/A")
        rst_tok = h.get("x-ratelimit-reset-tokens", "N/A")

        err_msg = str(e)
        is_tpm = "tokens_per_minute" in err_msg.lower() or "tpm" in err_msg.lower()

        return {
            "index": idx,
            "masked_key": masked,
            "status": "RATE_LIMITED",
            "status_label": "🟡 TPM Busy" if is_tpm else "🔴 Rate Limited",
            "remaining_requests": rem_req,
            "limit_requests": lim_req,
            "used_requests": max(0, lim_req - rem_req),
            "remaining_tokens": rem_tok,
            "limit_tokens": lim_tok,
            "reset_requests": rst_req,
            "reset_tokens": rst_tok,
            "error": err_msg
        }

    except Exception as e:
        return {
            "index": idx,
            "masked_key": masked,
            "status": "ERROR",
            "status_label": "🔴 Error",
            "remaining_requests": 0,
            "limit_requests": 1000,
            "used_requests": 0,
            "remaining_tokens": 0,
            "limit_tokens": 8000,
            "reset_requests": "N/A",
            "reset_tokens": "N/A",
            "error": str(e)
        }


def check_all_groq_limits(model="qwen/qwen3.8-27b", max_workers=10):
    """
    Checks rate limits across all configured Groq keys concurrently.
    Returns a unified quota summary dictionary.
    """
    keys = get_all_groq_keys()
    if not keys:
        return {
            "total_keys": 0,
            "keys": [],
            "total_remaining_requests": 0,
            "total_limit_requests": 0,
            "total_used_requests": 0,
            "estimated_jobs_today": 0,
            "daily_reset_in": "N/A",
            "model": model,
            "error": "No Groq API keys found in .env!"
        }

    indexed_keys = list(enumerate(keys, 1))
    workers = min(len(keys), max_workers)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(lambda ik: check_single_key(ik, model=model), indexed_keys))

    results.sort(key=lambda r: r["index"])

    tot_rem_req = sum(r["remaining_requests"] for r in results)
    tot_lim_req = sum(r["limit_requests"] for r in results)
    tot_used_req = sum(r["used_requests"] for r in results)

    # Earliest or representative reset time
    reset_times = [r["reset_requests"] for r in results if r["reset_requests"] != "N/A"]
    daily_reset = reset_times[0] if reset_times else "N/A"

    # Each job requires 2 requests: 1 for extraction + 1 for ranking
    estimated_jobs = tot_rem_req // 2

    return {
        "total_keys": len(keys),
        "keys": results,
        "total_remaining_requests": tot_rem_req,
        "total_limit_requests": tot_lim_req,
        "total_used_requests": tot_used_req,
        "estimated_jobs_today": estimated_jobs,
        "daily_reset_in": daily_reset,
        "model": model,
        "error": None
    }


def run_limits_cli():
    """Prints a formatted report of Groq API limits in the terminal."""
    print("=" * 72)
    print("🔑 CHECKING GROQ API LIMITS & QUOTA (ALL CONFIGURED KEYS)")
    print("=" * 72)

    t0 = time.time()
    data = check_all_groq_limits()
    duration = time.time() - t0

    if data.get("error"):
        print(f"❌ {data['error']}")
        return

    print(f"⚡ Checked {data['total_keys']} keys in {duration:.2f}s (Model: {data['model']})\n")
    print(f"📊 OVERALL DAILY QUOTA:")
    print(f"   • Total Active Keys:          {data['total_keys']}")
    print(f"   • Daily Requests Remaining:   {data['total_remaining_requests']:,} / {data['total_limit_requests']:,} "
          f"({(data['total_remaining_requests'] / (data['total_limit_requests'] or 1))*100:.1f}% remaining)")
    print(f"   • Daily Requests Used Today:  {data['total_used_requests']:,}")
    print(f"   • Capacity for Today:         ~{data['estimated_jobs_today']:,} candidate jobs (at 2 reqs/job)")
    print(f"   • Daily Reset In:             {data['daily_reset_in']} (at 00:00 UTC)")
    print("\n" + "-" * 72)
    print(f"{'Key #':<6} {'Masked Key':<16} {'Daily Requests':<18} {'TPM Buffer':<16} {'Status':<12}")
    print("-" * 72)

    for k in data["keys"]:
        req_str = f"{k['remaining_requests']}/{k['limit_requests']}"
        tok_str = f"{k['remaining_tokens']}/{k['limit_tokens']}"
        print(f"#{k['index']:<5} {k['masked_key']:<16} {req_str:<18} {tok_str:<16} {k['status_label']}")

    print("-" * 72)
    print("💡 Notes:")
    print("   • RPD (Requests/Day): Resets once daily at 00:00 UTC (1,000 req/key/day).")
    print("   • TPM (Tokens/Minute): Rolling 60s window (8,000 tokens/key/min). Automatically recovers in seconds.")
    print("   • To expand capacity: Add GROQ_API_KEY_6, GROQ_API_KEY_7, etc. to .env")
    print("=" * 72)


if __name__ == "__main__":
    run_limits_cli()
