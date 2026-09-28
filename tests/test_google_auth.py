"""Tests for tools/calendar/google_auth.py.

load_credentials()'s two paths are ordinary unit tests. run_oauth_flow()'s
REAL behavior -- opening a browser, waiting for a human to click "Allow" on
Google's consent screen -- is fundamentally not something an automated test
can drive, same category as tools/system/notification_tool.py's real popup
being untestable. What IS testable without a browser, and is the whole
point of this file, is that run_oauth_flow() calls Google's OAuth library
with access_type="offline" + prompt="consent" -- dropping either of these
is a real bug this project already found once (see google_auth.py's
docstring): without them Google may not issue a refresh_token at all, or
only on first consent, breaking /calendar disconnect + reconnect later.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tools.calendar.google_auth import load_credentials, run_oauth_flow


def _write_fake_authorized_user_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "refresh_token": "fake-refresh-token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "token": "fake-access-token",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/calendar.events"],
            }
        ),
        encoding="utf-8",
    )


def test_load_credentials_returns_none_when_no_token_file(tmp_path):
    assert load_credentials(tmp_path / "google_token.json") is None


def test_load_credentials_loads_a_saved_token(tmp_path):
    token_file = tmp_path / "google_token.json"
    _write_fake_authorized_user_file(token_file)

    credentials = load_credentials(token_file)

    assert credentials is not None
    assert credentials.refresh_token == "fake-refresh-token"


def test_run_oauth_flow_requests_offline_access_and_forced_consent(tmp_path, monkeypatch):
    fake_credentials = MagicMock()
    fake_credentials.to_json.return_value = json.dumps({"refresh_token": "x"})

    fake_flow = MagicMock()
    fake_flow.run_local_server.return_value = fake_credentials

    from_client_secrets_file = MagicMock(return_value=fake_flow)
    monkeypatch.setattr(
        "tools.calendar.google_auth.InstalledAppFlow.from_client_secrets_file", from_client_secrets_file
    )

    client_secret_file = tmp_path / "google_client_secret.json"
    client_secret_file.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "google_token.json"

    run_oauth_flow(client_secret_file, token_file)

    fake_flow.run_local_server.assert_called_once()
    _args, kwargs = fake_flow.run_local_server.call_args
    assert kwargs.get("access_type") == "offline"
    assert kwargs.get("prompt") == "consent"
    assert token_file.read_text(encoding="utf-8") == json.dumps({"refresh_token": "x"})
