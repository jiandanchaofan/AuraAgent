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
    max_turns: int = Field(default=15, alias="AURA_MAX_TURNS")
    log_level: str = Field(default="INFO", alias="AURA_LOG_LEVEL")

    # Fixed sandbox locations — not env-configurable in v1 so every tool's
    # blast radius is predictable regardless of how the process is launched.
    sandbox_root: Path = PROJECT_ROOT / "sandbox"
    notes_sandbox_root: Path = PROJECT_ROOT / "sandbox" / "notes"
    calendar_events_file: Path = PROJECT_ROOT / "sandbox" / "calendar" / "events.json"
    logs_dir: Path = PROJECT_ROOT / "logs"
    skills_dir: Path = PROJECT_ROOT / "skills_store"
    mcp_config_path: Path = PROJECT_ROOT / "config" / "mcp_servers.json"


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
    return settings
