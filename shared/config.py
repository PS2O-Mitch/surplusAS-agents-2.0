"""Centralised env-driven configuration.

All values come from environment variables (12-factor). In production the
secrets (Gemini API key, DB DSN, webhook signing key) are injected as env
vars at container start by the host (Fly secrets).
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings. Construct via `get_settings()` (cached)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Gemini ----------------------------------------------------------
    # Default path: Gemini Developer API via GOOGLE_API_KEY. The Vertex
    # escape hatch stays for anyone re-pointing at a GCP project.
    google_genai_use_vertexai: bool = False
    google_api_key: str = ""

    concierge_model: str = "gemini-2.5-pro"
    dispute_triage_model: str = "gemini-2.5-pro"
    pricing_model: str = "gemini-2.5-flash"
    onboarding_model: str = "gemini-2.5-flash"
    listing_intake_model: str = "gemini-2.5-flash"

    # --- Postgres ---------------------------------------------------------
    # Plain asyncpg DSN (Supabase). Use the session-mode pooler or the
    # direct connection — transaction-mode pgBouncer breaks asyncpg
    # prepared statements.
    database_url: str = ""

    # --- Webhooks -------------------------------------------------------
    webhook_signing_key: str = ""
    webhook_retry_interval_s: int = 30
    webhook_retry_batch_limit: int = 100

    # --- Service --------------------------------------------------------
    log_level: str = "INFO"
    port: int = Field(default=8080, ge=1, le=65535)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, instantiated once."""
    # google-genai reads its config from os.environ (not pydantic's .env),
    # so mirror the relevant values before any google.genai client is built.
    settings = Settings()
    if settings.google_genai_use_vertexai:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    elif settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
    return settings
