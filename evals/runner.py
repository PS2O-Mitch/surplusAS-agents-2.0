"""Eval runner — local or remote, threshold gate.

Local mode:
    Calls the deterministic pricing engine in-process with the inline
    anchor + coefficients shipped on each golden case. No DB, no Vertex,
    no Gemini. Used in CI; the only thing CI gates against is whether the
    formula's output drifted from the frozen golden.

Remote mode:
    Calls the deployed Agent Engine via the SDK (`vertexai.agent_engines`)
    using the same `mode/input` envelope `shared.a2a` uses. The deployed
    Pricing agent will resolve coefficients + anchor from Cloud SQL, so
    the goldens' inline coefficients/anchor are ignored.

Threshold gate: pass-rate (cases-passed-all-metrics / total) ≥ threshold.

CLI:
    python -m evals.runner --agent pricing --threshold 0.85 --mode local
    python -m evals.runner --agent pricing --threshold 0.85 --mode remote
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.metrics import (
    PRESSURE_KEYS,
    pressure_diff_l1,
    price_band_match,
    route_correctness,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    metrics: dict[str, tuple[bool, float]]
    notes: str = ""


def _load_golden(agent: str) -> list[dict[str, Any]]:
    path = GOLDEN_DIR / f"{agent}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"No golden file at {path}")
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(json.loads(line))
    return cases


# ---------------------------------------------------------------------------
# Pricing — local
# ---------------------------------------------------------------------------


def _run_pricing_local(case: dict[str, Any]) -> CaseResult:
    """Invoke the pure-Python engine with the case's inline anchor + coeffs."""
    from shared.pricing_intel import (
        Coefficients,
        PiecewiseCurve,
        PricingInput,
        recommend,
    )

    inp = PricingInput.model_validate(case["input"])
    coeffs_raw = case["coefficients"]
    coeffs = Coefficients(
        category=inp.category,
        region=coeffs_raw.get("region", inp.region),
        version=int(coeffs_raw["version"]),
        base_discount=float(coeffs_raw["base_discount"]),
        expiry_curve=PiecewiseCurve(breakpoints=coeffs_raw["expiry_curve"]),
        inventory_curve=PiecewiseCurve(breakpoints=coeffs_raw["inventory_curve"]),
        time_of_day_curve=PiecewiseCurve(
            breakpoints=coeffs_raw["time_of_day_curve"]
        ),
        source=coeffs_raw.get("source", "test-seed"),
    )
    anchor = case["anchor"]
    rec = recommend(
        inp=inp,
        coeffs=coeffs,
        anchor_p50=float(anchor["p50"]),
        anchor_source=anchor.get("source", "test"),
        anchor_region=anchor.get("region", inp.region),
    )

    actual_pressures = rec.applied_pressures.model_dump()
    metrics = {
        "pressure_diff_l1": pressure_diff_l1(
            case["expected_pressures"], actual_pressures
        ),
        "price_band_match": price_band_match(
            float(case["expected_price"]), rec.recommended_price
        ),
    }
    formula_ok = rec.formula_version == case.get("expected_formula_version", "v1")
    metrics["formula_version_match"] = (formula_ok, 1.0 if formula_ok else 0.0)
    passed = all(m[0] for m in metrics.values())
    notes = (
        f"price={rec.recommended_price:.2f} expected={float(case['expected_price']):.2f} "
        f"pressures_l1={metrics['pressure_diff_l1'][1]:.4f}"
    )
    return CaseResult(case_id=case["case_id"], passed=passed, metrics=metrics, notes=notes)


# ---------------------------------------------------------------------------
# Concierge — local (route_correctness only)
# ---------------------------------------------------------------------------


def _run_concierge_local(case: dict[str, Any]) -> CaseResult:
    """Pseudo-eval: assert the case carries the routing intent we expect.

    Concierge route eval needs Gemini to actually run, which the canonical
    local mode skips for cost reasons. We treat each case as 'passed' if
    the case file is internally consistent (expected specialist is one of
    the four valid peers); the real route check runs in remote mode.
    """
    valid = {"pricing", "onboarding", "listing_intake", "dispute_triage"}
    expected = case.get("expected_specialist")
    ok = expected in valid
    return CaseResult(
        case_id=case["case_id"],
        passed=ok,
        metrics={"route_correctness": route_correctness(expected or "", expected)},
        notes=f"expected_specialist={expected}",
    )


# ---------------------------------------------------------------------------
# Remote (any agent)
# ---------------------------------------------------------------------------


