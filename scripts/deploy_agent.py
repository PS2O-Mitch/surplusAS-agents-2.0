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
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parent.parent


class AgentManifest(BaseModel):
    """Schema for `agents/<name>/manifest.yaml`.

    Validated at deploy time so a bad manifest fails with a clear pydantic
    error, not a `KeyError` from deep inside `agent_engines.create()`.
    """

    model_config = ConfigDict(extra="forbid")

    displayName: str
    description: str = ""
    agentFramework: str = "ADK"
    model: str
    region: str = "us-central1"
    serviceAccount: str
    envFromSecretManager: list[str] = Field(default_factory=list)
    env: dict[str, str | int | bool | float] = Field(default_factory=dict)

VALID_AGENTS = ("concierge", "pricing", "onboarding", "listing_intake", "dispute_triage")

# Mapping from the env-var name an agent expects (manifest `envFromSecretManager`)
# to the Secret Manager secret id created by Terraform (infra/terraform/secret_manager.tf).
# Add a new row when introducing a new secret-backed env var; the deploy will
# fail loudly if a manifest references an env name that isn't mapped here.
SECRET_ID_MAP = {
    "DB_PASSWORD": "db-password-agents",
    "WEBHOOK_SIGNING_KEY": "webhook-signing-key",
}

# Which peer resource env vars each agent needs visible at runtime so its
# A2A routing tools can resolve handles. Concierge talks to all four
# specialists; Listing Intake calls Pricing for live anchors; Dispute Triage
# (Phase 4) replays through Pricing. Pulled from `shared.config.get_settings()`
# at deploy time so the values match the local `.env` we maintain alongside
# this script.
_PEER_RESOURCE_FORWARDS: dict[str, tuple[str, ...]] = {
    "concierge": (
        "PRICING_AGENT_RESOURCE",
        "ONBOARDING_AGENT_RESOURCE",
        "LISTING_INTAKE_AGENT_RESOURCE",
        "DISPUTE_TRIAGE_AGENT_RESOURCE",
    ),
    "listing_intake": ("PRICING_AGENT_RESOURCE",),
    "dispute_triage": ("PRICING_AGENT_RESOURCE",),
}

# Vertex Agent Engine rejects these env names (it auto-injects them into the
# deployed container). Including them in `env_vars` triggers:
#   400 Environment variable name '<NAME>' is reserved.
# We strip them here so the manifest stays declarative without a separate
# "things Vertex provides" list — the agent still sees them at runtime.
_VERTEX_RESERVED_ENV = frozenset(
    {
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_AGENT_ENGINE_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "PORT",
        "K_SERVICE",
        "K_REVISION",
        "K_CONFIGURATION",
    }
)


