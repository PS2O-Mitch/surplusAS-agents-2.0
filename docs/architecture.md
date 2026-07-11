# Architecture — `surplusAS-agents-2.0`

A hub-and-spoke multi-agent service: one FastAPI gateway on Fly.io with all five ADK agents running **in-process**. The **Concierge** is the only agent on the customer REST path; four specialists coordinate with it across the internal mesh. Customers see REST + webhooks.

Two A2A layers, by design (see [Customer integration model](#customer-integration-model)):
- **Open A2A surface** — every agent can also be published over the **open Agent-to-Agent protocol** (Agent Card at `/.well-known/agent-card.json` + JSON-RPC 2.0) via ADK's `to_a2a()` adapter, so external enterprise agents can discover and call it.
- **Internal mesh transport** — inter-agent calls run as in-process ADK `Runner` streams (`shared/a2a.py`): no network hop, spans parent naturally.

## Agent topology

```mermaid
flowchart TB
    subgraph customer["Customer surface (public)"]
        rest["REST<br/>POST /v1/concierge<br/>GET /v1/listings/{id}<br/>POST /v1/listings/{id}/dispute<br/>PATCH /v1/disputes/{id}<br/>POST /v1/webhooks/subscriptions"]
        wh["Webhooks (outbound)<br/>merchant.profile.created<br/>listing.created<br/>price.updated (|Δ| &gt; $0.25)<br/>dispute.resolved"]
    end

    subgraph gateway["FastAPI gateway"]
        app["service/app.py<br/>routes_rest · routes_disputes<br/>routes_webhooks · routes_demo<br/>_webhook_retry_loop (lifespan)"]
    end

    subgraph mesh["A2A internal mesh"]
        concierge["Concierge<br/>gemini-3.1-pro-preview<br/>(only externally-addressable)"]
        pricing["Pricing<br/>gemini-3.5-flash<br/>deterministic, audit-bearing"]
        onboarding["Onboarding<br/>gemini-3.5-flash"]
        intake["Listing Intake<br/>gemini-3.5-flash"]
        dispute["Dispute Triage<br/>gemini-3.1-pro-preview"]
    end

    subgraph data["Postgres (Supabase)"]
        log[("agents.recommendation_log<br/>(append-only)")]
        listings[("agents.listings")]
        disputes[("agents.disputes")]
        coeffs[("public.pricing_coefficients<br/>(read-only, owned by<br/>surplusAS-pricing-intel)")]
        subs[("agents.webhook_subscriptions<br/>agents.webhook_deliveries")]
    end

    rest --> app
    app -->|"in-process Runner (traced)"| concierge
    concierge -->|"route_to_pricing"| pricing
    concierge -->|"route_to_onboarding"| onboarding
    concierge -->|"route_to_listing_intake"| intake
    concierge -->|"route_to_dispute_triage"| dispute

    intake -. "lateral A2A<br/>request_anchor_price" .-> pricing
    dispute -. "lateral A2A<br/>replay_recommendation" .-> pricing

    pricing -->|"engine_adapter.recommend<br/>INSERT only"| log
    pricing -->|"SELECT"| coeffs
    intake -->|"persist_listing"| listings
    dispute -->|"persist_dispute"| disputes
    app -->|"emit_event"| subs
    subs --> wh

    classDef customer fill:#e8f4f8,stroke:#0366d6
    classDef agent fill:#fff4e6,stroke:#d97706
    classDef store fill:#f0fdf4,stroke:#16a34a
    class rest,wh customer
    class concierge,pricing,onboarding,intake,dispute agent
    class log,listings,disputes,coeffs,subs store
```

The two dotted edges (`Listing Intake → Pricing`, `Dispute Triage → Pricing`) are the only lateral A2A links by design. Everything else flows through the Concierge.

## Beat 1 — paste draft, get priced listing

```mermaid
sequenceDiagram
    autonumber
    actor M as Merchant
    participant UI as Static demo UI
    participant GW as Gateway /v1/concierge
    participant C as Concierge
    participant I as Listing Intake
    participant P as Pricing
    participant DB as agents.*

    M->>UI: paste sandwich draft
    UI->>GW: POST /v1/concierge {input}
    GW->>C: A2A call (mode=route)
    C->>I: route_to_listing_intake (A2A)
    I->>I: parse_draft → validate_listing
    I->>P: request_anchor_price (lateral A2A)
    P->>P: lookup_anchor + recommend (deterministic)
    P->>DB: INSERT agents.recommendation_log
    P-->>I: {price, applied_pressures, formula_version}
    I->>DB: INSERT agents.listings (initial_recommendation_id)
    I-->>C: ListingPersisted + narration
    C-->>GW: ListingResponse
    GW-->>UI: listing + price + audit pressures
    GW->>DB: emit_event(listing.created) → POST customer webhook
```

## Beat 2 — dispute, replay, narrate, webhook

```mermaid
sequenceDiagram
    autonumber
    actor M as Merchant
    participant UI as Demo UI
    participant GW as Gateway
    participant C as Concierge
    participant D as Dispute Triage
    participant P as Pricing
    participant DB as agents.*

    M->>UI: open dispute on listing
    UI->>GW: POST /v1/listings/{id}/dispute {reason}
    GW->>C: A2A call (mode=dispute)
    C->>D: route_to_dispute_triage (A2A)
    D->>DB: fetch_recommendation_log(listing_id)
    D->>P: replay_recommendation(orig_id) (lateral A2A)
    P->>P: re-resolve coefficients + anchor (FRESH)
    P->>DB: INSERT recommendation_log (replay_of=orig)
    P-->>D: new RecommendationLogEntry
    D->>D: diff_pressures(orig, new)
    D->>DB: INSERT agents.disputes (pending)
    alt |new_price − old_price| > $0.25
        D->>DB: emit_event(price.updated) → customer webhook
    end
    D-->>C: DisputeOpened + narration
    C-->>GW: DisputeResponse
    GW-->>UI: pressures diff + new price + status

    Note over GW,DB: PATCH /v1/disputes/{id} (later):<br/>UPDATE status (accepted|rejected|withdrawn)<br/>then emit_event(dispute.resolved)
```

## Hard guardrails (data plane)

1. **Pricing is deterministic.** The LLM never invents a number. The only path is [`pricing_engine.formula.recommend`](../vendor/surplusas-pricing/pricing_engine/formula.py) via [`agents/pricing/engine_adapter.py`](../agents/pricing/engine_adapter.py).
2. **Every recommendation is auditable.** `applied_pressures` + `formula_version` round-trip from engine → REST/webhook → DB → Concierge narration.
3. **`agents.recommendation_log` is append-only.** Replays write NEW rows with `replay_of=<orig_id>` ([`engine_adapter.replay_recommendation`](../agents/pricing/engine_adapter.py)).
4. **`public.pricing_coefficients` is read-only.** Owned by `surplusAS-pricing-intel`; this repo only `SELECT`s.
5. **No fine-tuning.** Base Gemini + prompting + tools.
6. **Per-merchant coefficient differentiation is OFF.** Lookup keyed on `(category, region)` only.

## Customer integration model

- **Customer-facing:** REST for synchronous ops, HMAC-SHA256 signed webhooks for async events (over the repo-wide `WEBHOOK_SIGNING_KEY`, NOT the per-subscription secret — that's reserved for future inbound verification).
- **A2A — open surface:** every agent can be published over the open A2A protocol via ADK's `to_a2a()` adapter ([`service/a2a_app.py`](../service/a2a_app.py)) — a discoverable Agent Card at `/.well-known/agent-card.json` + a JSON-RPC 2.0 endpoint, framework-agnostic. Verify with `uv run python -m scripts.verify_a2a` (stock `a2a-sdk` client; no cloud creds).
- **A2A — internal mesh transport:** the hub-and-spoke inter-agent calls go through [`shared/a2a.py`](../shared/a2a.py) as in-process ADK `Runner` streams; one cached Runner per agent, per-call session ids.
- **Tracing:** OpenTelemetry spans across every A2A hop, exported over OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (no-op otherwise); span names pinned in the [implementation plan](~/.claude/plans/the-ending-of-the-shimmering-reef.md) §8.
- **Webhook retry:** sync-first-attempt with async sweep; `2^attempt`-second backoff (2, 4, 8, 16, 32s), dead-letter at attempt=5. Idempotency by `event_id`. See [`CLAUDE.md`](../CLAUDE.md) "Webhook semantics".

## Deployment

One Fly.io machine runs the whole system: the FastAPI gateway (static demo UI, customer REST, webhook retry loop) with all five ADK agents loaded in-process. Postgres is Supabase over a plain asyncpg DSN (`DATABASE_URL`). Secrets (`GOOGLE_API_KEY`, `DATABASE_URL`, `WEBHOOK_SIGNING_KEY`) are Fly secrets, landing as env vars at container start. Deploy with `fly deploy` (see [`fly.toml`](../fly.toml)); provision the database per [`scripts/provision_supabase.sql`](../scripts/provision_supabase.sql).

Sessions are in-memory (`shared/a2a.py`), so the service is pinned to one machine; swap in a DB-backed `SessionService` before scaling out.