async def _run_remote(agent: str, case: dict[str, Any]) -> CaseResult:
    """Deployed-agent eval — calls the live Agent Engine via the SDK.

    Uses the same envelope as `shared.a2a.call_peer_agent` (mode/input dict).
    Parses the final stream event's payload to extract pressures + price.
    """
    from shared import a2a

    final_event = await a2a.call_peer_agent(
        peer=agent,  # type: ignore[arg-type]
        mode=case.get("mode", "price_listing"),
        input=case["input"],
        partner_id=case.get("partner_id", "sk_eval"),
    )
    payload = _extract_payload(final_event)
    metrics: dict[str, tuple[bool, float]] = {}

    if agent == "pricing":
        rec = payload.get("recommendation", {})
        metrics["price_band_match"] = price_band_match(
            float(case["expected_price"]),
            float(rec.get("recommended_price", 0.0)),
        )
        metrics["pressure_diff_l1"] = pressure_diff_l1(
            case["expected_pressures"],
            rec.get("applied_pressures", {}),
        )
    elif agent == "concierge":
        metrics["route_correctness"] = route_correctness(
            case.get("expected_specialist") or "",
            payload.get("specialist_called"),
        )

    passed = all(m[0] for m in metrics.values()) if metrics else False
    return CaseResult(case_id=case["case_id"], passed=passed, metrics=metrics)


def _extract_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Best-effort: peel the agent's final structured payload out of an ADK event.

    ADK final events typically nest the model's JSON tool result under
    `content.parts[0].text` or `actions.transfer_to_agent`. We try the most
    common shapes and fall back to the raw event so the caller's `.get`s
    don't crash.
    """
    if "recommendation" in event:
        return event
    parts = event.get("content", {}).get("parts", []) or []
    for part in parts:
        text = part.get("text", "")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            continue
    return event


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def _summarise(results: list[CaseResult]) -> tuple[float, str]:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    pass_rate = passed / total if total else 0.0
    lines = [f"  {r.case_id}: {'PASS' if r.passed else 'FAIL'}  {r.notes}" for r in results]
    return pass_rate, "\n".join(lines)


def _run_local_for(agent: str, cases: list[dict[str, Any]]) -> list[CaseResult]:
    if agent == "pricing":
        return [_run_pricing_local(c) for c in cases]
    if agent == "concierge":
        return [_run_concierge_local(c) for c in cases]
    # Onboarding / listing_intake / dispute_triage local mode is structural
    # only: we just assert the case has an `input` block and an
    # `expected_status`. Real semantic evals run in remote mode.
    return [
        CaseResult(
            case_id=c["case_id"],
            passed=("input" in c and "expected_status" in c),
            metrics={},
            notes=f"local-stub status={c.get('expected_status')}",
        )
        for c in cases
    ]


async def _run_remote_for(agent: str, cases: list[dict[str, Any]]) -> list[CaseResult]:
    return [await _run_remote(agent, c) for c in cases]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SurplusAS eval runner")
    parser.add_argument(
        "--agent",
        required=True,
        choices=["pricing", "onboarding", "listing_intake", "concierge", "dispute_triage"],
    )
    parser.add_argument("--threshold", type=float, default=0.85)
    parser.add_argument("--mode", choices=["local", "remote"], default="local")
    args = parser.parse_args(argv)

    cases = _load_golden(args.agent)
    if not cases:
        print(f"No cases found for agent={args.agent}", file=sys.stderr)
        return 1

    results = (
        _run_local_for(args.agent, cases)
        if args.mode == "local"
        else asyncio.run(_run_remote_for(args.agent, cases))
    )
    pass_rate, lines = _summarise(results)

    print(lines)
    print()
    print(f"agent={args.agent} mode={args.mode} cases={len(cases)} "
          f"pass_rate={pass_rate:.3f} threshold={args.threshold}")

    # Per-pressure-key contributors are a useful debug breadcrumb.
    if args.agent == "pricing" and any(
        not r.passed for r in results
    ):
        print()
        print("Worst pressure-key offenders (per case L1):")
        for r in sorted(results, key=lambda x: -x.metrics.get("pressure_diff_l1", (False, 0))[1])[:5]:
            print(f"  {r.case_id}: l1={r.metrics.get('pressure_diff_l1', (False, 0))[1]:.4f}")
        print(f"  pressure keys checked: {PRESSURE_KEYS}")

    return 0 if pass_rate >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
