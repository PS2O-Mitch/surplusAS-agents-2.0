"""Regenerate `evals/golden/pricing.jsonl` from a curated list of fixtures.

Run after a deliberate change to `pricing_engine.formula.recommend()`. The
goldens are the regression contract: a formula change here must be
reviewed and re-frozen, not silently accepted.

Each fixture provides (input, anchor, coefficients). We invoke the engine
and freeze the resulting price + pressures. The runner's `pressure_diff_l1`
+ `price_band_match` metrics then assert any future formula run produces
the same output.

    python -m evals.golden._gen_pricing
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.pricing_intel import (
    Coefficients,
    PiecewiseCurve,
    PricingInput,
    recommend,
)

OUT_PATH = Path(__file__).resolve().parent / "pricing.jsonl"

# Reusable curve shapes. Tuned so cases meaningfully exercise each pressure.
EXPIRY_AGGRESSIVE = [(0.0, 0.50), (4.0, 0.30), (12.0, 0.10), (48.0, 0.0)]
EXPIRY_MILD = [(0.0, 0.20), (12.0, 0.10), (48.0, 0.0)]
EXPIRY_FLAT = [(0.0, 0.0), (48.0, 0.0)]
INV_RAMP = [(1.0, 0.0), (10.0, 0.05), (50.0, 0.10)]
INV_FLAT = [(1.0, 0.0), (100.0, 0.0)]
TOD_DINNER_PEAK = [(0.0, 0.0), (11.0, 0.0), (14.0, 0.05), (18.0, 0.10), (21.0, 0.15), (23.0, 0.05)]
TOD_FLAT = [(0.0, 0.0), (23.0, 0.0)]


def _curve(bps: list[tuple[float, float]]) -> PiecewiseCurve:
    return PiecewiseCurve(breakpoints=bps)


def _fixture(
    case_id: str,
    *,
    category: str,
    region: str = "US-FL",
    units: int = 1,
    retail_value: float = 12.0,
    hours_until_expiry: float = 4.0,
    now_hour: int = 18,
    merchant_floor_pct: float = 0.10,
    anchor_p50: float = 11.50,
    anchor_source: str = "apify",
    anchor_region: str = "US-FL",
    base_discount: float = 0.10,
    expiry_curve: list[tuple[float, float]] | None = None,
    inventory_curve: list[tuple[float, float]] | None = None,
    time_of_day_curve: list[tuple[float, float]] | None = None,
    coefficients_version: int = 1,
) -> dict[str, Any]:
    inp = PricingInput(
        category=category,
        region=region,
        units=units,
        retail_value=retail_value,
        hours_until_expiry=hours_until_expiry,
        now_hour=now_hour,
        merchant_floor_pct=merchant_floor_pct,
    )
    coeffs = Coefficients(
        category=category,
        region=region,
        version=coefficients_version,
        base_discount=base_discount,
        expiry_curve=_curve(expiry_curve or EXPIRY_FLAT),
        inventory_curve=_curve(inventory_curve or INV_FLAT),
        time_of_day_curve=_curve(time_of_day_curve or TOD_FLAT),
        source="seed-v1",
    )
    rec = recommend(
        inp=inp,
        coeffs=coeffs,
        anchor_p50=anchor_p50,
        anchor_source=anchor_source,
        anchor_region=anchor_region,
    )

    # The .jsonl needs a "coefficients" block the runner can reconstruct.
    # We strip the curves back to their breakpoint lists for JSON-friendly storage.
    coeffs_json = {
        "version": coefficients_version,
        "region": region,
        "source": "seed-v1",
        "base_discount": base_discount,
        "expiry_curve": expiry_curve or EXPIRY_FLAT,
        "inventory_curve": inventory_curve or INV_FLAT,
        "time_of_day_curve": time_of_day_curve or TOD_FLAT,
    }

    return {
        "case_id": case_id,
        "input": inp.model_dump(mode="json"),
        "anchor": {
            "p50": anchor_p50,
            "source": anchor_source,
            "region": anchor_region,
        },
        "coefficients": coeffs_json,
        "expected_price": round(rec.recommended_price, 4),
        "expected_pressures": rec.applied_pressures.model_dump(),
        "expected_formula_version": rec.formula_version,
    }


# Each block tags the scenario the case is meant to exercise. 50 total —
# the count is canonical (plan §8) and each name is descriptive enough
# that a regression failure points at the rule that changed.
FIXTURES: list[dict[str, Any]] = [
    # ---- baseline: flat curves, single unit, retail $12 ----
    _fixture("baseline_prepared_meal", category="prepared_meal"),
    _fixture("baseline_produce", category="produce", anchor_source="off"),
    _fixture("baseline_dairy", category="dairy", anchor_source="off"),
    _fixture("baseline_bakery", category="bakery"),
    _fixture("baseline_packaged_goods", category="packaged_goods", anchor_source="off"),
    _fixture("baseline_beverage", category="beverage"),
    _fixture("baseline_deli", category="deli"),
    _fixture("baseline_frozen", category="frozen", anchor_source="off"),
    _fixture("baseline_mixed_bag", category="mixed_bag", anchor_source="off"),

    # ---- expiry sweep on prepared_meal w/ aggressive curve ----
    _fixture("expiry_0h", category="prepared_meal", hours_until_expiry=0.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_2h", category="prepared_meal", hours_until_expiry=2.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_4h", category="prepared_meal", hours_until_expiry=4.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_8h", category="prepared_meal", hours_until_expiry=8.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_24h", category="prepared_meal", hours_until_expiry=24.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_48h_flat_tail", category="prepared_meal", hours_until_expiry=48.0, expiry_curve=EXPIRY_AGGRESSIVE),
    _fixture("expiry_mild_2h", category="produce", anchor_source="off", hours_until_expiry=2.0, expiry_curve=EXPIRY_MILD),

    # ---- inventory sweep ----
    _fixture("inv_1_unit", category="bakery", units=1, inventory_curve=INV_RAMP),
    _fixture("inv_5_units", category="bakery", units=5, inventory_curve=INV_RAMP),
    _fixture("inv_10_units", category="bakery", units=10, inventory_curve=INV_RAMP),
    _fixture("inv_25_units", category="bakery", units=25, inventory_curve=INV_RAMP),
    _fixture("inv_100_units_clamped", category="bakery", units=100, inventory_curve=INV_RAMP),

    # ---- time-of-day sweep on prepared_meal ----
    _fixture("tod_morning_8", category="prepared_meal", now_hour=8, time_of_day_curve=TOD_DINNER_PEAK),
    _fixture("tod_lunch_13", category="prepared_meal", now_hour=13, time_of_day_curve=TOD_DINNER_PEAK),
    _fixture("tod_afternoon_15", category="prepared_meal", now_hour=15, time_of_day_curve=TOD_DINNER_PEAK),
    _fixture("tod_dinner_19", category="prepared_meal", now_hour=19, time_of_day_curve=TOD_DINNER_PEAK),
    _fixture("tod_late_22", category="prepared_meal", now_hour=22, time_of_day_curve=TOD_DINNER_PEAK),

    # ---- merchant floor variations ----
    _fixture("floor_0_pct", category="prepared_meal", merchant_floor_pct=0.0),
    _fixture("floor_5_pct", category="prepared_meal", merchant_floor_pct=0.05),
    _fixture("floor_15_pct", category="prepared_meal", merchant_floor_pct=0.15),
    _fixture("floor_25_pct", category="prepared_meal", merchant_floor_pct=0.25),

    # ---- floor-clamp scenarios (raw price would dip below floor) ----
    _fixture(
        "clamp_to_floor",
        category="prepared_meal",
        retail_value=10.0,
        anchor_p50=10.0,
        merchant_floor_pct=0.40,
        expiry_curve=EXPIRY_AGGRESSIVE,
        hours_until_expiry=0.0,
        base_discount=0.30,
    ),
    _fixture(
        "clamp_to_floor_high_inventory",
        category="bakery",
        retail_value=20.0,
        anchor_p50=20.0,
        units=50,
        inventory_curve=INV_RAMP,
        merchant_floor_pct=0.45,
        expiry_curve=EXPIRY_AGGRESSIVE,
        hours_until_expiry=2.0,
        base_discount=0.20,
    ),

    # ---- retail-clamp scenario (raw price would exceed retail; rare but legal) ----
    _fixture(
        "clamp_to_retail",
        category="prepared_meal",
        retail_value=8.0,
        anchor_p50=20.0,
        merchant_floor_pct=0.0,
        base_discount=0.0,
        expiry_curve=EXPIRY_FLAT,
    ),

    # ---- combined pressures (real-world-ish) ----
    _fixture(
        "combo_dinner_rush_expiring",
        category="prepared_meal",
        retail_value=14.0,
        anchor_p50=12.0,
        units=3,
        hours_until_expiry=2.0,
        now_hour=19,
        expiry_curve=EXPIRY_AGGRESSIVE,
        inventory_curve=INV_RAMP,
        time_of_day_curve=TOD_DINNER_PEAK,
    ),
    _fixture(
        "combo_lunch_rush_fresh_bakery",
        category="bakery",
        retail_value=6.0,
        anchor_p50=5.5,
        units=12,
        hours_until_expiry=8.0,
        now_hour=13,
        expiry_curve=EXPIRY_MILD,
        inventory_curve=INV_RAMP,
        time_of_day_curve=TOD_DINNER_PEAK,
    ),
    _fixture(
        "combo_overnight_clearance",
        category="produce",
        anchor_source="off",
        retail_value=4.99,
        anchor_p50=4.5,
        units=20,
        hours_until_expiry=10.0,
        now_hour=22,
        expiry_curve=EXPIRY_AGGRESSIVE,
        inventory_curve=INV_RAMP,
        time_of_day_curve=TOD_DINNER_PEAK,
    ),

    # ---- price granularity (quarter-dollar rounding) ----
    _fixture("rounding_just_under", category="prepared_meal", retail_value=10.00, anchor_p50=10.00, base_discount=0.13),
    _fixture("rounding_just_over", category="prepared_meal", retail_value=10.00, anchor_p50=10.00, base_discount=0.27),
    _fixture("rounding_exact_quarter", category="prepared_meal", retail_value=10.00, anchor_p50=10.00, base_discount=0.25),

    # ---- anchor variation ----
    _fixture("anchor_low_3.50", category="produce", anchor_source="off", anchor_p50=3.50, retail_value=4.99),
    _fixture("anchor_high_45", category="prepared_meal", anchor_p50=45.00, retail_value=50.00, units=2),

    # ---- region fallback (regions are opaque to formula but stored in audit) ----
    _fixture("region_us_only", category="prepared_meal", region="US", anchor_region="US"),
    _fixture("region_us_fl", category="prepared_meal", region="US-FL", anchor_region="US-FL"),
    _fixture("region_us_ny_kings", category="bakery", region="US-NY-Kings", anchor_region="US-NY"),

    # ---- single-pressure-only sanity ----
    _fixture("only_base_discount", category="prepared_meal", base_discount=0.18),
    _fixture(
        "only_expiry",
        category="prepared_meal",
        base_discount=0.0,
        merchant_floor_pct=0.0,
        hours_until_expiry=2.0,
        expiry_curve=EXPIRY_AGGRESSIVE,
    ),
    _fixture(
        "only_inventory",
        category="bakery",
        base_discount=0.0,
        merchant_floor_pct=0.0,
        units=25,
        inventory_curve=INV_RAMP,
    ),
    _fixture(
        "only_time_of_day",
        category="prepared_meal",
        base_discount=0.0,
        merchant_floor_pct=0.0,
        now_hour=21,
        time_of_day_curve=TOD_DINNER_PEAK,
    ),

    # ---- floor-overrides-base (floor subtracts more than base adds) ----
    _fixture(
        "floor_overrides_base",
        category="prepared_meal",
        base_discount=0.05,
        merchant_floor_pct=0.20,
    ),

    # ---- additional edge / regression slots ----
    _fixture(
        "midnight_packaged_low_inventory",
        category="packaged_goods",
        anchor_source="off",
        now_hour=0,
        units=1,
        hours_until_expiry=72.0,
        time_of_day_curve=TOD_DINNER_PEAK,
    ),
    _fixture(
        "early_morning_dairy",
        category="dairy",
        anchor_source="off",
        now_hour=6,
        hours_until_expiry=12.0,
        expiry_curve=EXPIRY_MILD,
    ),
    _fixture(
        "frozen_long_shelf",
        category="frozen",
        anchor_source="off",
        hours_until_expiry=72.0,
        expiry_curve=EXPIRY_MILD,
    ),
]


def main() -> None:
    assert len(FIXTURES) >= 50, f"Need ≥50 cases, got {len(FIXTURES)}"
    seen: set[str] = set()
    for f in FIXTURES:
        if f["case_id"] in seen:
            raise ValueError(f"Duplicate case_id: {f['case_id']}")
        seen.add(f["case_id"])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as f:
        for case in FIXTURES:
            f.write(json.dumps(case, sort_keys=True))
            f.write("\n")
    print(f"wrote {len(FIXTURES)} cases to {OUT_PATH}")


if __name__ == "__main__":
    main()
