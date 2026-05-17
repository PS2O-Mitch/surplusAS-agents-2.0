# Phase 5 Closeout — Webhook Emission Completeness + Dispute Lifecycle

**Shipped:** 2026-05-17. Plan: `~/.claude/plans/2026-05-17-phase-5-build.md`.

## What landed (10 tasks, all green)

| Track | Tasks | Outcome |
| - | - | - |
| **J — Cleanup** | J1 | Dropped redundant `pool.acquire()` wrapper around `fetch_one()` in `agents/listing_intake/tools.py:persist_listing`. Regression test counts pool acquisitions and asserts zero. |
| **K — Webhook event emission** | K1, K2 | Onboarding emits `merchant.profile.created` after `create_merchant_profile`. Listing Intake emits `listing.created` after `persist_listing`. Both follow the non-fatal pattern: webhook failures surface as `webhook_status: error` on the response but never fail the primary write. |
| **L — Dispute resolution lifecycle** | L1 | `PATCH /v1/disputes/{id}` route. Validates resolution against `{accepted, rejected, withdrawn}`, scopes by partner ownership, returns 409 on already-resolved (append-only invariant), UPDATEs `resolved_at = NOW()`, emits `dispute.resolved` non-fatally. Added `DisputeResolution` Literal type to `shared/schemas.py`. |
| **M — Tests + deploy** | M1, M2, M3, M4 | End-to-end emit_event integration test (parametrised across all 4 event types, asserts signature verifies against sent bytes). Onboarding + Listing Intake manifests gained `WEBHOOK_SIGNING_KEY`. Onboarding + Listing Intake + Concierge redeployed. Closeout verification all green. |
| **N — Documentation** | N1, N2 | CLAUDE.md webhook semantics section gained Event ownership table + Non-fatal-emit + Resolution lifecycle bullets. Module map updated to list all three dispute endpoints. This closeout doc. |

## Verification

- `pytest tests/unit` — **118+ passed** (10 new tests from Phase 5)
- `pytest tests/integration -m integration` — **16+ passed** (5 new from M1)
- `ruff check .` — clean
- `mypy agents shared service` — clean
- All 5 agent evals @ threshold 0.85 — **100% pass**
- 5 Agent Engines live (Concierge, Pricing, Onboarding, Listing Intake, Dispute Triage) — all redeployed engines have correct env_vars (WEBHOOK_SIGNING_KEY on emitters, peer resources on dispatchers)
- All 5 `.env` `*_AGENT_RESOURCE` slots populated

## Key design decisions baked in

1. **Webhook failures are non-fatal at every emission site.** Onboarding, Listing Intake, and the dispute-resolution PATCH route all wrap `emit_event` in `try/except Exception` (with `# noqa: BLE001` for ruff). The primary write succeeds either way; the audit row in `webhook_deliveries` is the source of truth. A future Phase 6 retry worker will sweep `delivered_at IS NULL` rows.

2. **`dispute.resolved` emits from the gateway, not from a Dispute Triage tool.** Resolution is a structured CRUD operation (`pending → terminal`); no LLM in the path. The price re-derivation already happened at dispute-open time (Phase 4).

3. **Resolutions are closed-once.** `pending → accepted | rejected | withdrawn` only. PATCH returns 409 on any non-pending current state. No reopen workflow.

4. **`persist_listing` connection pattern matched the rest of the codebase.** `shared/db.py:fetch_one` acquires its own connection. The previous outer `async with pool.acquire():` wrapper double-acquired and would have exhausted the pool under sustained load. Regression test pins the new shape.

5. **Test isolation:** every emit-path test stubs `emit_event` directly. The existing `persist_listing` happy-path test was updated to add the stub when K2 landed, otherwise it would have reached the real DB. The pattern is now consistent across all writer-tool tests.

## Deferred from Phase 5 (independent plans)

| Item | Where | When |
| - | - | - |
| **B4 — A2A wrapper migration** (`AdkApp` → `vertexai.preview.reasoning_engines.A2aAgent`) | OpenItems_B4.md | Phase 6a — needs user decisions on cutover sequencing, `/.well-known/agent-card.json` redirect, contest-FAQ confirmation |
| **Async webhook retry worker** | new service / Cloud Run job | Phase 6b — sweeps `delivered_at IS NULL` rows, exponential backoff, idempotency |
| **H6 — Demo HTML rewrite** | `service/static/surplusas-merchant-demo.html` | Phase 6c — contest decision needed (rewrite / disclose / ship-as-is) |
| **Per-subscription inbound signature verification** | `secret_hash` field on `webhook_subscriptions` used for verifying inbound | Phase 6+ — only when we accept inbound webhooks |
| **M5** — Cloud Build path-filter Terraform | `infra/terraform/cloudbuild_pricing.tf` | Phase 7 hardening |
| **M6** — `_handle_cache` autouse-fixture cleanup | `tests/unit/test_a2a.py` | Phase 7 hardening |
| **L1–L5** — cosmetic / docs | various | Phase 7 hardening |

## Stale engines pending deletion authorization

After M2 + M3 redeploys, the previous engine IDs are stale. They cost storage/quota until deleted but don't affect functionality. Authorize whichever you want me to delete:

| Engine ID | Why stale |
| - | - |
| `2692568186737393664` | Old Onboarding (replaced after manifest gained WEBHOOK_SIGNING_KEY) |
| `6610787823479947264` | Old Listing Intake (replaced after manifest gained WEBHOOK_SIGNING_KEY) |
| `2898555093131460608` | Old Concierge (replaced after Phase 4 Dispute Triage forwarding) — *pending from Phase 4 if not already authorized* |
| `1796439821820887040` | Older Concierge from Phase 4's first wave — *pending from Phase 4* |

(The new Concierge ID will be assigned during M3's final Concierge redeploy and recorded in `.env` after deploy.)

## Manual end-to-end verification (recommended but not blocking)

```powershell
uv run python -m service.main
```

Then either:
- **Beat 1** (Phase 3): paste a draft into `/static/surplusas-merchant-demo.html` and verify the merchant onboarding flow now persists a row in `agents.merchant_profiles` AND attempts a `merchant.profile.created` webhook delivery (visible in `agents.webhook_deliveries`).
- **Beat 2** (Phase 4 + Phase 5): trigger a dispute via `POST /v1/listings/{id}/dispute`, verify the narration; then `PATCH /v1/disputes/{id}` with a resolution and check the `dispute.resolved` row in `agents.webhook_deliveries`.

The audit-row check via `scripts/ops_connect.sh` is the canonical "did the webhook fire" verification:

```sql
SELECT event_type, last_status_code, delivered_at, last_error
FROM agents.webhook_deliveries
ORDER BY created_at DESC LIMIT 10;
```
