"""
Tier 1: deterministic classification. Catches most job-alert spam without
ever calling an LLM. Extend JOB_ALERT_DOMAINS from your own inbox — check
Gmail search: from:(naukri OR linkedin) "new jobs" to find yours.

Domains match on suffix: "linkedin.com" also covers "em.linkedin.com".
"""
import json
import os
import re

RULES_PATH = os.path.join(os.path.dirname(__file__), "sender_domains.json")

with open(RULES_PATH) as f:
    _rules = json.load(f)


def _patterns(key):
    return [re.compile(p, re.I) for p in _rules.get(key, [])]


JOB_ALERT_DOMAINS = set(_rules["job_alert_domains"])
# Never auto-archived (e.g. LinkedIn InMail + security mail); see is_keep_address.
KEEP_SENDERS = set(_rules.get("keep_senders", []))
SENDER_RULES = [
    (set(_rules.get("job_alert_senders", [])), "job_alert"),
    (set(_rules.get("social_senders", [])), "social"),
    (set(_rules.get("newsletter_senders", [])), "newsletter"),
]
JOB_ALERT_SUBJECT_PATTERNS = _patterns("job_alert_subject_patterns")
APPLICATION_SUBJECT_PATTERNS = _patterns("application_subject_patterns")
PROTECTED_SUBJECT_PATTERNS = _patterns("protected_subject_patterns")
NEWSLETTER_DOMAINS = set(_rules.get("newsletter_domains", []))
PROMO_DOMAINS = set(_rules.get("promo_domains", []))
SOCIAL_DOMAINS = set(_rules.get("social_domains", []))
LEARNING_DOMAINS = set(_rules.get("learning_domains", []))
FINANCE_DOMAINS = set(_rules.get("finance_domains", []))

# Checked in this order; the more specific categories win over "newsletter".
DOMAIN_RULES = [
    (JOB_ALERT_DOMAINS, "job_alert"),
    (LEARNING_DOMAINS, "learning"),
    (FINANCE_DOMAINS, "finance"),
    (SOCIAL_DOMAINS, "social"),
    (PROMO_DOMAINS, "promo"),
    (NEWSLETTER_DOMAINS, "newsletter"),
]

BULK_PROMO_KEYWORDS = ["unsubscribe", "newsletter", "digest", "weekly", "daily update", "promo", "sale", "discount", "% off", "ipo", "market"]


def extract_domain(sender_header: str) -> str:
    # sender_header e.g. "LinkedIn Job Alerts <jobs-noreply@linkedin.com>"
    match = re.search(r"@([\w.-]+)", sender_header)
    return match.group(1).lower().rstrip(".>") if match else ""


def extract_address(sender_header: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+", sender_header)
    return match.group(0).lower() if match else ""


def domain_in(domain: str, domains: set) -> bool:
    """True if domain equals, or is a subdomain of, any entry in domains."""
    parts = domain.split(".")
    return any(".".join(parts[i:]) in domains for i in range(len(parts) - 1))


def is_keep_address(address: str) -> bool:
    return address.lower() in KEEP_SENDERS


def is_protected(subject: str) -> bool:
    """Security / payment / account mail that must never be auto-archived."""
    return any(p.search(subject) for p in PROTECTED_SUBJECT_PATTERNS)


def classify_by_rules(sender_header: str, subject: str, has_list_unsubscribe: bool,
                      is_bulk: bool = False, gmail_labels=(), known_contact: bool = False):
    """
    Returns (category, priority) if a rule matches, else None — meaning
    "send to the LLM, this one's ambiguous."

    is_bulk: Precedence: bulk/list or Auto-Submitted header present.
    gmail_labels: Gmail's own labelIds (CATEGORY_PROMOTIONS etc.) — free signal.
    known_contact: you have sent mail to this address before.
    """
    domain = extract_domain(sender_header)
    bulk = has_list_unsubscribe or is_bulk

    # Job-hunt mail you actually care about beats every bulk rule.
    if any(p.search(subject) for p in APPLICATION_SUBJECT_PATTERNS):
        return ("application", "high")

    # Someone you've written to, sending 1:1 mail — a human conversation.
    if known_contact and not bulk:
        return ("personal", "high")

    address = extract_address(sender_header)
    for addresses, category in SENDER_RULES:
        if address in addresses:
            return (category, "low")

    for domains, category in DOMAIN_RULES:
        if domain_in(domain, domains):
            return (category, "low")

    # "X is hiring" from a real recruiter (no bulk headers) goes to the LLM instead.
    if bulk and any(p.search(subject) for p in JOB_ALERT_SUBJECT_PATTERNS):
        return ("job_alert", "low")

    if "CATEGORY_SOCIAL" in gmail_labels:
        return ("social", "low")
    if "CATEGORY_PROMOTIONS" in gmail_labels:
        return ("promo", "low")

    # Broad bulk detection: List-Unsubscribe header -> archive, unless the subject
    # looks like security/payment/account mail (those go to the LLM).
    if has_list_unsubscribe and not is_protected(subject):
        subj_lower = subject.lower()
        if any(kw in subj_lower for kw in BULK_PROMO_KEYWORDS):
            return ("promo", "low")
        if any(kw in subj_lower for kw in ["hiring", "intern", "jobs"]):
            return ("job_alert", "low")
        return ("newsletter", "low")

    return None


def apply_guard(category: str, priority: str, subject: str, known_contact: bool = False):
    """
    Final safety net over rules AND LLM output: protected subjects and known
    contacts are never archived. Missing one important mail costs far more
    than leaving a promo in the inbox.
    """
    if priority == "low" and (is_protected(subject) or known_contact):
        return ("updates" if is_protected(subject) else category), "medium"
    return category, priority
