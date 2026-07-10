# surplusAS-agents-2.0

SurplusAS multi-agent service. Hub-and-spoke topology — one FastAPI service on **Fly.io**, all five ADK agents running **in-process**:

- **Concierge** (`gemini-2.5-pro`) — single externally-addressable agent; routes merchant turns to specialists.
- **Pricing** (`gemini-2.5-flash`) — thin LLM shell over the deterministic pricing engine.
- **Onboarding** (`gemini-2.5-flash`) — converts merchant freeform into a `MerchantProfile`.
- **Listing Intake** (`gemini-2.5-flash`) — parses drafts; calls Pricing laterally for live anchors.
- **Dispute Triage** (`gemini-2.5-pro`) — replays prior recommendations against fresh anchors; narrates pressure deltas.

Customer surface: **REST + webhooks** (HMAC-SHA256 signed, at-least-once, 5 retries).

Models run on the **Gemini Developer API** (`GOOGLE_API_KEY`; no GCP project). Postgres is **Supabase** over a plain asyncpg DSN. The internal mesh (`shared/a2a.py`) runs each agent through a cached in-process ADK `Runner` — no network hop between agents.

**Open A2A surface:** every agent can also be published over the **open Agent-to-Agent protocol** via ADK's native `to_a2a()` adapter (`service/a2a_app.py`) — a discoverable Agent Card at `/.well-known/agent-card.json` plus a JSON-RPC 2.0 endpoint, so any A2A-speaking enterprise agent (ADK, LangGraph, CrewAI, …) can discover and call it. Reproduce with `uv run python -m scripts.verify_a2a` (uses the stock `a2a-sdk` client; no cloud creds needed).

---

## Audit posture (Google for Startups AI Agents Challenge — Track 3)

This repository was created on 2026-04-28 (within the contest period 2026-04-22 → 2026-06-05) for the **Google for Startups AI Agents Challenge — Track 3 (Refactor for Marketplace + Gemini Enterprise)**.

The deterministic pricing engine consumed via the `vendor/surplusas-pricing` git submodule was developed in a separate, pre-existing repository (`surplusAS-pricing-intel`). It is included as a **backend dependency** — the same way any project depends on `pydantic`, `google-adk`, or `fastapi`. The agents, the FastAPI gateway, the multi-tenant integration surface, the A2A orchestration, the eval harness, and all observability code in this repository are **net-new** to the contest period.

A signed git tag at the first commit (`v0.0.0-contest-start`) anchors the repository's birthdate. `git log --oneline` is the audit trail. (Post-contest, the service was migrated off the GCP runtime — Vertex Agent Engine → in-process ADK Runners, Cloud Run → Fly.io, Cloud SQL → Supabase — with Gemini kept as the model. See the `offgcp-*` commits.)

---

## Quick start

```bash
# Install deps
uv sync

# Run the gateway locally (.env needs GOOGLE_API_KEY + DATABASE_URL)
uv run python -m service.main

# Run unit tests
uv run pytest tests/unit

# Run lint + type check
uv run ruff check .
uv run mypy agents shared service

# Golden evals — local mode is pure-Python (CI); remote mode runs the live model in-process
uv run python -m evals.runner --agent pricing --threshold 0.85 --mode local
uv run python -m evals.runner --agent pricing --threshold 0.85 --mode remote
```

## Database provisioning (Supabase)

Fresh-Postgres standup order (details in `scripts/provision_supabase.sql`):

1. `vendor/surplusas-pricing/sql/001_reference_prices.sql`, then `002_pricing_coefficients.sql`
2. `scripts/provision_supabase.sql` Section A (app role + `public.partner_keys`)
3. `DATABASE_URL=<owner-dsn> uv run python scripts/apply_schema.py` (creates the `agents` schema)
4. `scripts/provision_supabase.sql` Section B (grants; enforces the append-only audit log)
5. `DATABASE_URL=<owner-dsn> uv run python scripts/seed_demo_merchant.py` (demo key + coefficients + anchors)

Use the **session-mode pooler or direct** connection string — transaction-mode pgBouncer (port 6543) breaks asyncpg prepared statements.

## Deploy (Fly.io)

```bash
fly launch --no-deploy      # first time only
fly secrets set GOOGLE_API_KEY=... DATABASE_URL=... WEBHOOK_SIGNING_KEY=...
fly deploy
```

Live smoke once deployed:

```bash
curl -s https://<app>.fly.dev/healthz
curl -s -X POST https://<app>.fly.dev/v1/concierge \
     -H "Authorization: Bearer sk_demo_surplus_2026" \
     -H "content-type: application/json" \
     -d '{"message":"I have 10 day-old turkey sandwiches, retail $12 each, expiring in 4 hours"}'
```

## A2A surface (open protocol)

```bash
# Verify locally with the stock a2a-sdk client (no cloud creds needed):
uv run python -m scripts.verify_a2a pricing
#   -> discovers /.well-known/agent-card.json, exercises the JSON-RPC endpoint

# Serve one agent's A2A surface:
A2A_AGENT=pricing uv run uvicorn service.a2a_app:app --port 8080
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the agent topology (hub-and-spoke with two lateral A2A edges), the Beat 1 + Beat 2 sequence diagrams, and the hard guardrails on the data plane.

## License

Proprietary. © 2026 SurplusAS.
