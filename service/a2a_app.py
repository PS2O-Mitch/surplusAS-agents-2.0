"""Standard, open Agent-to-Agent (A2A) server surface for SurplusAS agents.

This is the contest's Track-3 **A2A Interoperability** surface: each agent is
wrapped with ADK's native `to_a2a()` adapter, which exposes

  - a discoverable **Agent Card** at `/.well-known/agent-card.json`, and
  - a **JSON-RPC 2.0** endpoint (`message/send`, `message/stream`, `tasks/get`, …)
    at `/`,

over plain HTTP. Any A2A-speaking enterprise agent — regardless of framework
(ADK, LangGraph, CrewAI, …) — can read the card and call the agent. This is the
*open* protocol, distinct from the proprietary Vertex Agent Engine
`async_stream_query` transport that `shared/a2a.py` uses for the internal mesh.

Why `to_a2a()` and not the Vertex-managed `A2aAgent` wrapper: the managed
client path is blocked by an upstream `vertexai` ↔ `a2a-sdk` version skew (see
commit f941dd6 / OpenItems_B4.md). ADK's `to_a2a()` is the framework-native
adapter; it depends only on `google-adk` + `a2a-sdk` (pinned to ADK's supported
`>=0.3.4,<0.4.0` range) and runs as an ordinary Starlette ASGI app — locally or
on Cloud Run — with no dependency on the managed Agent Engine client.

Run one agent's A2A surface locally:

    A2A_AGENT=pricing uv run uvicorn service.a2a_app:app --port 8080

or build a specific app in code via `build_a2a_app("pricing", ...)`.
On Cloud Run, set `A2A_AGENT`, and `A2A_PUBLIC_HOST`/`A2A_PUBLIC_PROTOCOL`/
`A2A_PUBLIC_PORT` to the service's public URL so the advertised card URL is
reachable by external callers.
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette

# The full internal mesh — every agent can be published as an A2A server.
VALID_AGENTS: tuple[str, ...] = (
    "concierge",
    "pricing",
    "onboarding",
    "listing_intake",
    "dispute_triage",
)


def _load_agent(name: str) -> object:
    """Import `agents.<name>.agent` and return its `agent` object.

    Mirrors `scripts.deploy_agent._load_agent_object` but kept local so the
    A2A surface has no dependency on the deploy CLI.
    """
    if name not in VALID_AGENTS:
        raise ValueError(
            f"unknown agent {name!r}; expected one of {VALID_AGENTS}"
        )
    mod = importlib.import_module(f"agents.{name}.agent")
    if not hasattr(mod, "agent"):
        raise AttributeError(
            f"agents.{name}.agent has no `agent` attribute — "
            f"expected a google.adk.Agent(...) instance."
        )
    return mod.agent


def build_a2a_app(
    name: str,
    *,
    host: str = "0.0.0.0",  # noqa: S104 — bind host for the container; card URL host is separate
    port: int = 8080,
    protocol: str = "https",
) -> Starlette:
    """Build a standard A2A Starlette app exposing `name` over the open protocol.

    `host`/`port`/`protocol` set the JSON-RPC URL advertised in the Agent Card
    (what external callers dial), not the uvicorn bind address. The Agent Card
    and its routes are registered in the app's lifespan startup hook.
    """
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    agent = _load_agent(name)
    starlette_app: Starlette = to_a2a(agent, host=host, port=port, protocol=protocol)
    return starlette_app


def _build_app_from_env() -> Starlette:
    """Cloud Run / uvicorn entrypoint factory driven by environment.

    `A2A_AGENT` selects the agent (default: pricing). `A2A_PUBLIC_HOST`,
    `A2A_PUBLIC_PROTOCOL`, and `A2A_PUBLIC_PORT` set the externally-reachable
    URL advertised in the card; on Cloud Run point these at the service URL.
    """
    name = os.environ.get("A2A_AGENT", "pricing")
    host = os.environ.get("A2A_PUBLIC_HOST", "0.0.0.0")  # noqa: S104
    protocol = os.environ.get("A2A_PUBLIC_PROTOCOL", "https")
    port = int(os.environ.get("A2A_PUBLIC_PORT", os.environ.get("PORT", "8080")))
    return build_a2a_app(name, host=host, port=port, protocol=protocol)


def __getattr__(attr: str) -> object:
    """PEP 562 lazy module attribute: build `app` only when actually served.

    Lets `uvicorn service.a2a_app:app` work while keeping `import service.a2a_app`
    cheap and side-effect-free for tests (no agent is constructed until needed).
    """
    if attr == "app":
        return _build_app_from_env()
    raise AttributeError(f"module {__name__!r} has no attribute {attr!r}")
