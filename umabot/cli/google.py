"""CLI commands for Google Workspace integration.

Commands:
  umabot google setup    — import credentials from client_secret.json or prompt
  umabot google login    — open browser OAuth flow, store token locally
  umabot google status   — show auth state
  umabot google logout   — revoke and delete stored token
"""

from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("umabot.cli.google")

# Fixed callback port — must match the redirect URI registered in Google Cloud Console:
#   http://127.0.0.1:8765/callback
_OAUTH_CALLBACK_PORT = 8765


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

def cmd_setup(args) -> None:
    """Import credentials and save to config."""
    from umabot.config import load_config
    from umabot.config.loader import save_config

    cfg, config_path = load_config(config_path=getattr(args, "config", None))

    client_id = ""
    client_secret = ""

    # Option 1: parse a downloaded client_secret.json
    json_file = getattr(args, "credentials", None)
    if json_file:
        client_id, client_secret = _parse_client_secret_json(json_file)
        if not client_id:
            print(f"❌  Could not parse client_id from {json_file}")
            return
        print(f"✓  Parsed credentials from {json_file}")
    else:
        # Option 2: interactive prompts
        print("\n─── Google Workspace Setup ───────────────────────────────────────")
        print("You need a Google Cloud OAuth 2.0 credential.")
        print("Steps:")
        print("  1. Open https://console.cloud.google.com/apis/credentials")
        print("  2. Create Credentials → OAuth 2.0 Client ID")
        print("  3. Application type: Web application")
        print(f"  4. Add redirect URI: http://127.0.0.1:{_OAUTH_CALLBACK_PORT}/callback")
        print("  5. Download the JSON file  OR  copy the values below\n")

        json_path = input("Path to client_secret.json (or press Enter to type manually): ").strip()
        if json_path:
            client_id, client_secret = _parse_client_secret_json(json_path)
            if not client_id:
                print(f"❌  Could not parse {json_path}")
                return
        else:
            import getpass
            client_id = input("Client ID: ").strip()
            client_secret = getpass.getpass("Client Secret (hidden): ").strip()

    if not client_id or not client_secret:
        print("❌  client_id and client_secret are required.")
        return

    cfg.integrations.google.client_id = client_id
    cfg.integrations.google.client_secret = client_secret
    save_config(cfg, config_path)
    print(f"✓  Credentials saved to {config_path}")
    print("\nNext step — run:  umabot google login")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def run_oauth_login(client_id: str, client_secret: str, db) -> bool:
    """Run the browser OAuth flow and store the token in *db*.

    Returns True on success, False on failure.  Prints status to stdout so it
    works both from ``cmd_login`` and from the onboarding wizard.
    """
    from umabot.tools.google.auth import build_auth_url, exchange_code

    redirect_uri = f"http://127.0.0.1:{_OAUTH_CALLBACK_PORT}/callback"
    auth_url, state = build_auth_url(client_id, redirect_uri)

    print(f"\n─── Google Login ─────────────────────────────────────────────────")
    print("Opening browser for Google login...")
    print(f"If the browser does not open, visit:\n  {auth_url}\n")

    result: dict = {}
    try:
        server = _CallbackServer(("127.0.0.1", _OAUTH_CALLBACK_PORT), result)
    except OSError:
        print(f"❌  Port {_OAUTH_CALLBACK_PORT} is already in use. Kill any process using it and retry.")
        return False

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    webbrowser.open(auth_url)

    print("Waiting for Google to redirect back... (Ctrl+C to cancel)\n")
    thread.join(timeout=120)
    server.server_close()

    if result.get("error"):
        print(f"❌  Google returned an error: {result['error']}")
        return False

    code = result.get("code", "")
    if not code:
        print("❌  No authorization code received. Did you complete the login?")
        return False

    try:
        exchange_code(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            code=code,
            state=result.get("state", ""),
            db=db,
        )
    except Exception as exc:
        print(f"❌  Token exchange failed: {exc}")
        return False

    print("✅  Google authorised successfully!")
    print("    Gmail, Calendar, and Tasks tools are now available.\n")
    return True


