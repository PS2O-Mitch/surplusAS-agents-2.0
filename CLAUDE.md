# CLAUDE.md

This file provides guidance to AI agents (Claude Code, Gemini CLI, etc.) working in this repository.

## What this repo is

`surplusAS-agents-2.0` — the multi-agent service for SurplusAS. Hub-and-spoke topology, one FastAPI service on Fly.io with all five ADK agents in-process:

- **Concierge** (`gemini-2.5-pro`) is the only externally-addressable agent.
- Four specialists coordinate with the Concierge through the internal mesh (`shared/a2a.py`, in-process ADK Runners): **Pricing**, **Onboarding**, **Listing Intake**, **Dispute Triage**.
- Two **lateral edges** exist by design: `Listing Intake → Pricing` (live anchor at intake) and `Dispute Triage → Pricing` (replay at dispute time).
- Customer-facing protocols: **REST + webhooks**. The mesh is internal; the open A2A protocol surface (`service/a2a_app.py`) is an optional interop layer.
- Models: **Gemini Developer API** (`GOOGLE_API_KEY`, no GCP project). DB: **Supabase Postgres** (plain asyncpg DSN). Deploy: **Fly.io** (`fly.toml`).

The original implementation plan is `~/.claude/plans/the-ending-of-the-shimmering-reef.md`; the off-GCP migration plan is `~/.claude/plans/lively-gliding-avalanche.md`.

## Hard guardrails (non-negotiable)

These are inherited from the consumed `surplusas-pricing` engine. Violating them breaks the audit story the entire system is built around.

1. **Pricing is deterministic.** Agents NEVER invent prices. The only path to a number is `pricing_engine.formula.recommend()` via `agents/pricing/engine_adapter.py`. The Pricing agent's LLM parses intent and writes rationale; it does not compute.
2. **Every recommendation is auditable.** `applied_pressures` and `formula_version` must round-trip through every layer (REST response, webhook payload, `recommendation_log` row, Concierge narration).
3. **`recommendation_log` is append-only.** Re-derivations write a NEW row with `replay_of=<orig_id>`. Never `UPDATE` an existing row. Enforced at the DB level: `scripts/provision_supabase.sql` REVOKEs UPDATE/DELETE from the app role.
4. **`pricing_coefficients` (in the `public` schema, owned by `surplusAS-pricing-intel`) is read-only here.** This repo's agents must never write to it.
5. **No fine-tuning.** All Gemini usage is base-model + prompting + tools.
6. **Per-merchant coefficient differentiation is OFF.** Coefficients lookup is keyed on `(category, region)` only.

## Companion repos

- `c:\Users\Mitch\surplusAS-pricing-intel` — the deterministic pricing engine. Consumed here as `vendor/surplusas-pricing` (git submodule). Treat it as a vendor library.
- `C:\Users\Mitch\SurplusAS-API-2.0` — earlier monolith API. This repo borrows patterns (auth, tracing, schemas, the static demo UI) but does not import its code.

## Conventions

- **Async-first Python**, 3.12.
- **Pydantic v2** for schemas.
- **`asyncpg` over a plain DSN** (`DATABASE_URL`). Supabase: session-mode pooler or direct connection only — transaction-mode pgBouncer breaks asyncpg prepared statements.
- **structlog** for logs; every line carries `partner_id, merchant_id, agent_name, trace_id, span_id`.
- **OpenTelemetry** spans on every inter-agent hop; exported over OTLP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (no-op otherwise). Span name conventions are pinned in the plan §8.
- **`uv`** for dependency management; lockfile is checked in.
- File paths in markdown use the format `[name.py:42](path/to/name.py#L42)` so VSCode renders them clickable.

## Common commands

```bash
uv sync --extra dev                       # install deps incl. dev tools
uv run python -m service.main             # run the FastAPI gateway locally on $PORT (default 8080)
uv run pytest tests/unit                  # unit tests (CI runs only this for now)
uv run pytest tests/integration -m integration   # mocked-runner integration tests
uv run pytest tests/unit/test_smoke.py::test_name -v   # single test
uv run ruff check .                       # lint (config: ruff.toml)
uv run mypy agents shared service         # type check (strict; vendor/ excluded)
uv run python -m evals.runner --agent pricing --threshold 0.85        # golden evals, local (pure Python; one agent per run)
uv run python -m evals.runner --agent pricing --mode remote           # live-model smoke (needs GOOGLE_API_KEY + DB)
DATABASE_URL=... uv run python scripts/apply_schema.py    # apply shared/db_schema.sql
DATABASE_URL=... uv run python scripts/seed_demo_merchant.py   # reset + seed demo data
fly deploy                                # ship to Fly.io (secrets via `fly secrets set`)
```

