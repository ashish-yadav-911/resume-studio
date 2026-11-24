"""Application configuration.

All settings are environment-driven.  The defaults are deliberately safe for
local development and never contain real credentials.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "Resume Studio"
    app_env: str = "development"
    api_prefix: str = "/api"

    # Storage
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/app.db"
    max_upload_mb: int = 10

    # Auth: "dev" (local header based) or "firebase" or "auto"
    auth_mode: str = "auto"
    dev_user_id: str = "demo-user"
    firebase_project_id: str | None = None
    firebase_credentials_path: str | None = None
    # Optional write-through mirror of conversations, messages and immutable
    # versions to Firestore.  SQLite remains the transactional source of truth.
    firebase_sync_enabled: bool = True
    firebase_collection_prefix: str = "resume_studio"

    # LLM
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2

    # Research
    # Live web search used to derive role/company requirements when the user asks
    # to optimize for a company or role without pasting a job description.
    web_search_enabled: bool = True
    web_search_model: str | None = None
    web_search_max_results: int = 4
    exa_api_key: str | None = None
    exa_base_url: str = "https://api.exa.ai"
    research_timeout_seconds: float = 20.0
    # Wikimedia requires a descriptive User-Agent with contact details.
    research_user_agent: str = (
        "ResumeStudio/1.0 (+https://example.com/resume-studio; contact: research@example.com)"
    )

    # Optional durable LangGraph checkpoints (Postgres)
    langgraph_checkpoint_url: str | None = None

    # CORS
    allowed_origins: str = "http://localhost:2000,http://127.0.0.1:2000,http://localhost:3000,http://127.0.0.1:3000"

    # Limits
    max_message_chars: int = 8000
    max_jd_chars: int = 30000
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # Retention (days, 0 = keep)
    resume_retention_days: int = 0

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def research_enabled(self) -> bool:
        return bool(self.exa_api_key)

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    return settings


settings = get_settings()
