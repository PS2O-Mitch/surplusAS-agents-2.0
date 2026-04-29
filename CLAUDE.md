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

## Don't

- Don't add new SDKs or model providers without updating the plan and `pyproject.toml` together.
- Don't reimplement pricing logic — read it from the submodule and call it.
- Don't write to `agents.recommendation_log` outside `agents/pricing/engine_adapter.py`.
- Don't bypass `shared/a2a.py` for inter-agent calls — it owns ID-token caching and trace propagation.
- Don't introduce hard FK constraints across the `public` ↔ `agents` schema boundary; validate logically in app code.
