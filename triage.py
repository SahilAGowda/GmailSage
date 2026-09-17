"""
Run this daily (or via cron/EventBridge). Fetches recent inbox mail,
runs rules first, falls back to LLM only when rules don't decide,
then labels/archives accordingly.

Dry run first: python triage.py --dry-run
Undo last run: python triage.py --undo --hours 24
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime

from dotenv import load_dotenv

from classifier import classify_by_llm
from gmail_actions import apply_label_and_route, undo_route
from gmail_auth import SCOPES_MODIFY, get_service
from rules_engine import classify_by_rules, extract_domain
from store import already_processed, get_recent_processed, get_stats, get_undo_candidates, init_db, mark_processed, mark_undone

load_dotenv()

DEFAULT_QUERY = os.environ.get("TRIAGE_QUERY", "in:inbox newer_than:2d")
DEFAULT_MAX = int(os.environ.get("TRIAGE_MAX_RESULTS", "50"))
LOG_PATH = os.environ.get("TRIAGE_LOG_PATH", "triage.log.jsonl")


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
        .execute()
    )
    return resp.get("messages", [])


def log_event(event: dict):
    event["ts"] = datetime.utcnow().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")


def run(dry_run: bool, query: str = DEFAULT_QUERY, max_results: int = DEFAULT_MAX):
    init_db()
    service = get_service(scopes=SCOPES_MODIFY if not dry_run else None)

    messages = fetch_recent_messages(service, max_results=max_results, query=query)
    print(f"Found {len(messages)} messages to check (query: {query}).")

    stats = Counter()
    processed_now = 0
    skipped = 0

    for msg_ref in messages:
        msg_id = msg_ref["id"]
        if already_processed(msg_id):
            skipped += 1
            continue

        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata",
                 metadataHeaders=["From", "Subject", "List-Unsubscribe"])
            .execute()
        )
        headers = msg["payload"]["headers"]
        sender = get_header(headers, "From")
        subject = get_header(headers, "Subject")
        has_unsub = bool(get_header(headers, "List-Unsubscribe"))
        snippet = msg.get("snippet", "")
        domain = extract_domain(sender)

        rule_result = classify_by_rules(sender, subject, has_unsub)
        if rule_result:
            category, priority = rule_result
            source = "rules"
        else:
            llm_result = classify_by_llm(sender, subject, snippet)
            category, priority = llm_result["category"], llm_result["priority"]
            source = "llm"

        print(f"[{source}] {subject[:60]!r} -> {category}/{priority} ({domain})")
        stats[f"{category}/{priority} ({source})"] += 1
        log_event({"event": "classify", "msg_id": msg_id, "subject": subject[:80], "domain": domain, "category": category, "priority": priority, "source": source, "dry_run": dry_run})

        if not dry_run:
            apply_label_and_route(service, msg_id, category, priority)
            mark_processed(msg_id, category, priority, source, subject, domain)
            processed_now += 1

    # Summary
    print(f"\n--- Summary: {processed_now} processed, {skipped} skipped (already done) ---")
    for k, v in stats.most_common():
        print(f"  {v:2d} {k}")
    if not dry_run and processed_now:
        rows, total = get_stats(hours=24)
        print(f"  24h total in DB: {total}")
    log_event({"event": "run_summary", "found": len(messages), "processed": processed_now, "skipped": skipped, "stats": dict(stats), "dry_run": dry_run})


def run_undo(hours: int, dry_run: bool = False):
    init_db()
    service = get_service(scopes=SCOPES_MODIFY)
    candidates = get_undo_candidates(hours=hours)
    print(f"Found {len(candidates)} messages to undo (last {hours}h).")
    for msg_id, category in candidates:
        print(f"  undo {msg_id} ({category}) -> restore INBOX")
        if not dry_run:
            undo_route(service, msg_id, category)
            mark_undone(msg_id)
    if dry_run:
        print("(dry-run: no changes made)")
    else:
        print(f"Undone {len(candidates)} messages.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="classify only, no labels/archiving, no DB writes")
    parser.add_argument("--undo", action="store_true", help="reverse last run: restore INBOX and remove Triage labels")
    parser.add_argument("--hours", type=int, default=24, help="hours window for --undo (default 24)")
    parser.add_argument("--query", type=str, default=DEFAULT_QUERY, help="Gmail query (default from TRIAGE_QUERY env)")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX, help="max messages per run")
    args = parser.parse_args()
    if args.undo:
        run_undo(hours=args.hours, dry_run=args.dry_run)
    else:
        run(dry_run=args.dry_run, query=args.query, max_results=args.max_results)
