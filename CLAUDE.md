# CLAUDE.md

This file provides guidance to AI agents (Claude Code, Gemini CLI, etc.) working in this repository.

## What this repo is

`surplusAS-agents-2.0` — the multi-agent service for SurplusAS. Hub-and-spoke topology on Vertex AI Agent Engine:

- **Concierge** (`gemini-2.5-pro`) is the only externally-addressable agent.
- Four specialists communicate with the Concierge via A2A: **Pricing**, **Onboarding**, **Listing Intake**, **Dispute Triage**.
- Two **lateral A2A edges** exist by design: `Listing Intake → Pricing` (live anchor at intake) and `Dispute Triage → Pricing` (replay at dispute time).
- Customer-facing protocols: **REST + webhooks**. A2A is internal only.

The full implementation plan is `~/.claude/plans/the-ending-of-the-shimmering-reef.md`. Read it before making non-trivial changes.

## Hard guardrails (non-negotiable)

These are inherited from the consumed `surplusas-pricing` engine. Violating them breaks the audit story the entire system is built around.

1. **Pricing is deterministic.** Agents NEVER invent prices. The only path to a number is `pricing_engine.formula.recommend()` via `agents/pricing/engine_adapter.py`. The Pricing agent's LLM parses intent and writes rationale; it does not compute.
2. **Every recommendation is auditable.** `applied_pressures` and `formula_version` must round-trip through every layer (REST response, webhook payload, `recommendation_log` row, Concierge narration).
3. **`recommendation_log` is append-only.** Re-derivations write a NEW row with `replay_of=<orig_id>`. Never `UPDATE` an existing row.
4. **`pricing_coefficients` (in the `public` schema, owned by `surplusAS-pricing-intel`) is read-only here.** This repo's agents must never write to it.
5. **No fine-tuning.** All Gemini usage is base-model + prompting + tools.
6. **Per-merchant coefficient differentiation is OFF.** Coefficients lookup is keyed on `(category, region)` only.

## Companion repos

- `c:\Users\Mitch\surplusAS-pricing-intel` — the deterministic pricing engine. Consumed here as `vendor/surplusas-pricing` (git submodule). Treat it as a vendor library.
- `C:\Users\Mitch\SurplusAS-API-2.0` — earlier monolith API. This repo borrows patterns (A2A, auth, tracing, schemas, the static demo UI) but does not import its code.

## Conventions

- **Async-first Python**, 3.12.
- **Pydantic v2** for schemas.
- **`asyncpg` + Cloud SQL Python Connector** for DB access (no cloud-sql-proxy).
- **structlog** for logs; every line carries `partner_id, merchant_id, agent_name, trace_id, span_id`.
- **OpenTelemetry → Cloud Trace** for inter-agent spans; span name conventions are pinned in the plan §8.
- **`uv`** for dependency management; lockfile is checked in.
- File paths in markdown use the format `[name.py:42](path/to/name.py#L42)` so VSCode renders them clickable.

## Common commands

```bash
uv sync --extra dev                       # install deps incl. dev tools
uv run python -m service.main             # run the FastAPI gateway locally on $PORT (default 8080)
uv run pytest tests/unit                  # unit tests (CI runs only this for now)
uv run pytest tests/integration -m integration   # needs live Postgres / mocked Agent Engine
uv run pytest tests/unit/test_smoke.py::test_name -v   # single test
uv run ruff check .                       # lint (config: ruff.toml)
uv run mypy agents shared service         # type check (strict; vendor/ excluded)
uv run python -m evals.runner --agent all --threshold 0.85  # golden evals (needs Vertex)
PG_USER=... PG_PASSWORD=... uv run python scripts/apply_schema.py   # apply shared/db_schema.sql
```

Pytest markers (`pytest.ini`): `integration` (live PG / mocked Agent Engine), `e2e` (full demo flow).

## Module map

