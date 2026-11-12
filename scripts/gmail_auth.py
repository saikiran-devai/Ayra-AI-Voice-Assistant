"""Connect Ayra to Gmail. Run once, from the Ayra folder, with: python3 scripts/gmail_auth.py

A browser window opens for you to sign in. Ayra asks to read your email and to send email, and only sends
after you agree to each draft out loud. It can't delete or change existing email. The access is saved to
gmail_token.json: keep that file private, and delete it to disconnect Ayra.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # So this script can find Ayra's code when run directly

import os

from google_auth_oauthlib.flow import InstalledAppFlow

from agent.tools import GMAIL_CLIENT_FILE, GMAIL_SCOPES, GMAIL_TOKEN_FILE


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CLIENT_FILE, GMAIL_SCOPES)
    credentials = flow.run_local_server(port=0)
    GMAIL_TOKEN_FILE.write_text(credentials.to_json())
    os.chmod(GMAIL_TOKEN_FILE, 0o600)  # Only your user account can read it
    print(f"✅ Gmail connected. Access saved to {GMAIL_TOKEN_FILE.name}")


if __name__ == "__main__":
    main()
