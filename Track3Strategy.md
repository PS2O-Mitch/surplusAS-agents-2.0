# Track3Strategy.md — `surplusAS-agents-2.0`

Audience: any contributor (AI agent, human, internal reviewer, or contest judge) who wants the *business and strategic* picture of this repo. The per-PR coding contract lives in `CLAUDE.md`; the implementation plan lives at `~/.claude/plans/the-ending-of-the-shimmering-reef.md`. Section numbers track those of the master plan §14 so cross-references resolve cleanly; sections 2, 5, 6, 8, 10, 11, 12 are intentionally elided in this submission-tight version.

## §1. Executive summary

SurplusAS is a B2B Agent Service that brings **deterministic, audit-grade dynamic pricing** to surplus food and goods inventory. This repository is the multi-agent gateway customers integrate into existing systems without changing their workflow: they POST a draft listing or a dispute, and they receive a priced response plus signed webhooks. The Concierge agent is the only externally-addressable surface; four specialists (Pricing, Onboarding, Listing Intake, Dispute Triage) communicate with it over A2A behind the gateway. We are entering Track 3 of the Google for Startups AI Agents Challenge because we already operate a working deterministic pricing engine (`surplusAS-pricing-intel`) and Track 3 is explicitly the "refactor your existing functional agent for production" track. Net-new contest work is this repo; the engine is consumed as a vendor library, in the same way any project depends on `pydantic` or `google-adk`.

## §3. Track 3 alignment

The four Track 3 mandates and how this repo satisfies each:

- **B2B.** Customers are merchants (SMB grocers, restaurants, prepared-food retailers in Tampa Bay → wider US food retail). They integrate via REST and outbound webhooks; no end-consumer surface.
- **Cloud-Native.** FastAPI on Cloud Run, asyncpg on Cloud SQL via the Python Connector, Secret Manager for credentials, Cloud Trace for distributed tracing, Cloud Logging for structured logs, Cloud Build for per-service CI/CD.
- **Vertex AI.** Every agent is a Vertex AI Agent Engine ReasoningEngine. Gemini 2.5 Pro for Concierge + Dispute Triage (reasoning); Gemini 2.5 Flash for Pricing, Onboarding, Listing Intake (lower-latency tool routing). No fine-tuning; base models + prompting + tools.
- **A2A.** All five agents communicate via authenticated A2A (ID-token-per-audience, cached) with OpenTelemetry trace propagation across hops. The two lateral edges (Listing Intake → Pricing for live anchors; Dispute Triage → Pricing for replay) are by design — they preserve auditability across the dispute flow that Beat 2 demonstrates.

Submission packet references the contest rules by filename: `Google for Startups AI Agents Challenge Rules FINAL.pdf`. New-projects-rule audit posture: this repo was created 2026-04-29 (signed git tag `v0.0.0-contest-start` at first commit, public history on GitHub); the vendored engine pre-dates the contest period and is treated as a backend dependency, not contest work product.

## §4. Three-repo ecosystem

| Repo | Role | Contest status |
|---|---|---|
| [`surplusAS-pricing-intel`](https://github.com/PS2O-Mitch/surplusAS-pricing-intel) | Deterministic pricing engine + Tampa Bay reference corpus + nightly feedback loop | Pre-existing; consumed as `vendor/surplusas-pricing` submodule; will become a pip package post-contest |
| [`SurplusAS-API-2.0`](https://github.com/PS2O-Mitch/SurplusAS-API-2.0) | Earlier monolith API; pattern source for A2A, auth, tracing, the static merchant-view UI | Pre-existing; not part of submission; no code imported |
| [`surplusAS-agents-2.0`](https://github.com/PS2O-Mitch/surplusAS-agents-2.0) | This repo — multi-agent gateway, contest submission, future production agent surface | **Net-new in the contest period** |

Why three repos and not one: the pricing engine has its own release cadence and customer (SurplusAS internal data team), the agent service has a different one (SMB merchant integration), and the API-2.0 patterns are reference material we adapt — not import. Treating each as an independent unit keeps audit boundaries clean and lets the engine ship as a pip package without dragging the gateway with it.

## §7. Audit-trail story

The single most important property of this service is that every price recommendation is reproducible from the audit log alone. The flow:

1. Pricing receives a `PricingInput` (category, region, condition, days-to-expiry, photo, etc.) and resolves coefficients + anchor deterministically against `public.pricing_coefficients` (read-only, owned by the pricing-intel repo) and `agents.reference_prices`.
2. `pricing_engine.formula.recommend()` returns `(price, applied_pressures, formula_version, coefficients_version, anchor_p50, anchor_source, anchor_region)`.
3. Pricing INSERTs a row into `agents.recommendation_log` carrying ALL of those fields plus the verbatim `PricingInput` as JSONB. **Append-only**: replays write NEW rows with `replay_of=<orig_id>`; the original is never UPDATEd (guardrail #3 in `CLAUDE.md`).
4. `applied_pressures` round-trips through the Concierge's narration, the REST response, and the `price.updated` / `dispute.resolved` webhook payloads.
5. A regulator (or a skeptical merchant) auditing any price can query one row in `agents.recommendation_log`, replay the formula with the recorded `coefficients_version`, and verify the price by-the-cent.

This is why we refuse to let the LLM invent prices, why coefficients are read-only here, and why fine-tuning is off — it would dissolve the deterministic chain.

## §9. Success criteria

**Contest-only (judging-rubric-aligned):**
- Public GitHub repo (✅ pushed 2026-05-18)
- Three-minute demo video showing Beat 1 + Beat 2 end-to-end with Cloud Trace screenshots
- Architecture diagram (✅ `docs/architecture.md`)
- All five golden eval suites ≥0.85 (✅ verified Phase 6 closeout)
- New-projects-rule audit posture (✅ `v0.0.0-contest-start` tag + public commit history)

**Production-launch (post-contest, separate trackers):**
- First paying merchant (Tampa Bay pilot)
- Successful audit response on a regulator request (auditability proven in production, not just simulation)
- Sub-500ms p95 pricing latency at 10 RPS (load test scheduled this phase)
- Eval suite ≥0.85 maintained on every release tag
- Webhook redelivery success rate >99.5% over a trailing 7-day window (alerting infra landing this phase)

## References

- Contest rules: `Google for Startups AI Agents Challenge Rules FINAL.pdf`
- Per-PR coding contract: [`CLAUDE.md`](CLAUDE.md)
- Implementation plan: `~/.claude/plans/the-ending-of-the-shimmering-reef.md`
- Architecture diagram: [`docs/architecture.md`](docs/architecture.md)
- Three repo URLs in §4 above
