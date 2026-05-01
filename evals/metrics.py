"""Eval metrics for the SurplusAS agents.

Three metrics, each returning a `(passed: bool, score: float)` tuple. The
runner aggregates `passed` into the pass-rate threshold check and `score`
into per-case telemetry.

- `pressure_diff_l1` — sum of absolute deltas between expected and actual
  applied_pressures keys. Threshold default 0.02 (i.e. all pressures
  match within 2 percentage points combined). Pricing-only.
- `price_band_match` — actual price within ± `tolerance` (default $0.25)
  of expected. Pricing-only.
- `route_correctness` — for the Concierge: did the trace pick the
  expected specialist? Concierge-only.
"""

from __future__ import annotations

from typing import Any

PRESSURE_KEYS = ("base", "expiry", "inventory", "time_of_day", "merchant_floor")


def pressure_diff_l1(
    expected: dict[str, float],
    actual: dict[str, Any],
    *,
    threshold: float = 0.02,
) -> tuple[bool, float]:
    """L1 distance across the 5 numeric pressure keys."""
    total = 0.0
    for key in PRESSURE_KEYS:
        e = float(expected.get(key, 0.0))
        a = float(actual.get(key, 0.0))
        total += abs(e - a)
    return total <= threshold, total


def price_band_match(
    expected_price: float,
    actual_price: float,
    *,
    tolerance: float = 0.25,
) -> tuple[bool, float]:
    """Pass when actual is within ± `tolerance` of expected. Score = abs delta."""
    delta = abs(actual_price - expected_price)
    return delta <= tolerance, delta


def route_correctness(
    expected_specialist: str,
    actual_specialist: str | None,
) -> tuple[bool, float]:
    """1.0 when names match exactly (case-sensitive), else 0.0."""
    ok = expected_specialist == (actual_specialist or "")
    return ok, 1.0 if ok else 0.0
