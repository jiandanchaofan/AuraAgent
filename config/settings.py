"""Central application configuration, loaded from environment variables
and a local .env file (see .env.example). Every path/setting the rest of
the app needs flows through this single Settings object — nothing else
in the codebase should read os.environ directly.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # "anthropic" -> providers.anthropic_provider.AnthropicProvider
    # "openai"    -> providers.openai_provider.OpenAIProvider (also serves
    #                any OpenAI-compatible endpoint, e.g. DeepSeek, via
    #                openai_base_url)
    llm_provider: str = Field(default="anthropic", alias="AURA_LLM_PROVIDER")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")

    model_id: str = Field(default="claude-opus-5", alias="AURA_MODEL_ID")
    # 4096 (both SDKs' own default) turned out too small for a single
    # tool call carrying a lot of generated content (e.g. create_pptx for a
    # many-slide deck) -- the model hitting this limit mid-call leaves an
    # unparseable, truncated JSON argument string (see
    # providers/openai_provider.py's LLMOutputTruncatedError handling).
    llm_max_output_tokens: int = Field(default=8192, alias="AURA_LLM_MAX_OUTPUT_TOKENS")
    max_turns: int = Field(default=15, alias="AURA_MAX_TURNS")
    log_level: str = Field(default="INFO", alias="AURA_LOG_LEVEL")

    fetch_url_timeout_seconds: float = Field(default=10.0, alias="AURA_FETCH_URL_TIMEOUT_SECONDS")
    fetch_url_max_bytes: int = Field(default=200_000, alias="AURA_FETCH_URL_MAX_BYTES")
    download_max_bytes: int = Field(default=50_000_000, alias="AURA_DOWNLOAD_MAX_BYTES")
    skill_timeout_seconds: float = Field(default=30.0, alias="AURA_SKILL_TIMEOUT_SECONDS")
    clipboard_max_chars: int = Field(default=20_000, alias="AURA_CLIPBOARD_MAX_CHARS")
    document_read_max_bytes: int = Field(default=20_000_000, alias="AURA_DOCUMENT_READ_MAX_BYTES")
    document_extract_max_chars: int = Field(default=50_000, alias="AURA_DOCUMENT_EXTRACT_MAX_CHARS")

    # Fixed sandbox locations — not env-configurable in v1 so every tool's
    # blast radius is predictable regardless of how the process is launched.
    sandbox_root: Path = PROJECT_ROOT / "sandbox"
    # Like workspace_root below, meant to be pointed at a user's REAL notes
    # directory (e.g. an Obsidian vault) via /notes set or AURA_NOTES_
    # SANDBOX_ROOT — the boundary itself (resolve_within_sandbox()) still
    # always applies, only its location is configurable.
    notes_sandbox_root: Path = Field(
        default=PROJECT_ROOT / "sandbox" / "notes", alias="AURA_NOTES_SANDBOX_ROOT"
    )
    # N14 (Auralis / remote access): which subdirectory UNDER notes_sandbox_root
    # (or a real Obsidian vault, once /notes set repoints it) holds the daily
    # note files quick_note_sync appends into -- e.g. "Daily Notes", matching
    # the Obsidian Thino plugin's own convention. A relative sub-path, not a
    # second standalone root -- resolved through resolve_within_sandbox()
    # against notes_root.current at use time, same boundary as every other
    # notes tool, just one level deeper.
    quick_notes_subdir: str = Field(default="Daily Notes", alias="AURA_QUICK_NOTES_SUBDIR")
    # The other exception to "not env-configurable": file_tool's general
    # file-management tools (tools/files/) are meant to be pointed at a
    # user's REAL working directory, not just a repo-local sandbox — the
    # boundary itself (resolve_within_sandbox()) still always applies,
    # only its location is configurable.
    workspace_root: Path = Field(default=PROJECT_ROOT / "sandbox" / "workspace", alias="AURA_WORKSPACE_ROOT")
    calendar_events_file: Path = PROJECT_ROOT / "sandbox" / "calendar" / "events.json"
    # "local" -> LocalJSONCalendarProvider (default) | "google" -> GoogleCalendarProvider.
    # Deliberately NOT runtime-swappable via a CLI arg the way workspace_root/
    # notes_sandbox_root are -- /calendar connect (cli/commands.py) flips this
    # in .env itself once OAuth succeeds and hot-swaps the live provider via
    # SwappableCalendarProvider, so a human never edits this by hand in the
    # normal flow. See load_settings()'s validation below for what happens if
    # someone does anyway.
    calendar_backend: str = Field(default="local", alias="AURA_CALENDAR_BACKEND")
    # google_client_secret_file: the OAuth "Desktop app" client JSON the user
    # downloads themselves from Google Cloud Console (this project can't
    # automate creating that project/OAuth client) -- /calendar connect reads
    # it. google_token_file: written by /calendar connect after a successful
    # authorization; both live under sandbox/calendar/, already gitignored.
    google_client_secret_file: Path = PROJECT_ROOT / "sandbox" / "calendar" / "google_client_secret.json"
    google_token_file: Path = PROJECT_ROOT / "sandbox" / "calendar" / "google_token.json"

    # "local" -> LocalJSONTaskProvider (default) | "google" -> GoogleTaskProvider.
    # Mirrors calendar_backend exactly -- /tasks connect flips this in .env
    # once OAuth succeeds (see cli/service.py::finish_google_tasks_connect).
    tasks_backend: str = Field(default="local", alias="AURA_TASKS_BACKEND")
    # Tasks gets its OWN token file (separate scope/consent from Calendar,
    # see tools/tasks/google_tasks_auth.py) but reuses the SAME
    # google_client_secret_file above -- one registered OAuth Desktop-app
    # Client ID is reusable across scopes/token files.
    google_tasks_token_file: Path = PROJECT_ROOT / "sandbox" / "tasks" / "google_tasks_token.json"
    # The id of the "AuraAgent" Google Tasks list, looked up/created once at
    # connect time (tools/tasks/google_task_provider.py::ensure_aura_task_list)
    # and cached here so a later restart/reconnect doesn't create a
    # duplicate list. None until the first successful /tasks connect;
    # persisted to .env the same way AURA_TASKS_BACKEND is (set_key), not
    # meant to be hand-edited.
    google_tasks_list_id: str | None = Field(default=None, alias="AURA_GOOGLE_TASKS_LIST_ID")
    # Project ("/project" -- cli/commands.py): projects_dir is where a new
    # project's directory is auto-created when /project create doesn't get
    # an explicit existing path; project_meta_dir holds each project's
    # registry entry + summary, deliberately kept OUTSIDE any project's own
    # directory (which stays pure user/model content) -- same config/ (app
    # state) vs sandbox/ (user content) split used elsewhere in this project.
    projects_dir: Path = PROJECT_ROOT / "sandbox" / "projects"
    project_meta_dir: Path = PROJECT_ROOT / "sandbox" / "project_meta"
    # Hard cap on ProjectSummary.current_state's length (update_project_summary
    # truncates, it doesn't just hope the model stays brief) -- keeps a
    # project's token cost roughly constant no matter how long it's been
    # worked on. Same config pattern as clipboard_max_chars/fetch_url_max_bytes.
    project_summary_max_chars: int = Field(default=4_000, alias="AURA_PROJECT_SUMMARY_MAX_CHARS")
    # How often tools/scheduler/scheduler_loop.py checks for due scheduled
    # tasks. Daily/weekly/etc. granularity doesn't need sub-minute
    # precision -- 60s keeps the check cheap (one JSON read + datetime
    # comparisons) without meaningfully delaying anything.
    scheduler_poll_interval_seconds: float = Field(default=60.0, alias="AURA_SCHEDULER_POLL_INTERVAL_SECONDS")
    # GUI-only (gui/server.py, tools/sessions/session_store.py) -- the CLI
    # has no equivalent, its "history" is already scoped to one process
    # lifetime by design (see core/react_engine.py's docstring).
    chat_sessions_dir: Path = PROJECT_ROOT / "sandbox" / "chat_sessions"
    tasks_file: Path = PROJECT_ROOT / "sandbox" / "tasks" / "tasks.json"
    memory_file: Path = PROJECT_ROOT / "sandbox" / "memory" / "facts.json"
    user_profile_file: Path = PROJECT_ROOT / "sandbox" / "memory" / "user_profile.json"
    logs_dir: Path = PROJECT_ROOT / "logs"
    skills_dir: Path = PROJECT_ROOT / "skills_store"
    mcp_config_path: Path = PROJECT_ROOT / "config" / "mcp_servers.json"
    agents_config_path: Path = PROJECT_ROOT / "config" / "agents.json"
    # Where /config set-key (cli/commands.py) and its GUI REST counterpart
    # (gui/routes.py) write a newly-entered API key -- deliberately a
    # Settings field, not a bare PROJECT_ROOT constant inside
    # core/bootstrap.py, so a test can redirect it at a tmp_path file
    # instead of ever touching the real .env (same reasoning as
    # agents_config_path/workspace_root above).
    env_file_path: Path = PROJECT_ROOT / ".env"

    # N14 (Auralis / remote access): off by default, so a purely local
    # setup (today's only real scenario) is completely unaffected -- flip
    # to True only once you're about to put this process behind a
    # Cloudflare Tunnel, AFTER registering at least one device token (see
    # `/device register`, tools/devices/device_store.py) so you don't lock
    # yourself out of the very REST call that would let you register one.
    require_auth: bool = Field(default=False, alias="AURA_REQUIRE_AUTH")
    devices_file: Path = PROJECT_ROOT / "sandbox" / "devices" / "registry.json"

    # Auralis personal-data graph (footprints/persons/projects/links) --
    # relational by nature (many-to-many links, not a flat list), so this
    # is SQLite, not another JSON file -- see tools/personal_graph/graph_store.py.
    personal_graph_db_file: Path = PROJECT_ROOT / "sandbox" / "personal_graph" / "graph.db"

    # Telegram bot frontend (telegram_bot.py, tg_bot/) -- a third frontend
    # alongside the CLI and GUI. Telegram's own external credential name
    # (from @BotFather), no AURA_ prefix, same convention as
    # ANTHROPIC_API_KEY/OPENAI_API_KEY above.
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    # AuraAgent-internal policy knob: the ONE Telegram chat_id this bot will
    # ever respond to -- this is a personal single-user assistant, not a
    # public bot. None until the user discovers their own chat_id (the bot
    # replies with it on any message while this is unset) and sets it.
    telegram_allowed_chat_id: int | None = Field(default=None, alias="AURA_TELEGRAM_ALLOWED_CHAT_ID")
    # Telegram-only chat session store (tools/sessions/session_store.py) --
    # deliberately separate from chat_sessions_dir (GUI's own), plain field
    # name/no alias, same precedent as chat_sessions_dir/devices_file above.
    telegram_chat_sessions_dir: Path = PROJECT_ROOT / "sandbox" / "telegram_chat_sessions"


def load_settings() -> Settings:
    settings = Settings()
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill in your key."
            )
    elif settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Copy .env.example to .env and fill in your key "
                "(for DeepSeek: your DeepSeek API key, plus OPENAI_BASE_URL=https://api.deepseek.com)."
            )
    else:
        raise RuntimeError(
            f"Unknown AURA_LLM_PROVIDER '{settings.llm_provider}' — expected 'anthropic' or 'openai'."
        )

    if settings.calendar_backend == "google":
        if not settings.google_token_file.exists():
            raise RuntimeError(
                "AURA_CALENDAR_BACKEND=google but no Google token was found at "
                f"'{settings.google_token_file}'. Run /calendar connect first while "
                "AURA_CALENDAR_BACKEND is still 'local' (it switches to 'google' and "
                "saves the token itself once authorization succeeds), then restart. "
                "If you set this env var by hand, unset it (or set it back to 'local'), "
                "restart, run /calendar connect, then restart again."
            )
    elif settings.calendar_backend != "local":
        raise RuntimeError(
            f"Unknown AURA_CALENDAR_BACKEND '{settings.calendar_backend}' — expected 'local' or 'google'."
        )

    if settings.tasks_backend == "google":
        if not settings.google_tasks_token_file.exists():
            raise RuntimeError(
                "AURA_TASKS_BACKEND=google but no Google Tasks token was found at "
                f"'{settings.google_tasks_token_file}'. Run /tasks connect first while "
                "AURA_TASKS_BACKEND is still 'local' (it switches to 'google' and "
                "saves the token itself once authorization succeeds), then restart."
            )
        if not settings.google_tasks_list_id:
            raise RuntimeError(
                "AURA_TASKS_BACKEND=google but no AURA_GOOGLE_TASKS_LIST_ID was saved. "
                "Run /tasks connect again."
            )
    elif settings.tasks_backend != "local":
        raise RuntimeError(
            f"Unknown AURA_TASKS_BACKEND '{settings.tasks_backend}' — expected 'local' or 'google'."
        )
    return settings
