"""
Run once a day (separate cron from triage.py, e.g. 8am). Pulls everything
marked 'high' priority in the last 24h and sends you one summary email
so you never have to hunt through labels manually.
"""
import base64
import os
from collections import defaultdict
from email.mime.text import MIMEText

from dotenv import load_dotenv

from gmail_auth import SCOPES_MODIFY, get_service
from store import get_high_priority_since, get_recent_processed, get_stats, init_db

load_dotenv()
DIGEST_TO = os.environ["DIGEST_TO_EMAIL"]
DIGEST_HOURS = int(os.environ.get("DIGEST_HOURS", "24"))


def get_header(headers, name):
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def build_digest_body(service, rows, html: bool = False):
    if not rows:
        return ("<p>No high-priority mail in the last 24h.</p>" if html else "No high-priority mail in the last 24h.")

    # Group by category
    grouped = defaultdict(list)
    for message_id, category, priority in rows:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata",
                 metadataHeaders=["From", "Subject"])
            .execute()
        )
        headers = msg["payload"]["headers"]
        sender = get_header(headers, "From")
        subject = get_header(headers, "Subject")
        link = f"https://mail.google.com/mail/u/0/#inbox/{message_id}"
        grouped[category].append((subject, sender, link))

    if not html:
        lines = []
        for cat, items in grouped.items():
            lines.append(f"\n== {cat.upper()} ({len(items)}) ==")
            for subject, sender, link in items:
                lines.append(f"[{cat}] {subject} — {sender}")
                lines.append(f"  {link}")
        # Add stats footer
        init_db()
        stats, total = get_stats(hours=DIGEST_HOURS)
        lines.append(f"\n---\n24h stats: {total} processed. " + ", ".join(f"{c}:{n}" for c, _, _, n in stats))
        return "\n".join(lines)

    # HTML
    html_parts = ["<h2>Daily Priority Digest</h2>"]
    for cat, items in grouped.items():
        html_parts.append(f"<h3>{cat.title()} ({len(items)})</h3><ul>")
        for subject, sender, link in items:
            html_parts.append(f'<li><a href="{link}">{subject}</a> — {sender} <em>[{cat}]</em></li>')
        html_parts.append("</ul>")
    init_db()
    stats, total = get_stats(hours=DIGEST_HOURS)
    html_parts.append(f"<hr><p><small>24h: {total} processed. " + ", ".join(f"{c}: {n}" for c, _, _, n in stats) + "</small></p>")
    html_parts.append('<p><small><a href="https://mail.google.com/mail/u/0/#search/label%3AGmailSage">View all GmailSage labels</a></small></p>')
    return "\n".join(html_parts)


def send_digest(service, body: str, html: bool = False):
    subtype = "html" if html else "plain"
    message = MIMEText(body, subtype)
    message["to"] = DIGEST_TO
    message["subject"] = "Daily Priority Digest"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", action="store_true", help="send HTML digest")
    parser.add_argument("--dry-run", action="store_true", help="print digest without sending")
    args = parser.parse_args()

    service = get_service(scopes=SCOPES_MODIFY)
    rows = get_high_priority_since(hours=DIGEST_HOURS)
    body = build_digest_body(service, rows, html=args.html)
    if args.dry_run:
        print(body)
    else:
        send_digest(service, body, html=args.html)
        print(f"Digest sent with {len(rows)} high-priority item(s) to {DIGEST_TO} ({'HTML' if args.html else 'text'}).")
