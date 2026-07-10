"""Agent-to-Agent (A2A) client backed by in-process ADK Runners.

Post-GCP-migration transport: every peer agent runs IN-PROCESS. Each peer
gets a lazily-built, cached `google.adk.runners.Runner` wrapping the same
`agents.<peer>.agent` object the open A2A surface (`service/a2a_app.py`)
serves. Calls stream events from `runner.run_async(...)` on the current
event loop — no network hop, no ID tokens, no Agent Engine.

Calling pattern:

    runner = Runner(app_name=peer, agent=load_agent(peer),
                    session_service=InMemorySessionService(),
                    auto_create_session=True)
    async for event in runner.run_async(
        user_id=<partner_id>, session_id=<uuid>, new_message=<types.Content>,
    ):
        ...                                          # aggregate / final event

The helpers aggregate the stream client-side because ADK doesn't surface a
top-level structured envelope — tool calls and the model's final speech are
split across separate parts in separate stream events.

Trace context: `a2a_client_span` from shared/tracing.py is kept for span
continuity with the pre-migration dashboards; in-process calls parent
naturally, so no traceparent header propagation is needed.

Sessions: ADK does NOT auto-create sessions by default; the runners here opt
in via `auto_create_session=True` and callers passing `session_id=None` get a
fresh uuid per call (matching the old per-call Vertex semantics).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import uuid
from typing import Any, Literal, get_args

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from shared.tracing import a2a_client_span, set_attrs

Peer = Literal["concierge", "pricing", "onboarding", "listing_intake", "dispute_triage"]

# The full internal mesh — also the set service/a2a_app.py can publish
# over the open A2A protocol.
VALID_AGENTS: tuple[str, ...] = get_args(Peer)

_runner_cache: dict[str, Runner] = {}
_cache_lock = asyncio.Lock()


def load_agent(name: str) -> Any:
    """Import `agents.<name>.agent` and return its `agent` object.

    LAZY importlib on purpose: `agents/*/tools.py` import this module at
    module import; a module-level `agents.*` import here would be a cycle.
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


def _build_runner(peer: Peer) -> Runner:
    # ponytail: InMemorySessionService = single-machine ceiling; swap to a
    # DB-backed SessionService when scaling past one Fly machine.
    return Runner(
        app_name=peer,
        agent=load_agent(peer),
        session_service=InMemorySessionService(),  # type: ignore[no-untyped-call]
        auto_create_session=True,
    )


async def _get_runner(peer: Peer) -> Runner:
    """Build and cache the in-process Runner for `peer`."""
    runner = _runner_cache.get(peer)
    if runner is not None:
        return runner

    async with _cache_lock:
        if peer not in _runner_cache:
            # Agent-module import + Runner construction is slow on cold
            # paths; offload so the event loop stays responsive.
            _runner_cache[peer] = await asyncio.to_thread(_build_runner, peer)
        return _runner_cache[peer]


