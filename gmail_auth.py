"""
Handles Gmail OAuth. Read-only to start; add modify scope only once
you trust the classifier's decisions.

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
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Start read-only. Switch to gmail.modify once the rules/labels are verified.
SCOPES_READONLY = ["https://www.googleapis.com/auth/gmail.readonly"]
SCOPES_MODIFY = [
    "https://www.googleapis.com/auth/gmail.modify",  # labels, archive — no delete, no send-as-you
]

TOKEN_PATH = "token.json"
CREDS_PATH = "credentials.json"


def get_service(scopes=SCOPES_READONLY):
    if scopes is None:
        scopes = SCOPES_READONLY
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, scopes)

    # Force re-auth if stored token doesn't have the required scopes
    if creds and creds.scopes is not None and set(creds.scopes) != set(scopes):
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_PATH):
                raise FileNotFoundError(
                    f"{CREDS_PATH} not found. Download it from Google Cloud "
                    "Console (OAuth client, Desktop app type) and place it here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, scopes)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


if __name__ == "__main__":
    svc = get_service()
    profile = svc.users().getProfile(userId="me").execute()
    print(f"Authenticated as {profile['emailAddress']}")