- `service/app.py` — FastAPI gateway factory; mounts REST, demo shim, dispute + webhook routes, static UI. `service/main.py` is the uvicorn entrypoint.
- `service/routes_rest.py` — `POST /v1/concierge`, `GET /v1/listings/{id}`.
- `service/routes_disputes.py` — `POST /v1/listings/{id}/dispute`, `GET /v1/disputes/{id}`. (Phase 4)
- `service/routes_webhooks.py` — `POST /v1/webhooks/subscriptions`, `DELETE /v1/webhooks/subscriptions/{id}`. (Phase 4)
- `service/routes_demo.py` — same-origin `/demo/v1/*` shims for the bundled static UI.
- `agents/<name>/agent.py` + `manifest.yaml` — one directory per agent (`concierge`, `pricing`, `onboarding`, `listing_intake`, `dispute_triage`). The Pricing agent additionally owns `engine_adapter.py` (the only writer to `agents.recommendation_log`). The Dispute Triage agent owns the only writes to `agents.disputes` and is the only emitter of `price.updated` webhooks.
- `shared/a2a.py` — outbound A2A client; owns ID-token caching, trace propagation, and the `aggregate_peer_stream` helper that extracts narration + tool calls from ADK streams. **All inter-agent calls go through this.**
- `shared/auth.py` — `Authorization: Bearer <api_key>` resolver against `public.partner_keys`. Every public route uses `PartnerDep`.
- `shared/db.py` — process-wide asyncpg pool via Cloud SQL Connector. Tests use `init_pool_from_dsn()` to bypass the connector.
- `shared/schemas.py` — wire-format Pydantic contracts that cross agent boundaries (A2A envelopes, `RecommendationLogEntry`, `ValidationResult`, webhook events). Agent-internal DTOs live with their owning agent.
- `shared/config.py` — `get_settings()` (cached); 12-factor env-driven, secrets injected at container start. `webhook_signing_key` reads from `WEBHOOK_SIGNING_KEY`.
- `shared/webhook_dispatcher.py` — HMAC-SHA256 sign + httpx POST. Sync-with-audit-row semantics. (Phase 4)
- `shared/webhook_subscriptions.py` — CRUD on `agents.webhook_subscriptions`. Stores SHA-256 of customer-provided secrets (Phase 5 will use them for inbound verification). (Phase 4)
- `shared/webhook_events.py` — `emit_event(event_type, partner_id, payload)` orchestrator. Fan-out to active subscriptions, audit row per delivery, sync attempt. (Phase 4)
- `shared/db_schema.sql` — DDL for the `agents` schema. Apply via `scripts/apply_schema.py`.
- `infra/terraform/` — IAM, Cloud SQL user, Secret Manager. `vendor/surplusas-pricing/` is the pricing-engine submodule.

## Webhook semantics (Phase 4)

- **Signing:** every outbound delivery carries an `X-Surplus-Signature: sha256=<hex>` header. The HMAC is computed over the **exact JSON body bytes** (`separators=(",", ":")` — no whitespace) using the repo-wide `WEBHOOK_SIGNING_KEY` from Secret Manager. Customers verify with the same key (NOT the per-subscription secret — that field is reserved for future inbound verification).
- **Delivery model:** sync-with-audit-row. The emitting agent INSERTs a `webhook_deliveries` row (`attempt=1`, `delivered_at=NULL`), POSTs, then UPDATEs the row with `last_status_code` + `delivered_at` (on 2xx) or `last_status_code` + `last_error` (otherwise). No background retry worker yet — failed rows accumulate for Phase 5/6.
- **Threshold:** `price.updated` fires only when `|new_price - old_price| > $0.25`. Below threshold, the dispute still persists but no webhook ships. Pinned in `agents/dispute_triage/tools.py::_PRICE_UPDATE_THRESHOLD`.
- **Event envelope:** every delivery body is `{event_id, event_type, partner_id, occurred_at, payload}` — the inner `payload` is event-type-specific.

## Operational gotchas

- **A2A from a dev laptop:** plain-user ADC cannot mint audience-scoped ID tokens. Use `gcloud auth application-default login --impersonate-service-account=...` or run on Cloud Run/GCE. See `shared/a2a.py:11`.
- **CI does not clone the submodule** (`.github/workflows/ci.yml:22`). Week 2 work that imports `pricing_engine` will need a cross-repo PAT or deploy key first — don't "fix" the `submodules: false` line until that's provisioned.
- **Vertex flag is set in `get_settings()`** before any `google.genai` import (`shared/config.py:62`). Don't call `genai.Client(...)` at module import time.

## Don't

- Don't add new SDKs or model providers without updating the plan and `pyproject.toml` together.
- Don't reimplement pricing logic — read it from the submodule and call it.
- Don't write to `agents.recommendation_log` outside `agents/pricing/engine_adapter.py`.
- Don't bypass `shared/a2a.py` for inter-agent calls — it owns ID-token caching and trace propagation.
- Don't introduce hard FK constraints across the `public` ↔ `agents` schema boundary; validate logically in app code.