def _user_content(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


async def _drop_ephemeral_session(
    runner: Runner, peer: Peer, partner_id: str, sid: str,
) -> None:
    """Delete a per-call session so InMemorySessionService doesn't grow forever.

    Only called for sessions this module minted itself (caller passed
    session_id=None). Cleanup must never mask the real call outcome.
    """
    with contextlib.suppress(Exception):
        await runner.session_service.delete_session(
            app_name=peer, user_id=partner_id, session_id=sid,
        )


async def call_peer_agent(
    peer: Peer,
    mode: str,
    input: dict[str, Any],
    partner_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Invoke a peer agent and return the final stream event as a dict.

    The `mode` + `input` are packed into a structured envelope (JSON text
    part) so the peer agent's prompt + tools can dispatch on `mode`.
    (SurplusAS-API-2.0's `AgentRequest.mode` discriminator pattern,
    preserved at the message level.)

    `partner_id` becomes the Runner's `user_id` — our multi-tenant
    identity anchor.

    Returns the final event, dumped JSON-safe (`exclude_none=True` so
    absent parts don't surface as None-valued keys). Raises if the stream
    produced zero events.
    """
    runner = await _get_runner(peer)
    message = _user_content(json.dumps({"mode": mode, "input": input}))
    sid = session_id or uuid.uuid4().hex

    final_event: Any = None
    event_count = 0

    with a2a_client_span(peer, {}) as span:
        set_attrs(span, **{"a2a.mode": mode, "a2a.partner_id": partner_id})

        try:
            async for event in runner.run_async(
                user_id=partner_id,
                session_id=sid,
                new_message=message,
            ):
                event_count += 1
                final_event = event
        finally:
            if session_id is None:
                await _drop_ephemeral_session(runner, peer, partner_id, sid)

        set_attrs(span, **{"a2a.event_count": event_count})

    if final_event is None:
        raise RuntimeError(
            f"A2A call to {peer!r} (mode={mode!r}) yielded zero events."
        )
    result: dict[str, Any] = final_event.model_dump(mode="json", exclude_none=True)
    return result


async def aggregate_peer_stream(
    peer: Peer,
    user_message: str,
    partner_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Send a plain-string message to a peer and aggregate its streamed events.

    Unlike `call_peer_agent` (which packs an `{mode, input}` envelope and
    returns only the FINAL event verbatim), this helper:
      - sends the user's raw text the way ADK's Runner expects;
      - walks the full stream;
      - returns `{narration, tool_calls, event_count}` where `tool_calls` is
        the list of `function_call` parts each paired with their matching
        `function_response.response` (None if the agent didn't emit one).

    Used by:
      - `call_concierge` (gateway façade, derives `specialist_called`)
      - routing tools in `agents/concierge/tools.py` (relay the specialist's
        narration up to the Concierge model)
      - `agents/listing_intake/tools.py:request_anchor_price` (relay Pricing's
        recommendation up to the Listing Intake model)
      - `agents/dispute_triage/tools.py:request_reprice` (replay lateral edge)
    """
    runner = await _get_runner(peer)
    sid = session_id or uuid.uuid4().hex
    last_text = ""
    tool_calls: list[dict[str, Any]] = []
    event_count = 0

    with a2a_client_span(peer, {}) as span:
        set_attrs(span, **{"a2a.mode": "user_message",
                           "a2a.partner_id": partner_id})

        try:
            async for event in runner.run_async(
                user_id=partner_id,
                session_id=sid,
                new_message=_user_content(user_message),
            ):
                event_count += 1
                parts = event.content.parts if event.content and event.content.parts else []
                for part in parts:
                    if part.text:
                        last_text = part.text
                    if part.function_call is not None:
                        tool_calls.append({
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args or {}),
                            "response": None,
                        })
                    if part.function_response is not None:
                        fr = part.function_response
                        for tc in reversed(tool_calls):
                            if tc["name"] == fr.name and tc["response"] is None:
                                tc["response"] = fr.response
                                break
        finally:
            if session_id is None:
                await _drop_ephemeral_session(runner, peer, partner_id, sid)

        set_attrs(span, **{"a2a.event_count": event_count,
                           "a2a.tool_call_count": len(tool_calls)})

    return {
        "narration": last_text,
        "tool_calls": tool_calls,
        "event_count": event_count,
    }


async def call_concierge(
    user_message: str,
    partner_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Gateway façade: send a merchant message and derive ConciergeResponse fields.

    Returns `{narration, specialist_called, specialist_payload, event_count}`.
    `specialist_called` is the suffix of any `route_to_*` tool the Concierge
    model invoked; `specialist_payload` is that tool's `function_response`
    (which, after the routing tools are themselves aggregated, is a
    `{narration, status, ...}` dict the model relayed verbatim).
    """
    agg = await aggregate_peer_stream(
        "concierge", user_message, partner_id, session_id=session_id,
    )

    specialist_called: str | None = None
    specialist_payload: dict[str, Any] = {}
    for tc in agg["tool_calls"]:
        name = tc.get("name") or ""
        if name.startswith("route_to_"):
            specialist_called = name[len("route_to_"):]
            resp = tc.get("response")
            if isinstance(resp, dict):
                specialist_payload = resp
            break

    return {
        "narration": agg["narration"],
        "specialist_called": specialist_called,
        "specialist_payload": specialist_payload,
        "event_count": agg["event_count"],
    }