Pytest markers (`pytest.ini`): `integration`, `e2e` (declared; e2e-tagged flows currently use `integration`).

## Module map

- `service/app.py` — FastAPI gateway factory; mounts REST, demo shim, dispute + webhook routes, static UI; spawns the webhook retry loop. `service/main.py` is the uvicorn entrypoint.
- `service/routes_rest.py` — `POST /v1/concierge`, `GET /v1/listings/{id}`.
- `service/routes_disputes.py` — `POST /v1/listings/{id}/dispute` (open), `GET /v1/disputes/{id}` (read), `PATCH /v1/disputes/{id}` (resolve + emit `dispute.resolved`).
- `service/routes_webhooks.py` — `POST /v1/webhooks/subscriptions`, `DELETE /v1/webhooks/subscriptions/{id}`.
- `service/routes_demo.py` — same-origin `/demo/v1/*` shims for the bundled static UI.
- `service/a2a_app.py` — optional open-A2A surface: serves any agent's Agent Card + JSON-RPC endpoint via ADK `to_a2a()`.
- `agents/<name>/agent.py` — one directory per agent (`concierge`, `pricing`, `onboarding`, `listing_intake`, `dispute_triage`). The Pricing agent additionally owns `engine_adapter.py` (the only writer to `agents.recommendation_log`). The Dispute Triage agent owns the only writes to `agents.disputes` and is the only emitter of `price.updated` webhooks.
- `shared/a2a.py` — the internal mesh: one cached in-process ADK `Runner` per agent (`load_agent` imports lazily to avoid the tools→a2a→agents cycle), per-call session ids, and the `aggregate_peer_stream` helper that extracts narration + tool calls from event streams. **All inter-agent calls go through this.** Sessions are in-memory → exactly one machine; swap to a DB-backed SessionService before scaling out.
- `shared/auth.py` — `Authorization: Bearer <api_key>` resolver against `public.partner_keys`. Every public route uses `PartnerDep`.
- `shared/db.py` — process-wide asyncpg pool from `DATABASE_URL` (lazy init per-caller; `init_pool()` delegates to `init_pool_from_dsn()`). Registers JSONB codecs so `applied_pressures` reads back as a dict (guardrail #2).
- `shared/schemas.py` — wire-format Pydantic contracts that cross agent boundaries (A2A envelopes, `RecommendationLogEntry`, `ValidationResult`, webhook events). Agent-internal DTOs live with their owning agent.
- `shared/config.py` — `get_settings()` (cached); 12-factor env-driven, secrets injected at container start (Fly secrets). Mirrors `GOOGLE_API_KEY` into `os.environ` for google-genai.
- `shared/webhook_dispatcher.py` — HMAC-SHA256 sign + httpx POST. Sync-with-audit-row semantics.
- `shared/webhook_subscriptions.py` — CRUD on `agents.webhook_subscriptions`. Stores SHA-256 of customer-provided secrets (reserved for future inbound verification).
- `shared/webhook_events.py` — `emit_event(event_type, partner_id, payload)` orchestrator. Fan-out to active subscriptions, audit row per delivery, sync attempt.
- `shared/db_schema.sql` — DDL for the `agents` schema. Apply via `scripts/apply_schema.py`. Supabase standup order (roles, `public.partner_keys` DDL, vendor SQL, grants) is in `scripts/provision_supabase.sql`.
- `fly.toml` + `Dockerfile` — the deploy unit. `vendor/surplusas-pricing/` is the pricing-engine submodule.

## Webhook semantics

- **Signing:** every outbound delivery carries an `X-Surplus-Signature: sha256=<hex>` header. The HMAC is computed over the **exact JSON body bytes** (`separators=(",", ":")` — no whitespace) using the repo-wide `WEBHOOK_SIGNING_KEY` (a Fly secret). Customers verify with the same key (NOT the per-subscription secret — that field is reserved for future inbound verification).
- **Delivery model:** sync-first-attempt + async retry. The emitter (agent tool or gateway route) does the first POST synchronously and INSERTs the audit row in `webhook_deliveries` (`attempt=1`, `last_attempt_at=NOW()`). Failed rows are swept by the background retry loop in the gateway — `shared/webhook_retry.py:retry_failed_deliveries`, spawned by `service/app.py:_lifespan`.
- **Retry schedule:** `2^attempt` seconds between attempts (2s, 4s, 8s, 16s, 32s). After `attempt=5` the row is a dead-letter — never retried, just sits as audit trail. Polling interval: 30s (`WEBHOOK_RETRY_INTERVAL_S`). Batch limit per cycle: 100 rows (`WEBHOOK_RETRY_BATCH_LIMIT`). Backoff window is computed in SQL via `COALESCE(last_attempt_at, created_at) + POWER(2, attempt) * INTERVAL '1 second' <= NOW()`.
- **Idempotency contract:** every retry sends the SAME `event_id`. Customers MUST dedupe on `event_id`. Industry-standard pattern.
- **Subscriptions that go inactive (`active=FALSE`) are skipped by the retry worker** — we don't re-ping unsubscribed customers. Their pending dead-letter rows remain as audit.
- **Operator triage of dead-letters** (Supabase SQL editor):
  ```sql
  SELECT delivery_id, event_type, attempt, last_status_code, last_error, created_at
  FROM agents.webhook_deliveries
  WHERE delivered_at IS NULL AND attempt >= 5
  ORDER BY created_at DESC;
  ```
- **Threshold:** `price.updated` fires only when `|new_price - old_price| > $0.25`. Below threshold, the dispute still persists but no webhook ships. Pinned in `agents/dispute_triage/tools.py::_PRICE_UPDATE_THRESHOLD`.
- **Event envelope:** every delivery body is `{event_id, event_type, partner_id, occurred_at, payload}` — the inner `payload` is event-type-specific.
- **Event ownership:**
  - `merchant.profile.created` — Onboarding agent emits after `create_merchant_profile`.
  - `listing.created` — Listing Intake agent emits after `persist_listing`.
  - `price.updated` — Dispute Triage agent emits when `|delta| > $0.25` via `emit_price_update_webhook`.
  - `dispute.resolved` — the gateway route `PATCH /v1/disputes/{id}` emits after the resolution UPDATE.
- **Non-fatal emit:** webhook failures never fail the primary write. The tool/route returns `status: ok` with `webhook_status: error` and the audit row in `webhook_deliveries` is the source of truth.
- **Resolution lifecycle:** disputes are append-only at the resolution boundary. Once `pending -> accepted/rejected/withdrawn`, PATCH returns 409. No reopen workflow yet.

## Operational gotchas

- **`GOOGLE_API_KEY` is mirrored into `os.environ` by `get_settings()`** (`shared/config.py`) because google-genai reads the process env, not pydantic's `.env`. Don't construct `genai.Client(...)` at module import time — the first model call happens after settings are loaded.
- **`google-cloud-aiplatform` remains installed transitively** (hard dep of `google-adk` 2.0.0b1) even though nothing here imports it. Never import it; the runtime has no GCP dependency.
- **CI clones the submodule via a scoped PAT** (`.github/workflows/ci.yml` — `SUBMODULE_TOKEN` secret + explicit `git submodule update --init`). `fly deploy` builds from the local context, so the locally-initialized submodule ships; CI-driven deploys need the PAT checkout first.
- **Supabase DSN:** session-mode pooler (port 5432 on the pooler host, username `<role>.<project-ref>`) — transaction mode (port 6543) breaks asyncpg prepared statements, and the direct host is IPv6-only on most plans.
- **`DEMO_MODE` gates the unauthenticated surface.** The `/demo/v1/*` shim + static UI force the demo partner and skip auth (they were IAP-gated on Cloud Run); `service/app.py` mounts them only when `DEMO_MODE=true`. Never set it on the public Fly app.
- **No dead-letter alerting yet.** The GCP log-metric alert died with the Terraform; webhook dead-letters (attempt=5) are only visible via the triage SQL above. Wire an alert (Fly log ship → alerting, or a cron over `webhook_deliveries`) before real customers depend on webhooks.

## Don't

- Don't add new SDKs or model providers without updating the plan and `pyproject.toml` together.
- Don't reimplement pricing logic — read it from the submodule and call it.
- Don't write to `agents.recommendation_log` outside `agents/pricing/engine_adapter.py`.
- Don't bypass `shared/a2a.py` for inter-agent calls — it owns the runner cache, session handling, and span conventions.
- Don't introduce hard FK constraints across the `public` ↔ `agents` schema boundary; validate logically in app code.
