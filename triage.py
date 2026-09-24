"""
Run this daily (or via cron/EventBridge). Fetches recent inbox mail,
runs rules first, falls back to LLM only when rules don't decide,
then labels/archives accordingly.

Dry run first: python triage.py --dry-run
Undo last run: python triage.py --undo --hours 24
Undo one sender: python triage.py --undo --hours 168 --sender hirist.tech
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.auth.exceptions import TransportError

from classifier import LLMUnavailable, classify_by_llm
from gmail_actions import RETRIES, apply_label_and_route, ids_back_in_inbox, undo_route
from gmail_auth import get_service
from rules_engine import apply_guard, classify_by_rules, extract_address, extract_domain, is_keep_address
from store import (add_keep_sender, already_processed, contact_get, contact_put, get_archived_since,
                   get_stats, get_undo_candidates, init_db, is_keep_sender, mark_processed, mark_undone)

load_dotenv()

DEFAULT_QUERY = os.environ.get("TRIAGE_QUERY", "in:inbox newer_than:2d")
DEFAULT_MAX = int(os.environ.get("TRIAGE_MAX_RESULTS", "50"))
LOG_PATH = os.environ.get("TRIAGE_LOG_PATH", "triage.log.jsonl")
METADATA_HEADERS = ["From", "Subject", "List-Unsubscribe", "Precedence", "Auto-Submitted"]


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def fetch_recent_messages(service, max_results=DEFAULT_MAX, query=DEFAULT_QUERY):
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute(num_retries=RETRIES)
    )
    return resp.get("messages", [])


def log_event(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")


def is_known_contact(service, address: str) -> bool:
    """True if you've ever sent mail to this address. Cached for 30 days."""
    if not address:
        return False
    known = contact_get(address)
    if known is None:
        resp = service.users().messages().list(
            userId="me", q=f"in:sent to:{address}", maxResults=1
        ).execute(num_retries=RETRIES)
        known = bool(resp.get("messages"))
        contact_put(address, known)
    return known


def learn_from_rescues(service, dry_run: bool):
    """
    Archived mail you moved back to the inbox = a wrong call. Remember the
    sender so their mail is never archived again.
    """
    archived = get_archived_since(days=14)
    by_category = {}
    for msg_id, category, address in archived:
        by_category.setdefault(category, []).append((msg_id, address))
    rescued = 0
    for category, items in by_category.items():
        back = ids_back_in_inbox(service, category)
        for msg_id, address in items:
            if msg_id in back and address and not is_keep_sender(address):
                print(f"[learn] you rescued mail from {address} ({category}) -> will keep it in inbox from now on")
                if not dry_run:
                    add_keep_sender(address, f"rescued from {category}")
                    log_event({"event": "learn_keep_sender", "address": address, "category": category})
                rescued += 1
    return rescued


def classify_message(service, msg):
    headers = msg["payload"]["headers"]
    sender = get_header(headers, "From")
    subject = get_header(headers, "Subject")
    has_unsub = bool(get_header(headers, "List-Unsubscribe"))
    is_bulk = (get_header(headers, "Precedence").lower() in {"bulk", "list", "junk"}
               or get_header(headers, "Auto-Submitted").lower() not in {"", "no"})
    snippet = msg.get("snippet", "")
    address = extract_address(sender)

    # Real contacts count as "personal"; keep-listed senders only block archiving.
    contact = is_known_contact(service, address)
    known = contact or is_keep_address(address) or is_keep_sender(address)

    rule_result = classify_by_rules(sender, subject, has_unsub, is_bulk=is_bulk,
                                    gmail_labels=msg.get("labelIds", []), known_contact=contact)
    if rule_result:
        category, priority = rule_result
        source = "rules"
    else:
        llm_result = classify_by_llm(sender, subject, snippet, has_unsub=has_unsub)
        category, priority = llm_result["category"], llm_result["priority"]
        source = "llm"

    guarded = apply_guard(category, priority, subject, known_contact=known)
    if guarded != (category, priority):
        source += "+guard"
        category, priority = guarded
    return sender, subject, address, category, priority, source


