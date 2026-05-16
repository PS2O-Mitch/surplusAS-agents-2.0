"""Agent-to-Agent (A2A) client backed by the Vertex AI Agent Engine SDK.

Calling pattern (post-2026-04-30 SDK pivot):

    from vertexai import agent_engines
    handle = agent_engines.get(resource_name)        # cached per peer
    async for event in handle.async_stream_query(
        message=<dict|str>, user_id=<partner_id>, session_id=<optional>,
    ):
        ...                                          # collect final event

`async_stream_query` is the canonical async path on `AdkApp` in
`google-cloud-aiplatform>=1.149`. `query`/`async_query` exist as mixin
methods (`Queryable` / `AsyncQueryable`) but are NOT exposed on `AdkApp`,
which is the framework all five SurplusAS agents deploy as. `stream_query`
is deprecated. See SDK probe in commit history.

This helper aggregates the stream into a single final dict — most A2A
calls are request/response (Concierge → Pricing for a recommendation,
etc.). A streaming variant can be added later if Concierge wants to relay
intermediate events to the gateway as SSE.

Trace context: the existing `a2a_client_span` from shared/tracing.py is
re-used for span correctness; the W3C `traceparent` is injected into the
`run_config` map so the deployed agent can pick it up server-side once
the ADK plumbing supports it (today the SDK does not surface a per-call
header dict, so the trace continuity is best-effort cross-process).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Literal

from vertexai import agent_engines

from shared.config import get_settings
from shared.tracing import a2a_client_span, set_attrs

if TYPE_CHECKING:
    from vertexai.agent_engines import AgentEngine

logger = logging.getLogger("surplusas.a2a")

Peer = Literal["concierge", "pricing", "onboarding", "listing_intake", "dispute_triage"]

_handle_cache: dict[str, AgentEngine] = {}
_cache_lock = asyncio.Lock()


def _resolve_resource(peer: Peer) -> str:
    """Map a peer name to its Agent Engine resource name from settings."""
    settings = get_settings()
    resource = {
        "concierge": settings.concierge_agent_resource,
        "pricing": settings.pricing_agent_resource,
        "onboarding": settings.onboarding_agent_resource,
        "listing_intake": settings.listing_intake_agent_resource,
        "dispute_triage": settings.dispute_triage_agent_resource,
    }[peer]
    if not resource:
        raise RuntimeError(
            f"A2A peer {peer!r} has no resource configured "
            f"(set {peer.upper()}_AGENT_RESOURCE)."
        )
    return resource


async def _get_handle(peer: Peer) -> AgentEngine:
    """Resolve and cache the AgentEngine handle for `peer`."""
    resource = _resolve_resource(peer)
    cached = _handle_cache.get(resource)
    if cached is not None:
        return cached

    async with _cache_lock:
        cached = _handle_cache.get(resource)
        if cached is not None:
            return cached
        # agent_engines.get is synchronous metadata fetch; offload to thread
        # pool to keep the event loop responsive on cold paths.
        handle = await asyncio.to_thread(agent_engines.get, resource)
        _handle_cache[resource] = handle
        return handle


async def call_peer_agent(
    peer: Peer,
    mode: str,
    input: dict[str, Any],
    partner_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke a peer agent and return the final stream event.

    The `mode` + `input` are packed into a structured `message` dict so the
    deployed agent's prompt + tools can dispatch on `mode`. (SurplusAS-API-2.0's
    `AgentRequest.mode` discriminator pattern, preserved at the message level.)

    `partner_id` becomes the SDK's `user_id` — required by `async_stream_query`
    and our multi-tenant identity anchor.

    Returns the final event from the stream (typically the agent's terminal
    response with tool outputs). Raises if the stream produced zero events.
    """
    handle = await _get_handle(peer)
    message = {"mode": mode, "input": input}
    run_config: dict[str, Any] = {}

    final_event: dict[str, Any] | None = None
    event_count = 0

    headers: dict[str, str] = {}  # propagate fills it; mirrored into run_config
    with a2a_client_span(peer, headers) as span:
        run_config["traceparent"] = headers.get("traceparent")
        run_config = {k: v for k, v in run_config.items() if v is not None}
        set_attrs(span, **{"a2a.mode": mode, "a2a.partner_id": partner_id})

        # `async_stream_query` is registered dynamically on the AgentEngine
        # handle at runtime (it lives on AdkApp's `register_operations`); the
        # static type from `agent_engines.get` is the bare `AgentEngine`,
        # which doesn't surface this method. The runtime presence is part
        # of the contract every SurplusAS agent deploys with.
        async for event in handle.async_stream_query(  # type: ignore[attr-defined]
            message=message,
            user_id=partner_id,
            session_id=session_id,
            run_config=run_config or None,
        ):
            event_count += 1
            final_event = event

        set_attrs(span, **{"a2a.event_count": event_count})

    if final_event is None:
        raise RuntimeError(
            f"A2A call to {peer!r} (mode={mode!r}) yielded zero events."
        )
    return final_event


async def call_concierge(
    user_message: str,
    partner_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Send a merchant message to Concierge and aggregate the streamed response.

    Concierge is the only externally-addressable agent. Unlike `call_peer_agent`
    (which packs an internal `{mode, input}` envelope for inter-agent dispatch),
    this helper sends the user's raw text the way ADK's Runner expects, then
    walks the entire stream to reconstruct the gateway's `ConciergeResponse`
    contract:

      - `narration`         — the model's final text reply.
      - `specialist_called` — the peer whose `route_to_*` tool was invoked
                              (None when the model returned the out-of-scope
                              redirect without calling a tool).
      - `specialist_payload`— the raw response from that routing tool, which
                              for now is whatever the specialist's stream
                              produced. Empty `{}` when no specialist was called.

    The aggregation has to live client-side because ADK doesn't publish
    `specialist_called` as a first-class top-level field — tool calls are
    scattered across `function_call` / `function_response` parts in the
    earlier events of the stream.
    """
    handle = await _get_handle("concierge")

    last_text = ""
    tool_calls: list[dict[str, Any]] = []
    event_count = 0

    headers: dict[str, str] = {}
    with a2a_client_span("concierge", headers) as span:
        set_attrs(span, **{"a2a.mode": "user_message",
                           "a2a.partner_id": partner_id})

        async for event in handle.async_stream_query(  # type: ignore[attr-defined]
            message=user_message,
            user_id=partner_id,
            session_id=session_id,
        ):
            event_count += 1
            for part in (event.get("content", {}).get("parts") or []):
                if part.get("text"):
                    last_text = part["text"]
                if "function_call" in part:
                    fc = part.get("function_call") or {}
                    tool_calls.append({
                        "name": fc.get("name"),
                        "args": fc.get("args", {}),
                        "response": None,
                    })
                if "function_response" in part:
                    fr = part.get("function_response") or {}
                    for tc in reversed(tool_calls):
                        if tc["name"] == fr.get("name") and tc["response"] is None:
                            tc["response"] = fr.get("response")
                            break

        set_attrs(span, **{"a2a.event_count": event_count,
                           "a2a.tool_call_count": len(tool_calls)})

    specialist_called: str | None = None
    specialist_payload: dict[str, Any] = {}
    for tc in tool_calls:
        name = tc.get("name") or ""
        if name.startswith("route_to_"):
            specialist_called = name[len("route_to_"):]
            resp = tc.get("response")
            if isinstance(resp, dict):
                specialist_payload = resp
            break

    return {
        "narration": last_text,
        "specialist_called": specialist_called,
        "specialist_payload": specialist_payload,
        "event_count": event_count,
    }
