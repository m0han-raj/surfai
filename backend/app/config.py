"""Application configuration.

Every value is overridable through the environment so that no secret ever needs
to live in the source tree. See `.env.example` at the repository root.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    app_name: str = "SurfAI"
    environment: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Database --------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://surfai:surfai@localhost:5432/surfai",
        description="SQLAlchemy URL. Tests override this with SQLite.",
    )
    db_echo: bool = False

    # --- LLM -------------------------------------------------------------
    # Defaults target a local OpenAI-compatible runtime (Ollama, llama.cpp,
    # vLLM, LM Studio). No API key is required for most local runtimes.
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-oss-20b"
    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1536
    """Hard cap on completion length; keeps local models responsive."""

    # --- Agent loop ------------------------------------------------------
    max_agent_steps: int = 15
    max_retries: int = 2
    action_timeout_ms: int = 10_000
    task_timeout_s: int = 300

    # --- Context budget --------------------------------------------------
    max_elements_in_context: int = 60
    max_page_summary_chars: int = 1200
    max_recent_actions: int = 6
    max_extract_chars: int = 4000

    # --- Security --------------------------------------------------------
    # `local` is a single unauthenticated user, correct only for a backend bound
    # to localhost. `google` verifies a bearer token on every request and is the
    # only supported mode for a backend reachable from the network.
    auth_provider: str = "local"
    local_user_id: str = "local-user"
    google_client_id: str = ""
    # Optional allowlist. Empty means any Google account that authenticates with
    # our client id is accepted; set it to run a private instance.
    allowed_emails: str = ""
    cors_allow_origins: str = "chrome-extension://*"
    # Risk categories that must never auto-execute even if reclassified.
    always_confirm_categories: str = "PURCHASE,PAYMENT,DELETE,ACCOUNT_CHANGES"

    @field_validator("llm_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def is_local_auth(self) -> bool:
        return self.auth_provider.lower() == "local"

    @property
    def always_confirm_set(self) -> set[str]:
        return {c.strip().upper() for c in self.always_confirm_categories.split(",") if c.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
