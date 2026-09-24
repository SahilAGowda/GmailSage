"""
Handles Gmail OAuth. Uses the gmail.modify scope (labels + archive, never
delete); use triage.py --dry-run to preview before letting it change anything.

Setup:
1. Go to console.cloud.google.com -> create project
2. Enable "Gmail API"
3. OAuth consent screen -> External -> add yourself as test user
4. Credentials -> Create OAuth client ID -> Desktop app
5. Download JSON, save as credentials.json in this folder
6. Run this file once: python gmail_auth.py -> opens browser, you approve
   -> creates token.json (cached, auto-refreshes after that)
"""
import os
import sys
import time

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES_READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_MODIFY = [
    "https://www.googleapis.com/auth/gmail.modify",  # labels, archive — no delete, no send-as-you
]

TOKEN_PATH = "token.json"
CREDS_PATH = "credentials.json"


def _refresh_with_retry(creds, attempts=4):
    # Laptop cron often wakes before the network is up (DNS failures in the log).
    for i in range(attempts):
        try:
            creds.refresh(Request())
            return
        except TransportError:
            if i == attempts - 1:
                raise
            time.sleep(5 * 2 ** i)


def get_service(scopes=None):
    # Always request gmail.modify: it includes read access, and asking for a
    # narrower scope on refresh than the token was granted fails with invalid_scope.
    # `scopes` is accepted for backwards compatibility and ignored.
    scopes = SCOPES_MODIFY
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                _refresh_with_retry(creds)
            except RefreshError:
                creds = None  # revoked/expired refresh token -> need fresh consent
        if not creds or not creds.valid:
            if not os.path.exists(CREDS_PATH):
                raise FileNotFoundError(
                    f"{CREDS_PATH} not found. Download it from Google Cloud "
                    "Console (OAuth client, Desktop app type) and place it here."
                )
            if not sys.stdin.isatty():
                # Under cron nobody can click "Allow" — fail loudly instead of hanging.
                raise RuntimeError("Gmail token invalid and no terminal for consent. Run: python gmail_auth.py")
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, scopes)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    svc = get_service()
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Authenticated as {profile['emailAddress']}")
