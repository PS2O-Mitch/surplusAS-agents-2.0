"""Centralised env-driven configuration.

All values come from environment variables (12-factor). In production the
secrets (DB password, webhook signing key) are pulled from Secret Manager
into env vars at container start by the Agent Engine / Cloud Run runtime.
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

    # --- Vertex AI / Gemini ---------------------------------------------
    google_genai_use_vertexai: bool = True
    google_cloud_project: str = "ps2o-surplusas-api"
    google_cloud_location: str = "us-central1"

    concierge_model: str = "gemini-2.5-pro"
    dispute_triage_model: str = "gemini-2.5-pro"
    pricing_model: str = "gemini-2.5-flash"
    onboarding_model: str = "gemini-2.5-flash"
    listing_intake_model: str = "gemini-2.5-flash"

    # --- Cloud SQL ------------------------------------------------------
    cloud_sql_instance: str = "ps2o-surplusas-api:us-central1:surplusas-db"
    db_name: str = "surplusas"
    db_user: str = "surplusas_agents_app"
    db_password: str = ""

    # --- Inter-agent A2A peers -----------------------------------------
    # Vertex AI Agent Engine resource names, e.g.
    # projects/<num>/locations/us-central1/reasoningEngines/<id>.
    # Populated by Terraform outputs after each agent is deployed.
    concierge_agent_resource: str = ""
    pricing_agent_resource: str = ""
    onboarding_agent_resource: str = ""
    listing_intake_agent_resource: str = ""
    dispute_triage_agent_resource: str = ""

    # --- Webhooks -------------------------------------------------------
    webhook_signing_key: str = ""

    # --- Service --------------------------------------------------------
    log_level: str = "INFO"
    port: int = Field(default=8080, ge=1, le=65535)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, instantiated once."""
    # Ensure ADK picks up the Vertex flag before any google.genai import.
    settings = Settings()
    if settings.google_genai_use_vertexai:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)
    return settings
