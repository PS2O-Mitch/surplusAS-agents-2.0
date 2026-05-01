"""Deploy a SurplusAS agent to Vertex AI Agent Engine.

Wraps `vertexai.agent_engines.create()` with the boilerplate every agent
in this repo needs:

- Imports the agent module (`agents.<name>.agent`) and grabs the `agent`
  object built with `google.adk.Agent(...)`.
- Reads display name + service account from `agents/<name>/manifest.yaml`.
- Calls `agent_engines.create(...)` against `vertexai.init(project, location)`.
- Prints the resulting `resource_name`. The caller is expected to copy
  that into Terraform / `.env` (e.g. `PRICING_AGENT_RESOURCE=...`).

Used both by humans (one-off deploys) and Cloud Build (the per-agent
pipelines call this in their last step). Re-running with the same display
name UPDATES the existing engine — Vertex's idempotency by display name.

    uv run python -m scripts.deploy_agent --name pricing
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

VALID_AGENTS = ("concierge", "pricing", "onboarding", "listing_intake", "dispute_triage")


def _load_manifest(name: str) -> dict[str, Any]:
    manifest_path = REPO_ROOT / "agents" / name / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_agent_object(name: str) -> Any:
    """Import `agents.<name>.agent` and return its `agent` attribute."""
    mod = importlib.import_module(f"agents.{name}.agent")
    if not hasattr(mod, "agent"):
        raise AttributeError(
            f"agents.{name}.agent has no `agent` attribute — "
            f"did you replace the stub with an `Agent(...)` instance?"
        )
    return mod.agent


def deploy(name: str) -> str:
    """Deploy the named agent. Returns the resulting resource name."""
    manifest = _load_manifest(name)
    agent_obj = _load_agent_object(name)

    # Late import — vertexai pulls in heavy auth/genai code. Keep CLI startup snappy.
    import vertexai
    from vertexai import agent_engines

    project = manifest.get("env", {}).get("GOOGLE_CLOUD_PROJECT", "ps2o-surplusas-api")
    location = manifest.get("region", "us-central1")
    display_name = manifest["displayName"]
    service_account = manifest["serviceAccount"]

    vertexai.init(project=project, location=location)

    # `requirements` are deduced from the deployed image's pyproject in CI;
    # for local dev deploys we hand them in explicitly so the engine venv
    # has what it needs to import shared/* and pricing_engine/.
    #
    # Keep this in lockstep with `pyproject.toml::dependencies`. The OTel
    # entries are required because `shared/tracing.py` is on the import
    # path of every agent (via `shared.a2a → a2a_client_span`); omitting
    # them would crash the deployed agent at first import.
    requirements = [
        "google-adk>=2.0.0b1",
        "google-genai>=0.5.0",
        "google-cloud-aiplatform>=1.149.0",
        "google-cloud-secret-manager>=2.20.0",
        "google-auth>=2.35.0",
        "asyncpg>=0.30.0",
        "cloud-sql-python-connector[asyncpg]>=1.18.0",
        "pydantic>=2.9.0",
        "pydantic-settings>=2.5.0",
        "structlog>=24.4.0",
        "tenacity>=9.0.0",
        "argon2-cffi>=23.1.0",
        "opentelemetry-api>=1.27.0",
        "opentelemetry-sdk>=1.27.0",
        "opentelemetry-exporter-gcp-trace>=1.7.0",
    ]

    # `extra_packages` are paths to local packages to vendor into the deployed
    # bundle. We need the whole repo so the deployed agent can resolve
    # `from shared...` and `from pricing_engine...` imports.
    extra_packages = [
        str(REPO_ROOT / "agents"),
        str(REPO_ROOT / "shared"),
        str(REPO_ROOT / "vendor" / "surplusas-pricing"),
    ]

    print(
        f"Deploying agent={name} display_name={display_name} "
        f"project={project} location={location} sa={service_account}"
    )

    remote = agent_engines.create(
        agent_obj,  # type: ignore[arg-type]  # AdkApp accepts any Agent
        display_name=display_name,
        requirements=requirements,
        extra_packages=extra_packages,
        service_account=service_account,
    )

    resource_name: str = remote.resource_name  # type: ignore[attr-defined]
    print()
    print(f"resource_name={resource_name}")
    print()
    print("Set this in your .env / Terraform tfvars:")
    print(f"  {name.upper()}_AGENT_RESOURCE={resource_name}")
    return resource_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy a SurplusAS agent to Agent Engine")
    parser.add_argument("--name", required=True, choices=VALID_AGENTS)
    args = parser.parse_args(argv)

    try:
        deploy(args.name)
    except Exception as exc:  # noqa: BLE001 — top-level CLI; surface anything cleanly
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
