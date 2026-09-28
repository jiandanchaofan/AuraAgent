"""OAuth 2.0 plumbing for GoogleCalendarProvider — the only place in this
codebase that performs an OAuth authorization flow. Kept deliberately
separate from google_calendar_provider.py: this module's job stops at
"produce/refresh a valid Credentials object", the provider's job is
"use one to talk to the Calendar API".

Uses google-auth-oauthlib's InstalledAppFlow (a local-loopback browser
consent flow) rather than hand-rolling the PKCE/state-validation dance
ourselves — OAuth correctness is security-sensitive, and Google's own
maintained library is the safer bet here, same reasoning that justified
pyperclip/mss/psutil/plyer elsewhere in this project (reimplementing
would be more fragile than a real, justified dependency).

Scope is deliberately the narrower `calendar.events` (read/write events
only), not the broader `calendar` scope (which also covers creating/
deleting calendars themselves) — least privilege, since AuraAgent never
needs to manage calendars, only events on the user's primary one.
"""
from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def run_oauth_flow(client_secret_file: Path, token_file: Path) -> None:
    """Blocking — opens a browser and waits for the user to approve the
    consent screen. access_type='offline' + prompt='consent' are both
    required for Google to reliably issue a refresh_token (without them,
    a re-authorization, e.g. after /calendar disconnect then /calendar
    connect again, can silently come back with no refresh_token at all,
    leaving the connection unable to renew itself past the first hour)."""
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), scopes=SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")


def load_credentials(token_file: Path) -> Credentials | None:
    """None if no token has been saved yet (i.e. /calendar connect hasn't
    been run). Does not refresh — GoogleCalendarProvider does that lazily
    on first use, since refreshing here would be a network call made
    unconditionally at every startup even when nothing ends up using it."""
    if not token_file.exists():
        return None
    return Credentials.from_authorized_user_file(str(token_file), scopes=SCOPES)


__all__ = ["SCOPES", "run_oauth_flow", "load_credentials", "Request", "Credentials"]
