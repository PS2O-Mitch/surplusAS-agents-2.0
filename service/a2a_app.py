"""Standard, open Agent-to-Agent (A2A) server surface for SurplusAS agents.

This is the contest's Track-3 **A2A Interoperability** surface: each agent is
wrapped with ADK's native `to_a2a()` adapter, which exposes

  - a discoverable **Agent Card** at `/.well-known/agent-card.json`, and
  - a **JSON-RPC 2.0** endpoint (`message/send`, `message/stream`, `tasks/get`, …)
    at `/`,

over plain HTTP. Any A2A-speaking enterprise agent — regardless of framework
(ADK, LangGraph, CrewAI, …) — can read the card and call the agent. This is the
*open* protocol; the internal mesh in `shared/a2a.py` runs the same agent
objects in-process via ADK Runners.

ADK's `to_a2a()` is the framework-native adapter; it depends only on
`google-adk` + `a2a-sdk` (pinned to ADK's supported `>=0.3.4,<0.4.0` range)
and runs as an ordinary Starlette ASGI app on any container host.

Run one agent's A2A surface locally:

    A2A_AGENT=pricing uv run uvicorn service.a2a_app:app --port 8080

or build a specific app in code via `build_a2a_app("pricing", ...)`.
When hosting it, set `A2A_AGENT`, and `A2A_PUBLIC_HOST`/`A2A_PUBLIC_PROTOCOL`/
`A2A_PUBLIC_PORT` to the service's public URL so the advertised card URL is
reachable by external callers.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from shared.a2a import VALID_AGENTS, load_agent

if TYPE_CHECKING:
    from starlette.applications import Starlette

__all__ = ["VALID_AGENTS", "build_a2a_app", "load_agent"]


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

    agent = load_agent(name)
    starlette_app: Starlette = to_a2a(agent, host=host, port=port, protocol=protocol)
    return starlette_app


def _build_app_from_env() -> Starlette:
    """uvicorn entrypoint factory driven by environment.

    `A2A_AGENT` selects the agent (default: pricing). `A2A_PUBLIC_HOST`,
    `A2A_PUBLIC_PROTOCOL`, and `A2A_PUBLIC_PORT` set the externally-reachable
    URL advertised in the card; point these at the hosting service's URL.
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
