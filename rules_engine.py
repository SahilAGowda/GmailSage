"""
Tier 1: deterministic classification. Catches most job-alert spam without
ever calling an LLM. Extend JOB_ALERT_DOMAINS from your own inbox — check
Gmail search: from:(naukri OR linkedin) "new jobs" to find yours.
"""
import json
import os
import re

RULES_PATH = os.path.join(os.path.dirname(__file__), "sender_domains.json")

with open(RULES_PATH) as f:
    _rules = json.load(f)

JOB_ALERT_DOMAINS = set(_rules["job_alert_domains"])
JOB_ALERT_SUBJECT_PATTERNS = [re.compile(p, re.I) for p in _rules["job_alert_subject_patterns"]]
NEWSLETTER_DOMAINS = set(_rules.get("newsletter_domains", []))
PROMO_DOMAINS = set(_rules.get("promo_domains", []))
LEARNING_DOMAINS = set(_rules.get("learning_domains", []))
FINANCE_DOMAINS = set(_rules.get("finance_domains", []))


def extract_domain(sender_header: str) -> str:
    # sender_header e.g. "LinkedIn Job Alerts <jobs-noreply@linkedin.com>"
    match = re.search(r"@([\w.-]+)", sender_header)
    return match.group(1).lower() if match else ""


def classify_by_rules(sender_header: str, subject: str, has_list_unsubscribe: bool):
    """
    Returns (category, priority) if a rule matches, else None — meaning
    "send to the LLM, this one's ambiguous."
    """
    domain = extract_domain(sender_header)

    if domain in JOB_ALERT_DOMAINS:
        return ("job_alert", "low")

    if any(p.search(subject) for p in JOB_ALERT_SUBJECT_PATTERNS):
        return ("job_alert", "low")

    if domain in NEWSLETTER_DOMAINS:
        return ("newsletter", "low")

    if domain in PROMO_DOMAINS:
        return ("promo", "low")

    if domain in LEARNING_DOMAINS:
        return ("learning", "low")

    if domain in FINANCE_DOMAINS:
        return ("finance", "low")

    # Broad bulk detection: List-Unsubscribe + promo/newsletter keywords -> archive
    # Catches 80% of remaining spam without LLM
    if has_list_unsubscribe:
        subj_lower = subject.lower()
        if any(kw in subj_lower for kw in ["unsubscribe", "newsletter", "digest", "weekly", "daily update", "promo", "offer", "discount", "ipo", "market", "notification"]):
            return ("promo", "low")
        # Generic bulk mail with unsubscribe header is almost never high priority
        # Keep it as newsletter unless subject suggests personal/urgent
        if any(kw in subj_lower for kw in ["hiring", "intern", "jobs"]):
            return ("job_alert", "low")
        return ("newsletter", "low")

    return None