def run(dry_run: bool, query: str = DEFAULT_QUERY, max_results: int = DEFAULT_MAX):
    init_db()
    try:
        service = get_service()
    except TransportError as e:
        print(f"Network unavailable, skipping this run: {e}", file=sys.stderr)
        log_event({"event": "error", "error": f"network: {e}"})
        sys.exit(1)

    try:
        learn_from_rescues(service, dry_run)
    except Exception as e:  # learning is best-effort; never block triage
        print(f"[learn] skipped: {e}", file=sys.stderr)

    messages = fetch_recent_messages(service, max_results=max_results, query=query)
    print(f"Found {len(messages)} messages to check (query: {query}).")

    stats = Counter()
    processed_now = 0
    skipped = 0
    failed = 0

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        if already_processed(msg_id):
            skipped += 1
            continue

        # One bad message (network blip, LLM down) must not kill the whole run.
        # Failed messages aren't marked processed, so the next run retries them.
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="metadata", metadataHeaders=METADATA_HEADERS)
                .execute(num_retries=RETRIES)
            )
            sender, subject, address, category, priority, source = classify_message(service, msg)
            domain = extract_domain(sender)

            print(f"[{source}] {subject[:60]!r} -> {category}/{priority} ({domain})")
            stats[f"{category}/{priority} ({source})"] += 1
            log_event({"event": "classify", "msg_id": msg_id, "subject": subject[:80], "domain": domain, "category": category, "priority": priority, "source": source, "dry_run": dry_run})

            if not dry_run:
                apply_label_and_route(service, msg_id, category, priority)
                mark_processed(msg_id, category, priority, source, subject, domain, address)
                processed_now += 1
        except LLMUnavailable as e:
            failed += 1
            print(f"[skip] {msg_id}: LLM unavailable, will retry next run ({e})", file=sys.stderr)
            log_event({"event": "error", "msg_id": msg_id, "error": f"llm_unavailable: {e}"})
        except Exception as e:
            failed += 1
            print(f"[skip] {msg_id}: {type(e).__name__}: {e}", file=sys.stderr)
            log_event({"event": "error", "msg_id": msg_id, "error": f"{type(e).__name__}: {e}"})

    # Summary
    print(f"\n--- Summary: {processed_now} processed, {skipped} skipped (already done), {failed} failed ---")
    for k, v in stats.most_common():
        print(f"  {v:2d} {k}")
    if not dry_run and processed_now:
        rows, total = get_stats(hours=24)
        print(f"  24h total in DB: {total}")
    log_event({"event": "run_summary", "found": len(messages), "processed": processed_now, "skipped": skipped, "failed": failed, "stats": dict(stats), "dry_run": dry_run})


def run_undo(hours: int, dry_run: bool = False, sender: str = ""):
    init_db()
    service = get_service()
    candidates = get_undo_candidates(hours=hours, sender=sender)
    scope = f" from {sender}" if sender else ""
    print(f"Found {len(candidates)} archived messages{scope} to undo (last {hours}h).")
    for msg_id, category in candidates:
        print(f"  undo {msg_id} ({category}) -> restore INBOX")
        if not dry_run:
            undo_route(service, msg_id, category)
            mark_undone(msg_id, category)
    if dry_run:
        print("(dry-run: no changes made)")
    else:
        print(f"Undone {len(candidates)} messages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="classify only, no labels/archiving, no DB writes")
    parser.add_argument("--undo", action="store_true", help="reverse archiving: restore INBOX and remove GmailSage labels")
    parser.add_argument("--hours", type=int, default=24, help="hours window for --undo (default 24)")
    parser.add_argument("--sender", type=str, default="", help="with --undo: only this sender domain or address")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY, help="Gmail query (default from TRIAGE_QUERY env)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX, help="max messages per run")
    args = parser.parse_args()
    if args.undo:
        run_undo(hours=args.hours, dry_run=args.dry_run, sender=args.sender)
    else:
        run(dry_run=args.dry_run, query=args.query, max_results=args.max_results)
