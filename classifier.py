"""
Tier 2: only hit for mail rules_engine couldn't decide. Groq's free tier
is fast/cheap enough to run per-email; no need to batch for personal volume.
Adds simple hash-cache + confidence guard.
"""
import hashlib
import json
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

_client = Groq(api_key=os.environ["GROQ_API_KEY"])
_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-20b")
_CACHE = {}
_LOW_CONFIDENCE_FALLBACK = ("other", "medium")

SYSTEM_PROMPT = """You classify a personal Gmail inbox. Return ONLY valid JSON,
no markdown, no preamble.

Categories: interview, offer, deadline, personal, job_alert, newsletter, promo, learning, finance, social, updates, other
Priority: high, medium, low

Guidance:
- interview/offer/deadline (e.g. interview invite, offer letter, document submission, joining formalities, expiry alerts for tokens/certs) -> high
- personal (from a named individual, not automated, no List-Unsubscribe) -> high or medium depending on urgency
- job_alert (bulk "new jobs matching your profile" from job portals like hirist, naukri, linkedin jobs, indeed) -> low (archive)
- newsletter (regular digests, weekly/daily updates with List-Unsubscribe) -> low (archive)
- learning (courses, NPTEL, Udemy, JS Mastery, Skool, Unstop competitions) -> low (archive)
- finance (market updates, IPO, Groww digest, stock news) -> low (archive) unless urgent payment/action required
- promo (marketing, offers, product launches, Apple, FontAwesome) -> low (archive)
- social (Facebook notifications, community digests) -> low (archive)
- updates (system/security alerts, Google account notices, GitHub notifications) -> medium (keep but not urgent)
- other -> medium unless clearly bulk then low

Key signals:
- Has List-Unsubscribe header => almost always bulk => low priority (job_alert/newsletter/promo/learning)
- Subject contains "hiring", "is hiring", "jobs", "openings" => job_alert
- Subject contains "newsletter", "digest", "weekly", "daily update" => newsletter
- From nptel, udemy, jsmastery, skool, unstop => learning
- From groww, nse, ipo => finance

Output schema: {"category": "...", "priority": "...", "reason": "<one short phrase>"}
"""


def _cache_key(sender: str, subject: str, snippet: str) -> str:
    return hashlib.md5(f"{sender}|{subject}|{snippet[:100]}".encode()).hexdigest()


def classify_by_llm(sender: str, subject: str, snippet: str) -> dict:
    key = _cache_key(sender, subject, snippet)
    if key in _CACHE:
        return _CACHE[key]

    resp = _client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"From: {sender}\nSubject: {subject}\nSnippet: {snippet}",
            },
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = resp.choices[0].message.content
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"category": "other", "priority": "medium", "reason": "parse_failed"}

    result.setdefault("category", "other")
    result.setdefault("priority", "medium")
    # Confidence guard: unknown categories fall back to medium/other (stays in inbox)
    valid_cats = {"interview", "offer", "deadline", "personal", "job_alert", "newsletter", "promo", "learning", "finance", "social", "updates", "other"}
    if result["category"] not in valid_cats:
        result["category"], result["priority"] = _LOW_CONFIDENCE_FALLBACK
    if result["priority"] not in {"high", "medium", "low"}:
        result["priority"] = "medium"

    _CACHE[key] = result
    return result