def cmd_login(args) -> None:
    """Open browser OAuth flow and store token."""
    from umabot.config import load_config
    from umabot.storage import Database

    cfg, _ = load_config(config_path=getattr(args, "config", None))

    client_id = cfg.integrations.google.client_id or cfg.google.client_id
    client_secret = cfg.integrations.google.client_secret or cfg.google.client_secret

    if not client_id or not client_secret:
        print("❌  Google credentials not configured. Run:  umabot google setup")
        return

    db = Database(cfg.storage.db_path)
    try:
        run_oauth_login(client_id, client_secret, db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    """Show current Google auth state."""
    from umabot.config import load_config
    from umabot.storage import Database
    from umabot.tools.google.auth import is_authorized, get_credentials

    cfg, _ = load_config(config_path=getattr(args, "config", None))

    client_id = cfg.integrations.google.client_id or cfg.google.client_id
    client_secret = cfg.integrations.google.client_secret or cfg.google.client_secret

    print("\n─── Google Status ────────────────────────────────────────────────")
    if not client_id or not client_secret:
        print("  Credentials : ❌  not configured  (run: umabot google setup)")
        return

    print(f"  Client ID   : {client_id[:20]}...")
    print(f"  Secret      : {'*' * 8}")

    db = Database(cfg.storage.db_path)
    try:
        if not is_authorized(db):
            print("  Token       : ❌  not stored  (run: umabot google login)")
        else:
            creds = get_credentials(client_id, client_secret, db)
            if creds and creds.valid:
                print("  Token       : ✅  valid")
            elif creds and creds.expired:
                print("  Token       : ⚠️   expired (will auto-refresh on next use)")
            else:
                print("  Token       : ❌  invalid — run: umabot google login")
    except ImportError:
        print("  Token       : stored (google-auth-oauthlib not installed for validation)")
    finally:
        db.close()

    print()


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------

def cmd_logout(args) -> None:
    """Revoke and delete stored Google token."""
    from umabot.config import load_config
    from umabot.storage import Database

    cfg, _ = load_config(config_path=getattr(args, "config", None))
    db = Database(cfg.storage.db_path)
    try:
        raw = db.get_oauth_token("google")
        if not raw:
            print("No Google token stored — nothing to revoke.")
            return

        # Attempt to revoke with Google
        token_data = json.loads(raw)
        token = token_data.get("access_token") or token_data.get("refresh_token", "")
        if token:
            try:
                import urllib.request
                urllib.request.urlopen(
                    f"https://oauth2.googleapis.com/revoke?token={token}", timeout=10
                )
                print("✓  Token revoked with Google.")
            except Exception:
                print("⚠️   Could not revoke token with Google (may already be expired).")

        db.delete_oauth_token("google")
        print("✓  Token deleted from local storage.")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_client_secret_json(path: str):
    """Parse a GCP client_secret.json and return (client_id, client_secret)."""
    try:
        data = json.loads(Path(path).expanduser().read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"❌  Cannot read {path}: {exc}")
        return "", ""

    # GCP client_secret.json has either "web" or "installed" key
    creds = data.get("web") or data.get("installed") or {}
    client_id = creds.get("client_id", "").strip()
    client_secret = creds.get("client_secret", "").strip()
    return client_id, client_secret



class _CallbackServer(HTTPServer):
    """Single-request HTTP server that captures the OAuth callback."""

    def __init__(self, server_address, result: dict):
        self._result = result
        super().__init__(server_address, self._make_handler())

    def _make_handler(self):
        result = self._result

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # suppress access logs

            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                result["code"] = params.get("code", [""])[0]
                result["state"] = params.get("state", [""])[0]
                result["error"] = params.get("error", [""])[0]

                if result.get("error"):
                    body = b"<h2>Authorization failed</h2><p>You may close this window.</p>"
                else:
                    body = (
                        b"<h2>&#x2705; Google authorised!</h2>"
                        b"<p>You can close this window and return to the terminal.</p>"
                    )
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

        return Handler
