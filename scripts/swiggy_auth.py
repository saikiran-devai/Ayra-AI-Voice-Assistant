"""Connect Ayra to your Swiggy account. Run once, from the Ayra folder, with: python3 scripts/swiggy_auth.py

A browser window opens for you to sign in to Swiggy with your phone number and OTP. Ayra uses this only
to read your orders and track deliveries: it can't place, change or pay for anything. The access is saved
to swiggy_token.json and renews itself. Keep that file private, and delete it to disconnect Ayra.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # So this script can find Ayra's code when run directly

import base64
import hashlib
import http.server
import os
import secrets
import threading
import urllib.parse
import webbrowser

import httpx

from agent.tools import SWIGGY_AUTH_URL, save_swiggy_token

WAIT_SECONDS = 300
# Swiggy only accepts sign-in addresses it has approved, so this must stay fixed and match the access application.
# On a server, set SWIGGY_REDIRECT_URI to the approved HTTPS address instead.
DEFAULT_REDIRECT_URI = "http://localhost:47821/callback"


def main() -> None:
    received = {}
    done = threading.Event()

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if urllib.parse.urlparse(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Done. You can close this tab and return to the terminal.</p>".encode())
            done.set()

        def log_message(self, *args):
            pass  # Keep the code out of the terminal

    redirect_uri = os.environ.get("SWIGGY_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    port = urllib.parse.urlparse(redirect_uri).port or 47821
    try:
        server = http.server.HTTPServer(("127.0.0.1", port), Callback)
    except OSError:
        raise SystemExit(f"Port {port} is in use, so the Swiggy sign-in can't receive its reply. Close whatever is using it and try again.")
    threading.Thread(target=server.serve_forever, daemon=True).start()

    registration = httpx.post(f"{SWIGGY_AUTH_URL}/register", timeout=15, json={
        "client_name": "Ayra voice assistant",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    })
    if registration.status_code not in (200, 201):
        server.shutdown()
        raise SystemExit(f"Swiggy didn't accept the sign-in setup (HTTP {registration.status_code}). Please try again later.")
    client_id = registration.json()["client_id"]

    # PKCE: proves the code that comes back was requested by this script
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    url = f"{SWIGGY_AUTH_URL}/authorize?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp:tools",
    })
    print("Opening Swiggy sign-in in your browser. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)

    if not done.wait(WAIT_SECONDS):
        server.shutdown()
        raise SystemExit("Timed out waiting for the Swiggy sign-in. Please run this again.")
    server.shutdown()

    if received.get("state") != state:
        raise SystemExit("The sign-in response didn't match this request, so it was ignored. Please run this again.")
    if "code" not in received:
        raise SystemExit(f"Swiggy sign-in didn't complete: {received.get('error', 'no code returned')}.")

    response = httpx.post(f"{SWIGGY_AUTH_URL}/token", timeout=15, json={
        "grant_type": "authorization_code",
        "code": received["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    })
    if response.status_code != 200:
        raise SystemExit(f"Swiggy didn't accept the sign-in (HTTP {response.status_code}). Please run this again.")
    save_swiggy_token(response.json(), client_id)
    print("✅ Swiggy connected. Access saved to swiggy_token.json")


if __name__ == "__main__":
    main()
