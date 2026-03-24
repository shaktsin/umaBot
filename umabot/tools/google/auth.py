"""Google OAuth2 helpers for umabot.

Flow:
  1. Call ``build_auth_url(config, db)`` → returns (url, state)
  2. User opens URL in browser and authorises the app
  3. Google redirects to the control-panel callback with ?code=…&state=…
  4. Callback calls ``exchange_code(config, db, code, state)`` → stores token
  5. Subsequent calls to ``get_credentials(config, db)`` return valid Credentials,
     auto-refreshing when expired.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from typing import Optional, Tuple
from urllib.parse import urlencode

logger = logging.getLogger("umabot.tools.google.auth")

# Google OAuth endpoints
_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# All scopes needed for Gmail + Calendar + Tasks
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks",
    "openid",
    "email",
]

PROVIDER = "google"

# In-memory state store for CSRF protection (state → None; cleared after use)
_pending_states: dict[str, str] = {}


def build_auth_url(client_id: str, redirect_uri: str) -> Tuple[str, str]:
    """Return (auth_url, state) for the OAuth2 PKCE-like flow."""
    state = secrets.token_urlsafe(16)
    _pending_states[state] = redirect_uri
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{_AUTH_URI}?{urlencode(params)}", state


def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    state: str,
    db,
) -> bool:
    """Exchange authorization code for tokens and persist to DB.

    Returns True on success, raises on failure.
    """
    if state not in _pending_states:
        raise ValueError(f"Unknown or expired OAuth state: {state!r}")
    _pending_states.pop(state, None)

    import urllib.request
    payload = json.dumps({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        _TOKEN_URI,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_data = json.loads(resp.read().decode())

    if "error" in token_data:
        raise RuntimeError(f"Token exchange failed: {token_data['error']}: {token_data.get('error_description', '')}")

    db.store_oauth_token(PROVIDER, json.dumps(token_data))
    logger.info("Google OAuth token stored successfully")
    return True


def get_credentials(client_id: str, client_secret: str, db) -> Optional[object]:
    """Return a valid google.oauth2.credentials.Credentials object, or None if not authorised.

    Automatically refreshes expired tokens and re-persists them.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleRequest
    except ImportError:
        raise ImportError(
            "google-auth-oauthlib is required for Google tools. "
            "Install with: pip install google-auth-oauthlib google-api-python-client"
        )

    raw = db.get_oauth_token(PROVIDER)
    if not raw:
        return None

    token_data = json.loads(raw)
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=_SCOPES,
    )

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            # Persist the refreshed token
            updated = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_type": "Bearer",
                "scope": " ".join(creds.scopes or []),
            }
            if creds.expiry:
                updated["expiry"] = creds.expiry.isoformat()
            db.store_oauth_token(PROVIDER, json.dumps(updated))
            logger.debug("Google OAuth token refreshed and re-persisted")
        except Exception as exc:
            logger.warning("Failed to refresh Google token: %s", exc)
            return None

    return creds


def is_authorized(db) -> bool:
    """Quick check: is a Google OAuth token stored?"""
    return db.get_oauth_token(PROVIDER) is not None
