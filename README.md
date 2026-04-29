# surplusAS-agents-2.0

SurplusAS multi-agent service. Hub-and-spoke topology on **Vertex AI Agent Engine**:

- **Concierge** (`gemini-2.5-pro`) — single externally-addressable agent; routes merchant turns to specialists via A2A.
- **Pricing** (`gemini-2.5-flash`) — thin LLM shell over the deterministic pricing engine.
- **Onboarding** (`gemini-2.5-flash`) — converts merchant freeform into a `MerchantProfile`.
- **Listing Intake** (`gemini-2.5-flash`) — parses drafts; calls Pricing laterally for live anchors.
- **Dispute Triage** (`gemini-2.5-pro`) — replays prior recommendations against fresh anchors; narrates pressure deltas.

Customer surface: **REST + webhooks** (HMAC-SHA256 signed, at-least-once, 5 retries).
Internal: **A2A** (Google ID-token-authenticated POST `/v1/agent`).

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

# Run golden evals (requires Vertex AI access)
uv run python -m evals.runner --agent all --threshold 0.85
```

## Architecture

See [`/docs/architecture.md`](docs/architecture.md) (TODO Week 1) for the agent topology, A2A edges, and data flow. The implementation plan lives at `~/.claude/plans/the-ending-of-the-shimmering-reef.md`.

## License

Proprietary. © 2026 SurplusAS.
