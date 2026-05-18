# Phase 6 Closeout — Async Webhook Retry Worker

**Shipped:** 2026-05-17. Plan: `~/.claude/plans/2026-05-17-phase-6-build.md`.

## What landed (6 tasks, all green)

| Track | Tasks | Outcome |
| - | - | - |
| **O — Retry worker** | O1, O2, O3, O4 | Schema migration (`last_attempt_at TIMESTAMPTZ` + retry-eligible partial index), `shared.webhook_retry.retry_failed_deliveries` sweep function with 2^attempt backoff + 5-attempt cap + subscription.active filter, background loop spawned in `service/app.py:_lifespan` with log-and-continue error handling, end-to-end integration test verifying sign + POST + persist on the retry path. |
| **P — Docs** | P1, P2 | CLAUDE.md webhook semantics extended with retry schedule + idempotency-by-event_id contract + operator triage SQL. This closeout doc. |

## Verification

- `pytest tests/unit` — **126 passed** (8 new from Phase 6: 6 webhook_retry + 2 retry_loop)
- `pytest tests/integration -m integration` — **18 passed** (2 new from Phase 6: retry_cycle integration)
- `ruff check .` — clean
- `mypy agents shared service` — clean
- All 5 evals @ threshold 0.85 — 100% pass (unchanged from Phase 5)
- 5 Agent Engines live (no agent redeploys this phase — Phase 6 was purely gateway-side)

## Key design decisions baked in

1. **Worker lives in the gateway process** (FastAPI lifespan-managed background asyncio task). Ships fastest; no Terraform / Cloud Run job churn. Trade-off: retries pause when the gateway is down. Acceptable at current scale; Phase 7+ can extract to a dedicated Cloud Run job if redelivery SLOs demand independent scaling. The `service/app.py` module-level helper function `_webhook_retry_loop` is structured for that future extraction.

2. **`2^attempt` seconds backoff** (2, 4, 8, 16, 32). After `attempt=5` the row is dead — operator runs a SQL query to investigate. No automatic dead-letter alerting yet (Phase 7d).

3. **Backoff window evaluated in SQL** (`COALESCE(last_attempt_at, created_at) + POWER(2, attempt) * INTERVAL '1 second' <= NOW()`). Keeps the candidate scan exact and indexable. The new partial index `webhook_deliveries_retry_idx ON (delivered_at, attempt, last_attempt_at) WHERE delivered_at IS NULL` supports this query.

4. **Subscription.active filter join** — if a customer deactivates their subscription, the retry worker skips its rows. The customer doesn't get re-pinged after they unsubscribed; the row stays as audit.

5. **Subscription URL is re-fetched at retry time** — subscriptions can rotate URLs between attempts. The worker honors the current state.

6. **Log-and-continue on sweep failures.** A transient DB or HTTP error during a sweep must NOT kill the loop, otherwise the gateway would silently lose retry coverage until restart. Errors are logged via `_log.warning(..., exc_info=True)`; the loop sleeps and tries again next interval.

7. **Idempotency by `event_id`.** Every retry sends the SAME `event_id` (set at first INSERT time). Customers MUST dedupe on it — industry-standard pattern. Documented in CLAUDE.md.

8. **Polling interval 30s, batch limit 100** — configurable via `WEBHOOK_RETRY_INTERVAL_S` and `WEBHOOK_RETRY_BATCH_LIMIT` env vars. Defaults balance retry latency against DB load at current low volume.

## Deferred from Phase 6 (future plans)

| Item | Where | When |
| - | - | - |
| **B4 — A2A wrapper migration** (`AdkApp` → `A2aAgent`) | OpenItems_B4.md | Phase 7a — needs user decisions |
| **H6 — Demo HTML rewrite** | `service/static/surplusas-merchant-demo.html` | Phase 7b — contest decision needed |
| **Per-subscription inbound signature verification** | `secret_hash` field | Phase 7c — only when we accept inbound |
| **Dead-letter alerting** (operator notification at attempt=5) | new file / Cloud Logging alert | Phase 7d |
| **Standalone retry-worker service** (extract from gateway) | Cloud Run job + Terraform | Phase 7e — only if redelivery SLOs demand independent scaling |
| **M5** — Cloud Build path-filter Terraform | `infra/terraform/cloudbuild_pricing.tf` | Phase 7 hardening |
| **M6** — `_handle_cache` autouse-fixture cleanup | `tests/unit/test_a2a.py` | Phase 7 hardening |
| **L1–L5** — cosmetic / docs | various | Phase 7 hardening |

## Required manual step before deploying the gateway

`shared/db_schema.sql` was updated to include `last_attempt_at` + the partial index, BUT the live Cloud SQL DB has not yet been migrated. Before deploying the gateway with the Phase 6 code, run:

```powershell
PG_USER=surplusas_app `
  PG_PASSWORD=$(gcloud secrets versions access latest `
    --secret=db-app-password --project=ps2o-surplusas-api) `
  uv run python scripts/apply_schema.py
```

The migration is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) so re-running is safe. After the apply, the gateway can deploy and the retry loop will start sweeping.

If the gateway is deployed before the migration applies, the FIRST webhook emission will fail with a column-not-found error (since `webhook_events.py` now INSERTs into `last_attempt_at`). Apply the migration first.

## Manual end-to-end verification (recommended)

```powershell
uv run python -m service.main
```

Then induce a failure: create a subscription pointing at a URL that returns 503 (e.g., a local stub), emit an event, then watch the `agents.webhook_deliveries` row evolve over ~32 seconds:

```sql
SELECT delivery_id, attempt, last_status_code, last_attempt_at,
       delivered_at, created_at
FROM agents.webhook_deliveries
WHERE delivery_id = '<your_id>'
ORDER BY last_attempt_at DESC NULLS LAST;
```

Expected progression: attempt goes 1 → 2 → 3 → 4 → 5 over ~32 seconds (assuming `WEBHOOK_RETRY_INTERVAL_S=0` for fast testing; with the default 30s it takes ~3 minutes). After attempt=5, the row is dead — no further retries.
