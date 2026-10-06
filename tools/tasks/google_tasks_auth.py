"""OAuth 2.0 plumbing for GoogleTaskProvider — mirrors
tools/calendar/google_auth.py exactly, but kept as its own separate module
rather than extending that one: Tasks gets its OWN scope and its OWN token
file, a deliberately independent authorization from Calendar (least
privilege, narrow-by-feature, same reasoning that module's own docstring
gives for using the narrow `calendar.events` scope rather than the broader
`calendar` one) — connecting/disconnecting Tasks never touches Calendar's
own token, and vice versa. Both DO share the same `google_client_secret_file`
setting (config/settings.py): one registered Google Cloud Console OAuth
Desktop-app Client ID is reusable across any number of scopes/token files,
only the token file and scope differ per integration.
"""
from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/tasks"]


def run_oauth_flow(client_secret_file: Path, token_file: Path) -> None:
    """Blocking — opens a browser and waits for the user to approve the
    consent screen. access_type='offline' + prompt='consent' are both
    required for Google to reliably issue a refresh_token (see
    tools/calendar/google_auth.py's identical note)."""
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_file), scopes=SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")


def load_credentials(token_file: Path) -> Credentials | None:
    """None if no token has been saved yet (i.e. /tasks connect hasn't
    been run). Does not refresh — GoogleTaskProvider does that lazily on
    first use, same reasoning as the Calendar equivalent."""
    if not token_file.exists():
        return None
    return Credentials.from_authorized_user_file(str(token_file), scopes=SCOPES)


__all__ = ["SCOPES", "run_oauth_flow", "load_credentials", "Request", "Credentials"]
