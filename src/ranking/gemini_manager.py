"""
Gemini API Manager for Structured Job Description Extraction.

Provides multi-key rotation and multi-model fallback across Google Gemini models:
- Primary: gemini-3.5-flash-lite (500 RPD, 250k TPM)
- Secondary: gemini-3.1-flash-lite (500 RPD, 250k TPM)
- Tertiary: gemini-3.5-flash (20 RPD)

When Gemini keys are exhausted or unavailable, the caller automatically falls back
to Groq for JD extraction.
"""
import os
import re
import json
import asyncio
from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
    _GENAI_INSTALLED = True
except ImportError:
    _GENAI_INSTALLED = False


def get_all_gemini_keys():
    """Extracts all Gemini API keys configured in environment or .env file."""
    keys = []
    # 1. Numbered keys GEMINI_API_KEY_1 .. 100
    for i in range(1, 101):
        k = os.environ.get(f"GEMINI_API_KEY_{i}")
        if k and k.strip():
            clean = k.strip().strip("'\"")
            if clean and clean not in keys:
                keys.append(clean)

    # 2. Comma-separated GEMINI_API_KEYS
    if os.environ.get("GEMINI_API_KEYS"):
        for k in os.environ.get("GEMINI_API_KEYS").split(","):
            clean = k.strip().strip("'\"")
            if clean and clean not in keys:
                keys.append(clean)

    # 3. Standard fallback GEMINI_API_KEY
    if not keys and os.environ.get("GEMINI_API_KEY"):
        clean = os.environ.get("GEMINI_API_KEY").strip().strip("'\"")
        if clean and clean not in keys:
            keys.append(clean)

    return keys


class GeminiManager:
    GEMINI_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
    ]

    def __init__(self):
        self.keys = get_all_gemini_keys()
        self.total_keys = len(self.keys)
        self.clients = []
        self.current_key_idx = 0
        self.model_idx = 0
        self.active_model = self.GEMINI_MODELS[0]
        self.daily_exhausted_keys = set()
        self.all_exhausted = False

        if not _GENAI_INSTALLED or not self.keys:
            self.available = False
            if not self.keys:
                print("ℹ️ No GEMINI_API_KEY found in .env. JD Extraction will use Groq directly.")
            return

        self.available = True
        for k in self.keys:
            try:
                self.clients.append(genai.Client(api_key=k))
            except Exception as e:
                print(f"⚠️ Failed to init Gemini client for key ending in ...{k[-6:]}: {e}")

        if not self.clients:
            self.available = False
            return

        print(f"🌟 Loaded {len(self.clients)} Gemini API keys for JD Extraction (Primary: {self.active_model}).")

    def is_available(self) -> bool:
        return self.available and not self.all_exhausted and bool(self.clients)

    def get_client(self):
        if len(self.daily_exhausted_keys) < len(self.clients):
            attempts = 0
            while (self.current_key_idx + 1) in self.daily_exhausted_keys and attempts < len(self.clients):
                self.current_key_idx = (self.current_key_idx + 1) % len(self.clients)
                attempts += 1
        return self.clients[self.current_key_idx]

    def rotate_key(self, reason: str = ""):
        self.current_key_idx = (self.current_key_idx + 1) % len(self.clients)
        if len(self.daily_exhausted_keys) < len(self.clients):
            attempts = 0
            while (self.current_key_idx + 1) in self.daily_exhausted_keys and attempts < len(self.clients):
                self.current_key_idx = (self.current_key_idx + 1) % len(self.clients)
                attempts += 1
        msg = f"🔄 Swapped to Gemini API Key #{self.current_key_idx + 1}/{len(self.clients)}"
        if reason:
            msg += f" ({reason})"
        print(msg)

    def mark_current_key_exhausted(self):
        key_num = self.current_key_idx + 1
        self.daily_exhausted_keys.add(key_num)
        print(f"   ⚠️ Gemini Key #{key_num}/{len(self.clients)} exhausted on {self.active_model}.")

        if len(self.daily_exhausted_keys) >= len(self.clients):
            # All keys exhausted for current model -> try next fallback model
            if self.model_idx + 1 < len(self.GEMINI_MODELS):
                self.model_idx += 1
                self.active_model = self.GEMINI_MODELS[self.model_idx]
                self.daily_exhausted_keys.clear()
                self.current_key_idx = 0
                print(f"\n🔀 All Gemini keys exhausted on previous model! Switched to fallback: {self.active_model}")
            else:
                self.all_exhausted = True
                print("\n🛑 ALL Gemini API keys and models exhausted! Falling back to Groq for JD extraction.")
        else:
            self.rotate_key("daily-limit-rotate")

    async def extract_job_details(self, job_title: str, jd_text: str) -> dict | None:
        if not self.is_available():
            return None

        prompt = f"""You are a technical recruiter. Extract structured job details from this Job Description (JD).
Title: {job_title}

Description:
{jd_text}

Respond ONLY with a valid JSON object matching EXACTLY this schema:
{{
  "job_name": null,
  "company": null,
  "location": null,
  "salary": null,
  "job_type": null,
  "experience_required": null,
  "skills_required": null,
  "skills_preferred": null,
  "about_job": null,
  "responsibilities": null,
  "requirements": null,
  "nice_to_have": null
}}

Guidelines:
- Return ONLY the JSON object.
- Use arrays of short strings for skills_required, skills_preferred, responsibilities, requirements, and nice_to_have.
- If a field is not found or not mentioned, set its value to null.
"""

        max_attempts = len(self.clients) * len(self.GEMINI_MODELS) + 2
        for _ in range(max_attempts):
            if self.all_exhausted:
                return None

            client = self.get_client()
            key_num = self.current_key_idx + 1
            target_model = self.active_model

            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0
                )
                res = await client.aio.models.generate_content(
                    model=target_model,
                    contents=prompt,
                    config=config
                )

                if res and res.text:
                    raw_text = res.text.strip()
                    match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        if isinstance(data, dict):
                            print(f"   ✨ Extracted structured JD via Gemini #{key_num} ({target_model}) [0 Groq tokens used]")
                            return data
                return None

            except Exception as e:
                err_str = str(e).lower()
                if any(k in err_str for k in ["429", "resource_exhausted", "quota", "rate limit", "limit exceeded"]):
                    self.mark_current_key_exhausted()
                    await asyncio.sleep(0.5)
                    continue
                else:
                    print(f"   ⚠️ Gemini extraction error on Key #{key_num} ({e}). Rotating...")
                    self.rotate_key("api-error")
                    await asyncio.sleep(1.0)
                    continue

        return None
