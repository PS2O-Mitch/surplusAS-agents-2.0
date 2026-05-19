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

## Verification log (live, 2026-05-18)

### Schema migration applied

```
$ PG_USER=surplusas_app PG_PASSWORD=<secret db-app-password> uv run python scripts/apply_schema.py
Applying db_schema.sql to ps2o-surplusas-api:us-central1:surplusas-db/surplusas as surplusas_app...
Schema apply complete.
```

Post-apply column + index check:

```
Columns in webhook_deliveries:
 - delivery_id, subscription_id, event_type, payload, attempt,
   last_status_code, last_error, delivered_at, created_at, last_attempt_at
Indexes:
 - webhook_deliveries_pending_idx
 - webhook_deliveries_pkey
 - webhook_deliveries_retry_idx          ← Phase 6
```

The migration is idempotent (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`); re-running is safe.

### End-to-end retry cycle (live Cloud SQL + local 503 stub + local gateway)

Setup: local 503 stub on `127.0.0.1:8765`, local gateway on `:9090` with `WEBHOOK_RETRY_INTERVAL_S=1`, a temporary subscription pointing at the stub, `emit_event` called once for `price.updated`. Audit row polled every 2s.

```
[driver] inserted subscription b6b39c0e-... -> http://127.0.0.1:8765/hook
[driver] emit_event result: {'status': 'ok', 'delivery_ids': ['0a051552-...']}
[driver +  0.1s] attempt=1 status=503  (initial sync emit)
[driver +  2.2s] attempt=2 status=503  (+2s backoff = 2^1)
[driver +  8.4s] attempt=3 status=503  (+4s backoff = 2^2; +2s connector overhead)
[driver + 16.6s] attempt=4 status=503  (+8s backoff = 2^3)
[driver + 33.2s] attempt=5 status=503  DEAD-LETTERED (+16s backoff = 2^4)
[driver] cleanup: deliveries=DELETE 1 subscription=DELETE 1
```

Stub-side: 5 POSTs observed with `X-Surplus-Signature` headers; retries all carried the same signature (same event_id + payload → same HMAC), confirming the idempotency contract on the wire.

### Bug surfaced (and fixed) by live verification

`shared/webhook_events.py:77` was calling `UUID(sub["subscription_id"])` without first stringifying. Every existing unit/integration test stubs `list_active_subscriptions_for_event` to return a *str* `subscription_id`, but the live asyncpg connection returns `asyncpg.pgproto.UUID`, and the stdlib `uuid.UUID()` constructor calls `.replace()` on its argument — which raises `AttributeError` on non-str inputs.

The Phase 6 retry path on `shared/webhook_retry.py:77` already did `UUID(str(row["delivery_id"]))` defensively; emit_event just missed the cast. One-line fix landed in the same commit as the verification log; regression test `test_emit_event_accepts_non_string_subscription_id` added that stubs subscription_id with a stdlib `uuid.UUID` (same shape — no `.replace`).

This bug had been latent in production code since Phase 4 — `agents.webhook_deliveries` had zero rows when verification ran, confirming no real customer subscription had ever exercised the path before this test.

### Final verification sweep (post-fix)

- `pytest tests/unit -q` — **127 passed** (was 126; +1 regression)
- `pytest tests/integration -m integration -q` — **18 passed**
- `ruff check .` — clean
- Live e2e retry cycle — attempts 1→5 observed in DB, all 5 POSTs observed on the stub, signatures present
