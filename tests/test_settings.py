"""Tests for config/settings.py::load_settings()'s calendar_backend
validation -- the fast-fail check that guards against the bootstrapping
order-of-operations bug found while designing Google Calendar support:
starting with AURA_CALENDAR_BACKEND=google but no saved token would
otherwise fail deep inside core/bootstrap.py with a confusing error, or
worse, silently pass a None credentials object down to GoogleCalendarProvider.
"""
from __future__ import annotations

import pytest

from config.settings import Settings, load_settings


def _base_kwargs(tmp_path, **overrides):
    kwargs = dict(
        ANTHROPIC_API_KEY="test-key",
        AURA_NOTES_SANDBOX_ROOT=tmp_path / "notes",
        calendar_events_file=tmp_path / "calendar" / "events.json",
        tasks_file=tmp_path / "tasks" / "tasks.json",
        memory_file=tmp_path / "memory" / "facts.json",
        user_profile_file=tmp_path / "memory" / "user_profile.json",
        logs_dir=tmp_path / "logs",
        AURA_WORKSPACE_ROOT=tmp_path / "workspace",
        google_client_secret_file=tmp_path / "calendar" / "google_client_secret.json",
        google_token_file=tmp_path / "calendar" / "google_token.json",
        _env_file=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_calendar_backend_local_needs_no_token(tmp_path, monkeypatch):
    monkeypatch.setattr("config.settings.Settings", lambda: Settings(**_base_kwargs(tmp_path)))
    settings = load_settings()
    assert settings.calendar_backend == "local"


def test_calendar_backend_google_without_token_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.settings.Settings",
        lambda: Settings(**_base_kwargs(tmp_path, AURA_CALENDAR_BACKEND="google")),
    )
    with pytest.raises(RuntimeError, match="calendar connect"):
        load_settings()


def test_calendar_backend_google_with_a_saved_token_succeeds(tmp_path, monkeypatch):
    token_file = tmp_path / "calendar" / "google_token.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "config.settings.Settings",
        lambda: Settings(**_base_kwargs(tmp_path, AURA_CALENDAR_BACKEND="google", google_token_file=token_file)),
    )
    settings = load_settings()
    assert settings.calendar_backend == "google"


def test_unknown_calendar_backend_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "config.settings.Settings",
        lambda: Settings(**_base_kwargs(tmp_path, AURA_CALENDAR_BACKEND="dropbox")),
    )
    with pytest.raises(RuntimeError, match="AURA_CALENDAR_BACKEND"):
        load_settings()
