"""Cross-agent Pydantic schemas.

Wire-format contracts for REST, A2A, and webhook surfaces. Agent-internal
DTOs (e.g., `MerchantProfile` write payloads) live alongside the agent that
owns them; this module is the contract surface that crosses agent boundaries.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — pydantic v2 needs runtime resolution
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID  # noqa: TC003 — pydantic v2 needs runtime resolution

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# A2A inbound envelope. Mirrors the `AgentRequest` shape from
# SurplusAS-API-2.0/shared/schemas.py:96 so existing inter-service idioms
# port over without translation.
# ---------------------------------------------------------------------------
class AgentRequest(BaseModel):
    """Generic envelope for inter-agent A2A POSTs to `/v1/agent`."""

    model_config = ConfigDict(extra="allow")

    mode: str = Field(..., description="Discriminator the receiving agent dispatches on.")
    input: dict[str, Any] = Field(default_factory=dict)
    image: str | None = Field(default=None, description="Optional base64-encoded image.")
    partner_id: str = Field(..., description="Tenant identity (logical FK to public.partner_keys).")
    merchant_id: str | None = None
    listing_id: str | None = None


class AgentResponse(BaseModel):
    """Generic envelope returned to the caller of `/v1/agent`."""

    model_config = ConfigDict(extra="allow")

    status: Literal["ok", "no_anchor", "validation_error", "internal_error"] = "ok"
    payload: dict[str, Any] = Field(default_factory=dict)
    narration: str | None = None
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# Concierge (the only externally-addressable agent) request/response.
# ---------------------------------------------------------------------------
class ConciergeRequest(BaseModel):
    partner_id: str
    message: str
    merchant_id: str | None = None
    listing_id: str | None = None
    image: str | None = None


class ConciergeResponse(BaseModel):
    narration: str
    specialist_called: str | None = None
    specialist_payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


# ---------------------------------------------------------------------------
# RecommendationLogEntry: the auditable record of a price recommendation.
# Round-trips through every agent that touches a price.
# ---------------------------------------------------------------------------
class RecommendationLogEntry(BaseModel):
    recommendation_id: UUID
    listing_id: UUID | None = None
    merchant_id: UUID | None = None
    partner_id: str
    pricing_input: dict[str, Any]
    recommended_price: Decimal
    recommended_discount_pct: Decimal
    anchor_p50: Decimal
    anchor_source: str
    anchor_region: str
    # `float | bool` preserves the engine's `clamped_to_floor` /
    # `clamped_to_retail` flags through `model_dump(mode="json")`. A flat
    # `dict[str, float]` here would coerce `False` → `0.0` and lose the
    # clamping signal — guardrail #2 ("verbatim round-trip") demands we keep them.
    applied_pressures: dict[str, float | bool]
    formula_version: str
    coefficients_version: int
    replay_of: UUID | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Listing draft / persisted listing.
# ---------------------------------------------------------------------------
class ListingStatus(StrEnum):
    draft = "draft"
    draft_no_price = "draft_no_price"
    published = "published"
    withdrawn = "withdrawn"
    sold = "sold"


class ListingDraft(BaseModel):
    """Output of Listing Intake's `parse_draft`. Pre-persistence shape."""

    title: str
    description: str | None = None
    category: str
    units: int = Field(..., ge=1)
    retail_value: Decimal = Field(..., gt=0)
    hours_until_expiry: Decimal
    image_uri: str | None = None


# ---------------------------------------------------------------------------
# ValidationResult — wire format for listing-validation responses.
# Emitted by the gateway (`service/routes_rest.py`, Phase 3 Track C) and
# mirrored in tool returns from `validate_listing`. Pydantic models live here
# so the route can declare them as response_model without re-deriving the
# shape at the boundary.
# ---------------------------------------------------------------------------
class ValidationError(BaseModel):
    field: str
    error: str


class ValidationResult(BaseModel):
    status: Literal["ok", "validation_error"]
    errors: list[ValidationError] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# MerchantProfile (Onboarding's write target).
# ---------------------------------------------------------------------------
class MerchantProfile(BaseModel):
    merchant_id: UUID | None = None
    partner_id: str
    merchant_name: str
    region: str
    merchant_floor_pct: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    allowed_categories: list[str]
    timezone: str = "America/New_York"


# ---------------------------------------------------------------------------
# Dispute (Dispute Triage write target) + result envelope.
# ---------------------------------------------------------------------------
class DisputeResult(BaseModel):
    dispute_id: UUID
    listing_id: UUID
    old_price: Decimal
    new_price: Decimal
    pressure_diff: dict[str, float]
    narration: str


# ---------------------------------------------------------------------------
# Webhooks.
# ---------------------------------------------------------------------------
class WebhookEventType(StrEnum):
    merchant_profile_created = "merchant.profile.created"
    listing_created = "listing.created"
    price_updated = "price.updated"
    dispute_resolved = "dispute.resolved"


class WebhookEvent(BaseModel):
    event_id: UUID
    event_type: WebhookEventType
    partner_id: str
    occurred_at: datetime
    payload: dict[str, Any]
