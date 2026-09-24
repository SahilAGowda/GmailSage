"""
Run once a day (separate cron from triage.py, e.g. 8am). Pulls everything
marked 'high' (plus actionable 'medium', e.g. security/payment notices) in
the last 24h and sends you one summary email so you never have to hunt
through labels manually. Also lists the senders archived most this week —
candidates to unsubscribe from.
"""
import base64
import html as html_lib
import os
from collections import defaultdict
from email.mime.text import MIMEText

from dotenv import load_dotenv

from gmail_actions import RETRIES
from gmail_auth import get_service
from store import get_digest_items, get_stats, get_top_archived_senders, init_db

load_dotenv()
try:
    DIGEST_TO = os.environ["DIGEST_TO_EMAIL"]
except KeyError:
    raise SystemExit(
        "DIGEST_TO_EMAIL is not set. Add it to your .env (the address the daily digest is sent to)."
    )
DIGEST_HOURS = int(os.environ.get("DIGEST_HOURS", "24"))


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def build_digest_body(service, rows, html: bool = False):
    # Group by priority then category; subject comes from the DB, sender from Gmail.
    grouped = defaultdict(list)
    for message_id, category, priority, subject in rows:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata",
                 metadataHeaders=["From", "Subject"])
            .execute(num_retries=RETRIES)
        )
        headers = msg["payload"]["headers"]
        sender = get_header(headers, "From")
        subject = subject or get_header(headers, "Subject")
        # #all/ works whether or not the message is still in the inbox
        link = f"https://mail.google.com/mail/u/0/#all/{message_id}"
        grouped[(priority, category)].append((subject, sender, link))

    stats, total = get_stats(hours=DIGEST_HOURS)
    top_senders = get_top_archived_senders(days=7)
    counts = defaultdict(int)
    for c, p, _source, n in stats:
        counts[f"{c}/{p}"] += n
    stats_line = f"{DIGEST_HOURS}h: {total} processed. " + ", ".join(f"{k}: {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))

    if not html:
        lines = [] if rows else [f"No priority mail in the last {DIGEST_HOURS}h."]
        for (priority, cat), items in grouped.items():
            lines.append(f"\n== {priority.upper()} · {cat.upper()} ({len(items)}) ==")
            for subject, sender, link in items:
                lines.append(f"[{cat}] {subject} — {sender}")
                lines.append(f"  {link}")
        if top_senders:
            lines.append("\n== Most archived senders (7d) — consider unsubscribing ==")
            lines += [f"  {n:3d}  {d}" for d, n in top_senders]
        lines.append(f"\n---\n{stats_line}")
        return "\n".join(lines)

    esc = html_lib.escape
    html_parts = ["<h2>Daily Priority Digest</h2>"]
    if not rows:
        html_parts.append(f"<p>No priority mail in the last {DIGEST_HOURS}h.</p>")
    for (priority, cat), items in grouped.items():
        html_parts.append(f"<h3>{esc(priority.title())} · {esc(cat.title())} ({len(items)})</h3><ul>")
        for subject, sender, link in items:
            html_parts.append(f'<li><a href="{link}">{esc(subject)}</a> — {esc(sender)}</li>')
        html_parts.append("</ul>")
    if top_senders:
        html_parts.append("<h3>Most archived senders (7d)</h3><p><small>Consider unsubscribing:</small></p><ul>")
        html_parts += [f"<li>{esc(d)} — {n}</li>" for d, n in top_senders]
        html_parts.append("</ul>")
    html_parts.append(f"<hr><p><small>{esc(stats_line)}</small></p>")
    html_parts.append('<p><small><a href="https://mail.google.com/mail/u/0/#search/label%3AGmailSage">View all GmailSage labels</a></small></p>')
    return "\n".join(html_parts)


def send_digest(service, body: str, html: bool = False):
    subtype = "html" if html else "plain"
    message = MIMEText(body, subtype)
    message["to"] = DIGEST_TO
    message["subject"] = "Daily Priority Digest"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute(num_retries=RETRIES)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", action="store_true", help="send HTML digest")
    parser.add_argument("--dry-run", action="store_true", help="print digest without sending")
    args = parser.parse_args()

    init_db()
    service = get_service()
    rows = get_digest_items(hours=DIGEST_HOURS)
    body = build_digest_body(service, rows, html=args.html)
    if args.dry_run:
        print(body)
    else:
        send_digest(service, body, html=args.html)
        print(f"Digest sent with {len(rows)} item(s) to {DIGEST_TO} ({'HTML' if args.html else 'text'}).")
