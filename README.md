# surplusAS-agents-2.0

SurplusAS multi-agent service. Hub-and-spoke topology on **Vertex AI Agent Engine**:

- **Concierge** (`gemini-2.5-pro`) — single externally-addressable agent; routes merchant turns to specialists via A2A.
- **Pricing** (`gemini-2.5-flash`) — thin LLM shell over the deterministic pricing engine.
- **Onboarding** (`gemini-2.5-flash`) — converts merchant freeform into a `MerchantProfile`.
- **Listing Intake** (`gemini-2.5-flash`) — parses drafts; calls Pricing laterally for live anchors.
- **Dispute Triage** (`gemini-2.5-pro`) — replays prior recommendations against fresh anchors; narrates pressure deltas.

Customer surface: **REST + webhooks** (HMAC-SHA256 signed, at-least-once, 5 retries).

**Open A2A surface (Track-3 interoperability mandate):** every agent is published over the **open Agent-to-Agent protocol** via ADK's native `to_a2a()` adapter (`service/a2a_app.py`) — a discoverable Agent Card at `/.well-known/agent-card.json` plus a JSON-RPC 2.0 endpoint, so any A2A-speaking enterprise agent (ADK, LangGraph, CrewAI, …) can discover and call it. Reproduce with `uv run python -m scripts.verify_a2a` (uses the stock `a2a-sdk` client; no GCP creds needed).

Internal mesh: hub-and-spoke inter-agent calls run over Vertex AI Agent Engine's managed, ID-token-authenticated streaming channel (`async_stream_query` against the deployed reasoning engines, in `shared/a2a.py`).

---

## Audit posture (Google for Startups AI Agents Challenge — Track 3)

This repository was created on 2026-04-28 (within the contest period 2026-04-22 → 2026-06-05) for the **Google for Startups AI Agents Challenge — Track 3 (Refactor for Marketplace + Gemini Enterprise)**.

The deterministic pricing engine consumed via the `vendor/surplusas-pricing` git submodule was developed in a separate, pre-existing repository (`surplusAS-pricing-intel`). It is included as a **backend dependency** — the same way any project depends on `pydantic`, `google-adk`, or `fastapi`. The agents, the FastAPI gateway, the multi-tenant integration surface, the A2A orchestration, the eval harness, the Terraform/Cloud Build infra, and all observability code in this repository are **net-new** to the contest period.

A signed git tag at the first commit (`v0.0.0-contest-start`) anchors the repository's birthdate. `git log --oneline` is the audit trail.

---

## Quick start

```bash
# Install deps
uv sync

# Run the gateway locally
uv run python -m service.main

# Run unit tests
uv run pytest tests/unit

# Run lint + type check
uv run ruff check .
uv run mypy agents shared service

# Run golden evals (requires Vertex AI access; run per agent)
uv run python -m evals.runner --agent pricing --threshold 0.85
```

## A2A surface (open protocol)

Each agent is served over the open A2A protocol via ADK's `to_a2a()` adapter.

```bash
# Verify locally with the stock a2a-sdk client (no GCP creds needed):
uv run python -m scripts.verify_a2a pricing
#   -> discovers /.well-known/agent-card.json, exercises the JSON-RPC endpoint

# Serve one agent's A2A surface locally:
A2A_AGENT=pricing uv run uvicorn service.a2a_app:app --port 8080

# Deploy it to Cloud Run (builds the Dockerfile via Cloud Build, wires the
# agent SA + Cloud SQL + DB secret, advertises the live URL in the Agent Card):
scripts/deploy_a2a_cloudrun.sh pricing          # or scripts/deploy_a2a_cloudrun.ps1
```

After deploy, the card is public at `https://<service-url>/.well-known/agent-card.json`.

**Live endpoints — all five agents are A2A-discoverable on Cloud Run** (public Agent Card, no auth):

| Agent | Agent Card URL |
|---|---|
| Concierge | https://surplusas-a2a-concierge-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json |
| Pricing | https://surplusas-a2a-pricing-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json |
| Onboarding | https://surplusas-a2a-onboarding-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json |
| Listing Intake | https://surplusas-a2a-listing-intake-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json |
| Dispute Triage | https://surplusas-a2a-dispute-triage-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json |

```bash
# Discover any agent over the open protocol (no auth required):
curl -s https://surplusas-a2a-pricing-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json | python -m json.tool
```

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the agent topology (hub-and-spoke with two lateral A2A edges), the Beat 1 + Beat 2 sequence diagrams, and the hard guardrails on the data plane. The implementation plan lives at `~/.claude/plans/the-ending-of-the-shimmering-reef.md`.

## License

Proprietary. © 2026 SurplusAS.
