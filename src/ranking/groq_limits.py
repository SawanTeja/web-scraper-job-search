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
    # 1. Numbered keys GROQ_API_KEY_1 .. 100
    for i in range(1, 101):
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


DAILY_TOKENS_PER_KEY = 200_000   # Groq Free Tier TPD limit for qwen/qwen3.8-27b and openai/gpt-oss-20b
TOKENS_PER_JOB = 1_800           # Typical tokens per candidate job (Extraction + LLM Evaluation)
MIN_TPM_BUFFER_JOB = 1_500       # Minimum TPM tokens in rolling 60s window to safely process a job


def check_single_key(idx_and_key, model="qwen/qwen3.8-27b", timeout=6.0):
    """
    Checks rate limits & token health for a single Groq key via a 1-token raw response.
    Returns structured stats dict evaluated strictly by TOKEN capacity (TPM & TPD).
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

        used_req = max(0, lim_req - rem_req)
        # Estimate daily tokens consumed (Groq free tier limit is 200k tokens/day)
        # Evaluated jobs average ~900 tokens per API call (Extraction ~800, Ranking ~1000)
        est_used_daily_tokens = min(DAILY_TOKENS_PER_KEY, used_req * 900)
        est_rem_daily_tokens = max(0, DAILY_TOKENS_PER_KEY - est_used_daily_tokens)
        daily_tok_pct = (est_rem_daily_tokens / DAILY_TOKENS_PER_KEY) * 100

        # Health is evaluated STRICTLY by token capacity (TPM & TPD), not request counts
        if est_rem_daily_tokens <= 0:
            status = "TPD_EXHAUSTED"
            status_label = "🚫 Daily Tokens Used"
        elif rem_tok == 0:
            status = "TPM_BUSY"
            status_label = f"🟠 TPM Empty ({rst_tok})"
        elif rem_tok < MIN_TPM_BUFFER_JOB:
            status = "TPM_LOW"
            status_label = f"🟡 TPM Low ({rem_tok:,}/8k)"
        elif rem_tok < 5000:
            status = "HEALTHY"
            status_label = f"🟢 Ready ({rem_tok:,}/8k TPM)"
        else:
            status = "HEALTHY"
            status_label = "🟢 Token Buffer Healthy"

        return {
            "index": idx,
            "masked_key": masked,
            "status": status,
            "status_label": status_label,
            "remaining_tokens": rem_tok,
            "limit_tokens": lim_tok,
            "est_remaining_daily_tokens": est_rem_daily_tokens,
            "est_used_daily_tokens": est_used_daily_tokens,
            "limit_daily_tokens": DAILY_TOKENS_PER_KEY,
            "daily_token_pct": daily_tok_pct,
            "remaining_requests": rem_req,
            "limit_requests": lim_req,
            "used_requests": used_req,
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
        err_lower = err_msg.lower()
        is_tpd = any(x in err_lower for x in ["tokens_per_day", "tokens per day", "tpd"])
        is_tpm = any(x in err_lower for x in ["tokens_per_minute", "tokens per minute", "tpm"])

        if is_tpd:
            status = "TPD_EXHAUSTED"
            status_label = "🚫 Daily Token Limit Reached"
            est_rem_daily = 0
            est_used_daily = DAILY_TOKENS_PER_KEY
        elif is_tpm:
            status = "TPM_BUSY"
            status_label = f"🟠 TPM Rate Limited ({rst_tok})"
            used_req = max(0, lim_req - rem_req)
            est_used_daily = min(DAILY_TOKENS_PER_KEY, used_req * 900)
            est_rem_daily = max(0, DAILY_TOKENS_PER_KEY - est_used_daily)
        else:
            status = "RATE_LIMITED"
            status_label = "🔴 Rate Limited"
            used_req = max(0, lim_req - rem_req)
            est_used_daily = min(DAILY_TOKENS_PER_KEY, used_req * 900)
            est_rem_daily = max(0, DAILY_TOKENS_PER_KEY - est_used_daily)

        return {
            "index": idx,
            "masked_key": masked,
            "status": status,
            "status_label": status_label,
            "remaining_tokens": rem_tok,
            "limit_tokens": lim_tok,
            "est_remaining_daily_tokens": est_rem_daily,
            "est_used_daily_tokens": est_used_daily,
            "limit_daily_tokens": DAILY_TOKENS_PER_KEY,
            "daily_token_pct": (est_rem_daily / DAILY_TOKENS_PER_KEY) * 100,
            "remaining_requests": rem_req,
            "limit_requests": lim_req,
            "used_requests": max(0, lim_req - rem_req),
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
            "remaining_tokens": 0,
            "limit_tokens": 8000,
            "est_remaining_daily_tokens": 0,
            "est_used_daily_tokens": 0,
            "limit_daily_tokens": DAILY_TOKENS_PER_KEY,
            "daily_token_pct": 0,
            "remaining_requests": 0,
            "limit_requests": 1000,
            "used_requests": 0,
            "reset_requests": "N/A",
            "reset_tokens": "N/A",
            "error": str(e)
        }


def check_all_groq_limits(model="qwen/qwen3.8-27b", max_workers=10):
    """
    Checks rate limits across all configured Groq keys concurrently.
    Evaluates health and capacity strictly by token limits (TPM & TPD).
    Returns a unified quota summary dictionary.
    """
    keys = get_all_groq_keys()
    if not keys:
        return {
            "total_keys": 0,
            "keys": [],
            "total_daily_tokens": 0,
            "total_remaining_tokens": 0,
            "total_used_tokens": 0,
            "token_based_capacity": 0,
            "fallback_tokens": 0,
            "fallback_capacity": 0,
            "combined_capacity": 0,
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

    tot_daily_tokens = len(keys) * DAILY_TOKENS_PER_KEY
    tot_rem_tokens = sum(r["est_remaining_daily_tokens"] for r in results)
    tot_used_tokens = sum(r["est_used_daily_tokens"] for r in results)

    # Real job capacity based STRICTLY on tokens, not requests!
    # (Each evaluated job takes ~1,800 tokens total for extraction + ranking)
    token_based_capacity = tot_rem_tokens // TOKENS_PER_JOB

    # Fallback model quota: openai/gpt-oss-20b provides fresh 200k tokens per key
    fallback_tokens = len(keys) * DAILY_TOKENS_PER_KEY
    fallback_capacity = fallback_tokens // TOKENS_PER_JOB
    combined_capacity = token_based_capacity + fallback_capacity

    # Earliest or representative reset time
    reset_times = [r["reset_requests"] for r in results if r["reset_requests"] != "N/A"]
    daily_reset = reset_times[0] if reset_times else "N/A"

    return {
        "total_keys": len(keys),
        "keys": results,
        "total_daily_tokens": tot_daily_tokens,
        "total_remaining_tokens": tot_rem_tokens,
        "total_used_tokens": tot_used_tokens,
        "token_based_capacity": token_based_capacity,
        "fallback_tokens": fallback_tokens,
        "fallback_capacity": fallback_capacity,
        "combined_capacity": combined_capacity,
        "total_remaining_requests": tot_rem_req,
        "total_limit_requests": tot_lim_req,
        "total_used_requests": tot_used_req,
        "estimated_jobs_today": token_based_capacity,  # Real job capacity based strictly on tokens!
        "daily_reset_in": daily_reset,
        "model": model,
        "error": None
    }


def run_limits_cli():
    """Prints a formatted report of Groq API token limits & health in the terminal."""
    print("=" * 80)
    print("🔑 CHECKING GROQ API TOKEN LIMITS & HEALTH (ALL CONFIGURED KEYS)")
    print("=" * 80)

    t0 = time.time()
    data = check_all_groq_limits()
    duration = time.time() - t0

    if data.get("error"):
        print(f"❌ {data['error']}")
        return

    rem_pct = (data['total_remaining_tokens'] / (data['total_daily_tokens'] or 1)) * 100
    used_pct = 100.0 - rem_pct

    print(f"⚡ Checked {data['total_keys']} keys in {duration:.2f}s (Primary Model: {data['model']})\n")
    print(f"📊 OVERALL DAILY TOKEN QUOTA (TPD - The Real Bottleneck):")
    print(f"   • Total Active Keys:          {data['total_keys']} keys configured")
    print(f"   • Total Daily Token Quota:    {data['total_daily_tokens']:,} tokens/day ({DAILY_TOKENS_PER_KEY:,}/key)")
    print(f"   • Daily Tokens Consumed:      ~{data['total_used_tokens']:,} tokens ({used_pct:.1f}% used today)")
    print(f"   • Daily Tokens Remaining:     ~{data['total_remaining_tokens']:,} tokens ({rem_pct:.1f}% remaining)")
    print(f"   • Real Job Capacity Today:    ~{data['token_based_capacity']:,} candidate jobs (at ~1,800 tokens/job)")
    print(f"   • Fallback Model Capacity:    +{data['fallback_capacity']:,} candidate jobs (openai/gpt-oss-20b: +{data['fallback_tokens']:,} tokens)")
    print(f"   • Total Available Capacity:   ~{data['combined_capacity']:,} candidate jobs across both models")
    print(f"   • Daily Reset In:             {data['daily_reset_in']} (at 00:00 UTC)")
    print("\n" + "-" * 80)
    print(f"{'Key #':<6} {'Masked Key':<15} {'TPM Buffer (60s)':<18} {'Est. Daily Tokens':<18} {'Token Health Status':<20}")
    print("-" * 80)

    for k in data["keys"]:
        tpm_str = f"{k['remaining_tokens']:,} / {k['limit_tokens']:,}"
        rem_k = k['est_remaining_daily_tokens'] // 1000
        lim_k = k['limit_daily_tokens'] // 1000
        tpd_str = f"{rem_k}k / {lim_k}k ({k['daily_token_pct']:.0f}%)"
        print(f"#{k['index']:<5} {k['masked_key']:<15} {tpm_str:<18} {tpd_str:<18} {k['status_label']}")

    print("-" * 80)
    print("💡 Notes:")
    print("   • Health is evaluated strictly by TOKEN capacity (TPM & TPD), not request counts.")
    print("   • Daily Token Limit (TPD): 200,000 tokens/day per key (the real bottleneck on Groq).")
    print("   • Minute Token Limit (TPM): 8,000 tokens/min rolling buffer. A candidate job takes ~1,800 tokens.")
    print("   • To expand capacity: Add GROQ_API_KEY_8, GROQ_API_KEY_9, etc. to .env (+200k tokens / key)")
    print("=" * 80)


if __name__ == "__main__":
    run_limits_cli()

