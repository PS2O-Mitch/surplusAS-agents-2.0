"""Integration test: Dispute Triage -> Pricing (lateral edge, in-process).

The Pricing runner is mocked at the transport boundary
(`shared.a2a._get_runner`), so this runs in CI without a Gemini key. What
it verifies:

1. `request_reprice` sends a **plain-string** request to Pricing (the
   shape ADK Runner expects) — not the legacy `{mode, input}` envelope.
2. The request names the `replay_recommendation` tool, the original
   recommendation_id, and the partner_id.
3. The aggregator extracts the structured `replay_recommendation` tool
   response (new_recommendation_id, recommended_price, applied_pressures)
   from the stream's `function_response` part.
4. `request_reprice` returns `{status: ok, new_recommendation_id, new_price,
   new_pressures, narration}` for the Dispute Triage model to use directly.

Lateral edge per CLAUDE.md ("Dispute Triage -> Pricing") — pinned here so a
regression in the wire shape fails CI even before a remote eval is run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from google.adk.events import Event
from google.genai import types

from agents.dispute_triage.tools import request_reprice
from shared import a2a

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


def _ev(*parts: types.Part) -> Event:
    return Event(author="pricing", content=types.Content(role="model", parts=list(parts)))


class FakeRunner:
    def __init__(self, events: list[Event]) -> None:
        self.events = events
        self.captured: dict[str, Any] = {}

    async def run_async(self, **kwargs: Any) -> AsyncIterator[Event]:
        self.captured.update(kwargs)
        for ev in self.events:
            yield ev


def _patch_runner(monkeypatch: pytest.MonkeyPatch, fake: FakeRunner) -> None:
    async def _get(_peer: str) -> FakeRunner:
        return fake

    monkeypatch.setattr(a2a, "_get_runner", _get)


async def test_dispute_to_pricing_wire_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pricing must receive a plain-string `replay_recommendation` request."""
    fake = FakeRunner([
        _ev(types.Part(function_call=types.FunctionCall(
            name="replay_recommendation",
            args={"recommendation_id": "11111111-1111-1111-1111-111111111111",
                  "partner_id": "sk_demo_surplus_2026"},
        ))),
        _ev(types.Part(function_response=types.FunctionResponse(
            name="replay_recommendation",
            response={
                "status": "ok",
                "recommendation": {
                    "recommendation_id":
                        "55555555-5555-5555-5555-555555555555",
                    "recommended_price": 6.50,
                    "applied_pressures": {
                        "base": 0.10, "expiry": 0.21, "inventory": 0.05,
                        "time_of_day": 0.05, "merchant_floor": 0.10,
                        "clamped_to_floor": False, "clamped_to_retail": False,
                    },
                    "formula_version": "v1",
                    "replay_of": "11111111-1111-1111-1111-111111111111",
                },
            },
        ))),
        _ev(types.Part(text="Replayed under fresh coefficients: new price $6.50, "
                            "expiry pressure now 0.21.")),
    ])
    _patch_runner(monkeypatch, fake)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo_surplus_2026",
    )

    # Pricing receives a STRING message (not a dict envelope).
    msg = fake.captured["new_message"].parts[0].text
    assert isinstance(msg, str)
    assert "replay_recommendation" in msg
    assert "11111111-1111-1111-1111-111111111111" in msg
    assert "sk_demo_surplus_2026" in msg
    assert fake.captured["user_id"] == "sk_demo_surplus_2026"

    # Structured fields extracted from the function_response part:
    assert out["status"] == "ok"
    assert out["new_recommendation_id"] == "55555555-5555-5555-5555-555555555555"
    assert out["new_price"] == 6.50
    assert out["new_pressures"]["expiry"] == 0.21
    assert out["new_pressures"]["clamped_to_floor"] is False
    assert "6.50" in out["narration"]


async def test_dispute_reprice_error_when_pricing_skips_replay_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Pricing narrates without invoking `replay_recommendation`, we surface
    an error rather than fabricating a recommendation_id."""
    fake = FakeRunner([
        _ev(types.Part(text="I couldn't find a prior recommendation matching that id.")),
    ])
    _patch_runner(monkeypatch, fake)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo",
    )
    assert out["status"] == "error"
    assert "replay_recommendation" in out["error"]