def _load_manifest(name: str) -> AgentManifest:
    manifest_path = REPO_ROOT / "agents" / name / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")
    with manifest_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return AgentManifest.model_validate(raw)


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
    """Deploy the named agent to Vertex AI Agent Engine. Returns the resource name.

    The agent is wrapped in `AdkApp` and exposes `async_stream_query` over the
    Agent Engine RPC surface — the transport `shared/a2a.py` uses for the
    internal hub-and-spoke mesh.

    The *open* A2A surface (Agent Card + JSON-RPC, for discovery by external
    enterprise agents — Track-3 mandate #4) is a SEPARATE deployment: the
    ADK-native `to_a2a()` Starlette app in `service/a2a_app.py`, served on
    Cloud Run. We do not use Vertex's managed `A2aAgent` wrapper — it is blocked
    by an upstream `vertexai` ↔ `a2a-sdk` version skew (see OpenItems_B4.md /
    commit f941dd6); ADK's `to_a2a()` is the framework-native path instead.
    """
    manifest = _load_manifest(name)
    agent_obj = _load_agent_object(name)

    # Late import — vertexai pulls in heavy auth/genai code. Keep CLI startup snappy.
    import vertexai
    from vertexai import agent_engines
    from vertexai.agent_engines.templates.adk import AdkApp

    project = str(manifest.env.get("GOOGLE_CLOUD_PROJECT", "ps2o-surplusas-api"))
    location = manifest.region
    display_name = manifest.displayName
    service_account = manifest.serviceAccount

    # Agent Engine uploads the `extra_packages` tarball to GCS before building the
    # container, so `vertexai.init` requires a staging bucket. Override with
    # AGENT_STAGING_BUCKET if you keep one per env; the default is project-wide.
    # Bootstrapped once with:
    #   gcloud storage buckets create gs://ps2o-surplusas-agents-staging \
    #     --project=ps2o-surplusas-api --location=us-central1 \
    #     --uniform-bucket-level-access
    # DO NOT run two `deploy_agent.py` invocations in parallel. The SDK writes
    # `agent_engine.pkl` and `dependencies.tar.gz` to a fixed path under the staging
    # bucket; concurrent deploys overwrite each other's pickle and both engines end
    # up loading whichever was uploaded last. Vertex's `staging_bucket` arg accepts
    # only a plain bucket name (no path prefix), so per-agent isolation requires
    # a separate bucket per agent — not worth the IAM/Terraform churn for a 5-agent
    # repo. Run deploys one at a time. Observed 2026-05-12.
    staging_bucket = os.environ.get(
        "AGENT_STAGING_BUCKET",
        "gs://ps2o-surplusas-agents-staging",
    )

    vertexai.init(project=project, location=location, staging_bucket=staging_bucket)

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
        "google-cloud-aiplatform>=1.153.1",
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
    #
    # IMPORTANT: paths must be RELATIVE to the current working directory.
    # The SDK calls `tarfile.add(path)` and uses the path verbatim as the
    # arcname; absolute paths get baked into the tarball (e.g. on Windows you
    # end up with `C:/Users/.../agents/__init__.py` inside the tarball, which
    # extracts to nowhere useful and breaks `import agents.pricing.tools` at
    # cloudpickle-load time on the remote engine. So we chdir to REPO_ROOT.
    os.chdir(REPO_ROOT)
    extra_packages = [
        "agents",
        "shared",
        "vendor/surplusas-pricing",
    ]

    # Wrap in AdkApp ourselves with an explicit app_name. Otherwise AdkApp's
    # set_up() falls back to GOOGLE_CLOUD_AGENT_ENGINE_ID (a numeric resource
    # id) which fails ADK's `App.name` validator (isidentifier() == False on
    # purely-numeric strings) — Pydantic ValidationError at engine startup.
    deployable: Any = AdkApp(agent=agent_obj, app_name=name, enable_tracing=True)

    # Build the env_vars dict the SDK forwards into the deployed container's
    # process environment. Plain values from manifest.env, SecretRef-backed
    # values from manifest.envFromSecretManager (resolved via SECRET_ID_MAP).
    # The agent's pydantic-settings reads these the same way it reads .env in
    # local dev, so shared/config.py needs no changes.
    env_vars: dict[str, Any] = {}
    skipped_reserved: list[str] = []
    for env_key, env_value in manifest.env.items():
        if env_key in _VERTEX_RESERVED_ENV:
            # Vertex Agent Engine injects these itself; including them is a 400.
            skipped_reserved.append(env_key)
            continue
        # YAML loader parses unquoted "true"/"8080" as bool/int — normalise
        # to str because the SDK's EnvVar proto requires a string value.
        env_vars[env_key] = str(env_value)
    for secret_env in manifest.envFromSecretManager:
        if secret_env in _VERTEX_RESERVED_ENV:
            skipped_reserved.append(secret_env)
            continue
        if secret_env not in SECRET_ID_MAP:
            raise KeyError(
                f"manifest declares envFromSecretManager={secret_env!r} but no "
                f"Secret Manager mapping is registered in scripts/deploy_agent.py "
                f"(SECRET_ID_MAP)"
            )
        env_vars[secret_env] = {
            "secret": SECRET_ID_MAP[secret_env],
            "version": "latest",
        }
    if skipped_reserved:
        print(f"skipped reserved env keys (auto-injected by Vertex): {sorted(skipped_reserved)}")

    # Forward peer Agent Engine resource names so this agent's A2A tools can
    # resolve handles at runtime. Values come from `shared.config.get_settings()`,
    # which itself reads from .env or the process environment. Empty values
    # (e.g. DISPUTE_TRIAGE_AGENT_RESOURCE in Phase 3) are skipped — those
    # routing tools will raise at call time, which is the desired behaviour.
    forwarded: list[str] = []
    if name in _PEER_RESOURCE_FORWARDS:
        from shared.config import get_settings  # late import; needs .env loaded
        settings = get_settings()
        setting_map = {
            "PRICING_AGENT_RESOURCE": settings.pricing_agent_resource,
            "ONBOARDING_AGENT_RESOURCE": settings.onboarding_agent_resource,
            "LISTING_INTAKE_AGENT_RESOURCE": settings.listing_intake_agent_resource,
            "DISPUTE_TRIAGE_AGENT_RESOURCE": settings.dispute_triage_agent_resource,
            "CONCIERGE_AGENT_RESOURCE": settings.concierge_agent_resource,
        }
        for env_key in _PEER_RESOURCE_FORWARDS[name]:
            value = setting_map.get(env_key, "")
            if value:
                env_vars[env_key] = value
                forwarded.append(env_key)
    if forwarded:
        print(f"forwarded peer resources: {sorted(forwarded)}")

    print(
        f"Deploying agent={name} display_name={display_name} "
        f"project={project} location={location} sa={service_account}"
    )
    print(f"env_vars: {sorted(env_vars.keys())}")

    remote = agent_engines.create(
        deployable,  # type: ignore[arg-type]
        display_name=display_name,
        requirements=requirements,
        extra_packages=extra_packages,
        service_account=service_account,
        env_vars=env_vars,
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
