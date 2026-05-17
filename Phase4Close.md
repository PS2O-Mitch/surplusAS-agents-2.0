# Phase 4 Closeout — Dispute Triage + Webhooks

**Shipped:** 2026-05-17. Plan: `~/.claude/plans/2026-05-17-phase-4-build.md`.

## What landed (21 tasks, all green)

| Track | Tasks | Outcome |
| - | - | - |
| **E — Dispute Triage agent** | E0–E7 (8) | 5 tools (`fetch_recommendation_log`, `request_reprice`, `diff_pressures`, `persist_dispute`, `emit_price_update_webhook`), prompt, agent wiring, 20-case eval. 15 unit tests. |
| **F — Webhooks infrastructure** | F1–F3 (3) | `shared.webhook_dispatcher` (HMAC-SHA256 + httpx), `shared.webhook_subscriptions` (CRUD), `shared.webhook_events.emit_event` (fan-out + audit-row). 14 unit tests. |
| **G — Gateway routes** | G1–G2 (2) | `POST /v1/listings/{id}/dispute`, `GET /v1/disputes/{id}`, `POST /v1/webhooks/subscriptions`, `DELETE /v1/webhooks/subscriptions/{id}`. 10 unit tests. |
| **H — Integration + deploy + CI** | H1–H5 (5) | Lateral integration test (Dispute → Pricing), Beat 2 e2e, Dispute Triage deployed + smoke harness, Concierge redeployed with peer wiring, CI eval gate, full closeout verification. |
| **I — Documentation** | I1–I2 (2) | CLAUDE.md updated (module map + webhook semantics section), this closeout doc. |

## Verification

- `pytest tests/unit` — 108+ pass
- `pytest tests/integration -m integration` — 11+ pass
- `ruff check .` — clean
- `mypy agents shared service` — clean (40+ source files)
- Evals @ threshold 0.85 for all 5 agents — pass_rate=1.000
- 5 Agent Engines live (Concierge, Pricing, Onboarding, Listing Intake, Dispute Triage)
- All 5 `.env` `*_AGENT_RESOURCE` slots populated

## Key design decisions baked in

1. **Webhook signing uses the repo-wide `WEBHOOK_SIGNING_KEY`** (from Secret Manager), NOT the per-subscription secret. The per-subscription `secret_hash` column on `agents.webhook_subscriptions` is reserved for future inbound verification (Phase 5).
2. **Delivery model is sync-with-audit-row.** The emitting agent INSERTs a `webhook_deliveries` row, POSTs, UPDATEs the row with status. No background retry worker yet — failed rows accumulate with `delivered_at IS NULL` for a future sweep service.
3. **`price.updated` threshold is `|delta| > $0.25` strictly.** Exactly $0.25 does not fire (avoids edge oscillations on coefficient nudges).
4. **`request_reprice` extracts structured fields from the Pricing stream's `tool_calls`** (no narration parsing). This preserves the Phase 3 "`applied_pressures` round-trips verbatim" invariant.
5. **Dispute entry points are dual.** `POST /v1/listings/{id}/dispute` for structured submissions; Concierge's `route_to_dispute_triage` for conversational. Both terminate at the same agent.
6. **`shared.db.fetch_one` acquires its own connection.** Phase 3 E1 review caught the leak pattern. Phase 4 code uses the cleaner shape: `await init_pool()` then call `fetch_one`/`execute`/`fetch_all` directly. **`agents/listing_intake/tools.py:persist_listing` still has the leak pattern** — flagged for Phase 5/7 cleanup.

## Deferred from Phase 4 scope (open items)

| Item | Where | When |
| - | - | - |
| **Async webhook retry worker** | new `scripts/webhook_worker.py` or Cloud Run job | Phase 5/6 |
| **Dispute resolution lifecycle endpoints** | `PATCH /v1/disputes/{id}` to move `pending → accepted/rejected/withdrawn` | Phase 5 |
| **Per-subscription inbound signature verification** | use `secret_hash` to verify inbound webhooks if we accept any | Phase 5 |
| **`emit_event` wiring for `merchant.profile.created`** | Onboarding agent emits after `create_merchant_profile` | Phase 5 |
| **`emit_event` wiring for `listing.created`** | Listing Intake agent emits after `persist_listing` | Phase 5 |
| **`dispute.resolved` webhook** | emitted on resolution lifecycle, paired with the resolution endpoint | Phase 5 |
| **`agents/listing_intake/tools.py:persist_listing` connection-leak cleanup** | rewrite to the E1 pattern (no outer `pool.acquire()` wrapper) | Phase 5 or 7 |

## Open items carried from prior phases (unchanged)

- **B4 / OpenItems_B4** — A2A wrapper migration (`AdkApp` → `A2aAgent`). Phase 5.
- **H6** — demo HTML rewrite. Phase 5.
- **M3** — Pricing `dry_run` mode (optional).
- **M4** — `evals.runner --mode remote` end-to-end. Phase 7.
- **M5** — Cloud Build path-filter Terraform. Phase 7.
- **M6** — `_handle_cache` autouse-fixture cleanup. Phase 7.
- **L1–L5** — cosmetic / docs.

## Manual Beat 2 verification

Once the user runs the manual dispute-flow dry-run against the deployed gateway, this section gets the result. As of this commit, automated verification (unit + integration + smoke ping) is green; the human-in-the-loop check is the remaining gate.

Steps for the manual check:

```powershell
uv run python -m service.main
# Open http://127.0.0.1:8080 and either:
#   (a) use the static demo page to dispute an existing listing
#   (b) POST /v1/listings/<id>/dispute with a Bearer token via curl
# Verify:
#   - response narration names the dominant pressure mover with verbatim values
#   - new row in agents.disputes (via scripts/ops_connect.sh psql)
#   - new row in agents.recommendation_log with replay_of=<original_id>
#   - if |delta| > $0.25: row in agents.webhook_deliveries with delivered_at set (or last_error populated)
```
