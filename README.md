# gmailsage

Personal inbox triage: rules-first, LLM fallback (Groq), auto-label + archive job-alert spam, daily digest for what matters.

## Setup

1. **Google Cloud OAuth**
   - console.cloud.google.com → new project → enable "Gmail API"
   - OAuth consent screen → External → add your own email as a test user
   - Credentials → Create OAuth client ID → Desktop app → download JSON
   - Save it as `credentials.json` in this folder

2. **Install deps**
   ```
   pip install -r requirements.txt
   ```

3. **Groq API key** — free at console.groq.com → copy `.env.example` to `.env`, fill in `GROQ_API_KEY`.

4. **First auth + sanity check**
   ```
   python gmail_auth.py
   ```
   Opens a browser, you approve, creates `token.json` (auto-refreshes after).

## Usage

**Always dry-run first** to see what it *would* do, before it touches labels:
```
python triage.py --dry-run
```

Check the output. If `job_alert` classifications look right, tune `rules/sender_domains.json`
with any senders that slipped to the LLM tier (cheaper + more predictable than the LLM).

Once you trust it:
```
python triage.py
```

Set up a daily cron (or AWS EventBridge → Lambda, since that's your stack) to run
`triage.py` every few hours and `digest.py` once a morning.

## Safety notes

- Nothing is ever deleted. "Archiving" = removing the `INBOX` label — mail still exists
  in All Mail, fully searchable, fully reversible.
- Starts on `gmail.readonly` scope; `triage.py` (non-dry-run) needs `gmail.modify` —
  re-auth will prompt for the extra permission the first time.
- `triage.db` (SQLite) tracks processed message IDs so re-runs never reclassify
  or double-label the same mail.

## Extending

- Add more job-portal domains to `rules/sender_domains.json` as you spot them —
  every domain you add there is one less LLM call.
- Categories/labels live in `gmail_actions.py::CATEGORY_LABELS` — add new ones there.
