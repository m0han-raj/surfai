"""Application configuration.

Every value is overridable through the environment so that no secret ever needs
to live in the source tree. See `.env.example` at the repository root.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _with_psycopg_driver(url: str) -> str:
    """Point a bare PostgreSQL URL at psycopg 3.

    `postgresql://` selects psycopg2 by default, which is not installed, and the
    resulting ModuleNotFoundError names a package nobody asked for. Anything
    already carrying a driver, and anything that is not PostgreSQL, is returned
    untouched.
    """
    if not url:
        return url
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


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
    # Managed providers inject the connection string under their own names and
    # never with a driver prefix. Read as fallbacks so provisioning a database
    # on Vercel, Neon or Supabase needs no manual copying. Ordered so a pooled
    # URL wins: a serverless host opens a connection per invocation.
    postgres_url: str = ""
    postgres_prisma_url: str = ""
    database_url_unpooled: str = ""
    postgres_url_non_pooling: str = ""
    db_echo: bool = False
    # Set automatically by Vercel. On a serverless host every invocation may be
    # a fresh process, so a per-process connection pool multiplies by the number
    # of concurrent lambdas and exhausts PostgreSQL. Detecting it here lets the
    # engine hold no pool at all.
    vercel: str = ""

    @property
    def is_serverless(self) -> bool:
        return bool(self.vercel)

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
    # --- browser control via MCP -------------------------------------
    #
    # Off by default. Driving the browser needs a Playwright MCP server
    # running beside the browser itself, which the hosted deployment can never
    # have: a function on Vercel cannot reach a Chrome on somebody's laptop.
    # Everything SurfAI did before this works unchanged when it is off.
    mcp_enabled: bool = False
    mcp_server_url: str = ""
    mcp_timeout_s: float = 60.0
    #: Ceiling on browser tool calls in one task, independent of agent steps.
    #: A model that has got stuck clicking the same thing should stop.
    mcp_max_tool_calls: int = 12
    #: Ceiling on one tool result. A Playwright accessibility snapshot of a
    #: real site runs past 20,000 characters, which is ~5,000 tokens and most
    #: of a free tier's per-minute budget in a single call. Measured: doing
    #: that twice rate-limited the model mid-task.
    mcp_max_result_chars: int = 6000
    #: Optional comma-separated allowlist of tool names to expose, e.g.
    #: "browser_navigate,browser_snapshot,browser_click". Empty means every
    #: tool the server offers.
    #:
    #: Tools are still discovered from the server, never hard-coded; this only
    #: narrows what is forwarded to the model. It exists because the schemas
    #: are the dominant token cost: all 24 of Playwright MCP's tools come to
    #: roughly 5,000 tokens on *every* turn, which on a free tier allowing
    #: 8,000 a minute leaves no room for the page or the answer. Measured.
    mcp_tool_filter: str = ""

    max_elements_in_context: int = 60
    # What the model is allowed to see of a page's text. Was 1200, about two
    # hundred words, which meant an answer "about this page" was really about
    # its opening sentence. The extension sends a budget of its own; this is
    # the ceiling, and it must not sit below it or it silently re-truncates.
    max_page_summary_chars: int = 12000
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

    @model_validator(mode="after")
    def _resolve_database_url(self) -> Settings:
        """Pick a connection string and give it the driver SQLAlchemy needs.

        Managed providers hand out `postgres://` or `postgresql://` URLs, which
        SQLAlchemy resolves to psycopg2 rather than the psycopg 3 driver that is
        actually installed. Normalising here means a URL pasted straight from a
        provider dashboard works.
        """
        # `model_fields_set` is the only reliable signal that a value was
        # actually supplied, rather than left at the field default. Comparing
        # against the default string breaks the moment the default changes.
        if "database_url" not in self.model_fields_set:
            for candidate in (
                self.postgres_url,
                self.postgres_prisma_url,
                self.database_url_unpooled,
                self.postgres_url_non_pooling,
            ):
                if candidate:
                    self.database_url = candidate
                    break

        self.database_url = _with_psycopg_driver(self.database_url)
        return self

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
