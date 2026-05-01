# Phase 2 Code Review — Open Items

Reviewer: Claude (Opus 4.7) — 2026-04-30
Subject: Pricing agent (Phase 2) per `familiarize-yourself-with-track3-md-steady-pine.md`
Contest: Google for Startups AI Agents Challenge — Track 3

## Summary

The Pricing agent code itself is **solid**: deterministic adapter is the only writer to `agents.recommendation_log`, replay path correctly writes a NEW row with `replay_of` set, tools wrap the adapter cleanly, and the prompt enforces the no-invent-numbers guardrail. All four CI gates pass locally:

| Gate | Result |
|---|---|
| `uv run ruff check .` | PASS (0 issues, 26 files) |
| `uv run mypy agents shared service` | PASS |
| `uv run pytest tests/unit -v` | 18/18 passed |
| `uv run pytest tests/unit/test_pricing_engine_adapter.py --cov-fail-under=100` | 10/10, **100%** coverage |
| `uv run python -m evals.runner --agent pricing --threshold 0.85 --mode local` | 52/52 (pass_rate 1.000) |
| `uv run pytest tests/integration -m integration` | 3/3 passed |

That said, the work has not landed in git, several Phase 0 / Phase 2 prerequisites are skipped, and there are concrete bugs and contest-mandate concerns that need to clear before Phase 3.

Findings are tagged **[BLOCKER]** (must fix before Phase 3 / submission), **[HIGH]** (fix before submission), **[MED]** (track and resolve in-phase), **[LOW]** (cosmetic / future).

---

## BLOCKERS

### B1. All Phase 2 work is uncommitted [BLOCKER]

`git log --oneline` ends at `81297f9 scripts: Python-based schema apply…` (2026-04-29). Every file produced this phase — `agents/pricing/engine_adapter.py`, `tools.py`, `prompts.py`, the `agent.py` rewrite, all of `evals/`, `infra/cloudbuild/`, `scripts/deploy_agent.py`, `tests/integration/`, `tests/unit/test_a2a.py`, `tests/unit/test_pricing_engine_adapter.py` — is untracked or staged-modified. A laptop crash, a stray `git stash drop`, or `git clean -fd` loses the entire phase.

Also unstaged: the Phase 1 A2A SDK rewrite (`shared/a2a.py`, `shared/config.py`, `shared/pricing_intel.py`, `agents/pricing/agent.py`, `.env.example`).

**Remediation:** stage and commit Phase 1 and Phase 2 in coherent slices before any further work:
1. Phase 1 — `shared/a2a.py` SDK rewrite + `shared/config.py` rename + `.env.example` + `tests/unit/test_a2a.py`.
2. Phase 2A — `agents/pricing/{engine_adapter,tools,prompts}.py`, `agents/pricing/agent.py` body, `tests/unit/test_pricing_engine_adapter.py`.
3. Phase 2B — `evals/` (runner, metrics, golden generator, frozen `pricing.jsonl`), `tests/integration/test_a2a_concierge_pricing.py`.
4. Phase 2C — `infra/cloudbuild/pricing.cloudbuild.yaml`, `scripts/deploy_agent.py`.

Tag a phase-completion checkpoint (`v0.0.1-phase2-complete`) so audit-window claims hold.

### B2. CI will break the moment Phase 2 lands [BLOCKER]

`tests/unit/test_pricing_engine_adapter.py` does:

```python
from shared.pricing_intel import (
    AppliedPressures, Coefficients, PiecewiseCurve, PricingInput, Recommendation,
)
```

`shared/pricing_intel.py:23-49` wraps the `pricing_engine.*` re-exports in a `try/except ImportError` that swallows the failure and leaves `__all__ = []`. When the submodule is absent (which is exactly what `.github/workflows/ci.yml:22` enforces with `submodules: false`), those names are **never bound**, so the test file fails at import collection — `tests/unit` goes red.

Three options to remediate, pick one:

1. **Provision `SUBMODULE_TOKEN`** (the Phase 0 step 4 [GATE: human] item) and flip `submodules: false` → `submodules: recursive`. This is the cleanest path and unblocks Cloud Build evals as well (see B3).
2. Move the engine-types import inside the test functions and skip the test module if `shared.pricing_intel.__all__` is empty.
3. Make `shared/pricing_intel.py` re-raise on missing submodule with a clear error and keep CI red until the PAT lands. Worse: turns a recoverable signal into a hard stop.

Option 1 is what the spec intends. Don't merge Phase 2 to `main` until B2 is resolved.

### B3. Cloud Build pipeline will fail at the `evals` and `unit-tests` steps [BLOCKER]

`infra/cloudbuild/pricing.cloudbuild.yaml` runs in `ghcr.io/astral-sh/uv:0.5.5-python3.12-bookworm`. Cloud Build's default checkout step does **not** initialize git submodules. Both `unit-tests` (via `shared.pricing_intel`) and `evals` (which imports the engine directly) will fail.

**Remediation:** add an explicit submodule fetch step before `install`:

```yaml
- id: fetch-submodules
  name: gcr.io/cloud-builders/git
  entrypoint: bash
  args:
    - -c
    - |
      set -euo pipefail
      git config --global url.https://x-access-token:$$GITHUB_PAT@github.com/.insteadOf https://github.com/
      git submodule update --init --recursive
  secretEnv: ['GITHUB_PAT']
availableSecrets:
  secretManager:
    - versionName: projects/$PROJECT_NUMBER/secrets/submodule-pat/versions/latest
      env: 'GITHUB_PAT'
```

(or vendor the `pricing_engine` package into the deploy bundle — see H3 below.)

### B4. Track 3 "A2A Interoperability" mandate may not be satisfied [BLOCKER — contest]

The contest rules (page 4) require:

> **A2A Interoperability**: Your agent's communication layer must utilize the **Agent-to-Agent (A2A) protocol**, ensuring it can be seamlessly discovered by and coordinate with other enterprise agents.

`shared/a2a.py` calls `vertexai.agent_engines.AgentEngine.async_stream_query(...)` — that is Google Cloud's proprietary Agent Engine SDK transport, **not** the open A2A protocol (`a2aproject.org`, ADK 2.0's `agent.to_a2a()` adapter). A judge taking the mandate literally could read the current implementation as failing the criterion.

**Remediation options** (any one of these likely satisfies the criterion; verify against the contest FAQ / Devpost forum):

1. **Wrap each ADK Agent in `to_a2a()` and expose an A2A endpoint** alongside the Agent Engine deployment. Have `shared/a2a.py` route inter-agent traffic through the A2A endpoint instead of (or in addition to) `async_stream_query`. This is the most defensible reading of the rule.
2. Add a clear note to the submission's text description + architecture diagram that "Agent Engine's runtime is A2A-compliant under the hood — `async_stream_query` is Google's transport for A2A messages between deployed agents," with a link to the relevant Vertex docs page proving it. Lower-confidence; depends on a judge agreeing.
3. Implement A2A as a parallel surface only on the customer-facing edge (Concierge) so external agents can discover and call SurplusAS, while preserving the SDK transport internally. Likely sufficient for "discoverable by other enterprise agents."

Confirm the chosen approach by Phase 5 at the latest — Concierge is when the externally-addressable surface lands.

---

## HIGH

### H1. `applied_pressures` boolean flags are silently coerced to floats at the entry boundary [HIGH]

`shared/schemas.py:81` declares `RecommendationLogEntry.applied_pressures: dict[str, float]`. The engine returns an `AppliedPressures` object that includes `clamped_to_floor: bool` and `clamped_to_retail: bool`. Pydantic v2's lax mode coerces those bools to `0.0` / `1.0` when validating the entry. Verified in a 60-second repro:

```text
{'base': 0.1, 'expiry': 0.3, ..., 'clamped_to_floor': 0.0, 'clamped_to_retail': 0.0}
<class 'float'>
```

Storage is fine: `engine_adapter._INSERT_SQL` uses `json.dumps(pressures)` against the raw dict, so the JSONB column keeps `clamped_to_floor: false`. But every read path that returns a `RecommendationLogEntry` (every tool that does `entry.model_dump(mode="json")`) ships `0.0/1.0` to the consumer. That:

- Loses information (can't tell `True` from a `1.0` numeric pressure).
- Violates CLAUDE.md guardrail #2 ("`applied_pressures` … must round-trip through every layer").
- Will surface in the Concierge narration as "clamped_to_floor: 0" instead of a clean clamping audit signal in Beat 2.

The Phase 2 unit tests don't catch this because they assert on the raw dict that goes into `json.dumps`, never on the entry attribute (the one place where it would be visible — the test inspects `entry.applied_pressures["expiry"]` but never the bool keys).

**Remediation:** change `shared/schemas.py:81` to either:

```python
applied_pressures: dict[str, float | bool]   # minimal, matches storage
```

…or, better, give it a real shape:

```python
from shared.pricing_intel import AppliedPressures
applied_pressures: AppliedPressures
```

Then update tests to assert `entry.applied_pressures["clamped_to_floor"] is False` (currently they only assert against the JSONB-bound bytes, not the entry attribute). The cleaner fix also matches what `_resolve_and_recommend`'s own return-type hint already says: `dict[str, float | bool]` (`engine_adapter.py:91`).

### H2. `deploy_agent.py` requirements list is incomplete — deployed agent will fail at startup [HIGH]

`scripts/deploy_agent.py:73-81` hard-codes a `requirements` list it passes to `agent_engines.create()`. It's missing:

- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-gcp-trace`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-httpx` — `shared/tracing.py` imports OTel at module load.
- `pyyaml` — `scripts/deploy_agent.py` itself imports `yaml`. Less of an Agent Engine concern (it's not vendored), but flag for the Cloud Build deploy step.
- `tenacity` — listed in `pyproject.toml`; will be needed by Phase 6's webhook dispatcher.
- `argon2-cffi` — listed in `pyproject.toml`; will be needed by webhook subscription auth.

OTel is the urgent one because `from shared.tracing import a2a_client_span` is on the import path of every agent that uses `shared.a2a` (which is all five). The deployed Pricing agent will `ImportError` on first request.

**Remediation:** derive `requirements` from `pyproject.toml` (e.g. `uv pip compile pyproject.toml --extra runtime -o requirements-deploy.txt`) instead of maintaining a hand-curated list. Or at minimum add the OTel + tenacity entries.

### H3. `extra_packages` paths in `deploy_agent.py` may not bundle correctly [HIGH]

`scripts/deploy_agent.py:86-90`:

```python
extra_packages = [
    str(REPO_ROOT / "agents"),
    str(REPO_ROOT / "shared"),
    str(REPO_ROOT / "vendor" / "surplusas-pricing"),
]
```

`agent_engines.create(extra_packages=…)` expects either pip-installable directories (with a top-level `pyproject.toml` / `setup.py`) or wheel files. Passing bare module directories typically results in the SDK uploading them as a tarball that the runtime can't pip-install. The vendor pricing repo specifically has no `pyproject.toml` (per `vendor/surplusas-pricing/CLAUDE.md` — "Greenfield"), so this path is the most likely to silently fail.

**Remediation:** verify against the actual SDK semantics for the installed `google-cloud-aiplatform` version. Common patterns:

1. Build a wheel for the local repo (`uv build`) and pass the `.whl` path.
2. Use `extra_packages=[str(REPO_ROOT)]` (the whole repo, with `pyproject.toml`) and let the SDK pip-install it. Currently `pyproject.toml` packages `["agents", "shared", "service"]`, so you'd need to add an `agents.pricing` import that pulls everything transitively.
3. For the vendor engine, write a minimal `vendor/surplusas-pricing/pyproject.toml` upstream and bump the submodule.

Until this is resolved, the cloud-build `deploy` step is unverifiable.

### H4. No deployed-agent smoke check has been performed [HIGH]

Phase 2 exit-checks list:

> `vertexai.agent_engines.get(resource).async_query(input={"mode":"price_listing", "input": <canonical fixture>})` returns a non-empty `applied_pressures` dict.

This step has not been run. `agents/pricing/manifest.yaml` has the SA + display name, but `pricing_agent_resource` is empty in `shared/config.py:49`. Until a real deploy lands and a real smoke call returns a recommendation, the Cloud Build deploy step (B3) can't be exercised end-to-end.

This is the hardest item to fake-test, because it's also the one that catches the most subtle issues (SA missing `aiplatform.user`, vendor engine not bundled, OTel deps missing — see H2/H3).

**Remediation:** schedule a manual deploy + smoke run as the first task of Phase 3 (or in parallel with Phase 3 on a side branch). Don't gate Phase 5 on it — Phase 5 is when the gateway lights up the demo, and an undeployed Pricing breaks the entire Beat 1 flow.

### H5. Phase 0 prerequisites partially skipped [HIGH]

The plan is explicit that Phase 0 closes three blockers before any agent work:

1. `terraform plan` + `terraform apply` (creates 6 SAs, 2 secrets, Cloud SQL user) — **[GATE: human]**, not done. `pricing_agent_resource` env var has nowhere to land yet.
2. Schema grants via `scripts/grant_agents_app.sql` against the `postgres` superuser — not done; the `surplusas_agents_app` user can't write to `agents.*` until this runs.
3. `SUBMODULE_TOKEN` PAT — **[GATE: human]**, not provisioned (per commit `65f218b ci: disable submodule clone until cross-repo PAT is provisioned`).

Phase 2 was started before any of these closed. The deployed-agent smoke (H4) cannot succeed until at least #1 and #2 are done.

**Remediation:** treat these as blocking Phase 2 *deployment* (not Phase 2 *coding*, which is fine). Sequence: provision SUBMODULE_TOKEN (closes B2 + B3), run `terraform apply` and the grant SQL, capture the resource name from the first deploy, plumb it into `pricing_agent_resource`.

### H6. `service/static/surplusas-merchant-demo.html` is copied wholesale from a prior project [HIGH — contest]

Per the plan Phase 0 step 5:

```bash
cp ../SurplusAS-API-2.0/static/surplusas-merchant-demo.html service/static/
```

The contest rules ([page 5, "New Projects Only"](#)) say:

> Projects must be newly created by the entrant during the Contest Period. The Project must be your original creation **not a modification or extension of Your or anyone else's existing work**.

The CLAUDE.md acknowledges the borrow ("This repo borrows patterns … but does not import its code") — but the static UI is not "patterns," it's a verbatim copy. It is also visible to judges as part of the Beat-1 demo.

**Remediation options:**

1. Rewrite the demo HTML against the new `/v1/concierge` REST contract during Phase 5. A clean rewrite is defensible.
2. Disclose the reuse in the Devpost text description ("Static demo UI adapted from prior internal SurplusAS API tooling, included as a thin client; all core agent code is new for this contest"). This satisfies the Third-Party Integrations clause as long as the demo UI is not the substance of the submission.
3. Ship without the demo HTML — just a `curl` script for judging — and lose some demo polish.

Pick one before Phase 7 (video shoot). The safest is #1.

---

## MED

### M1. Adapter uses `shared.db._require_pool` (private) [MED]

`agents/pricing/engine_adapter.py:32` and `tools.py:24` import `_require_pool` from `shared.db`. The leading underscore is a conventional "this is internal" marker. If you want the adapter to be the canonical external surface, expose a public `require_pool` (or move the enforcement into `pool.acquire`). Otherwise rename `_require_pool` → `require_pool` to match the de-facto contract.

### M2. Prompt example mentions a pressure-delta the tool doesn't return [MED]

`agents/pricing/prompts.py:55-56`:

> "Replayed under fresh coefficients: new price $6.50, expiry pressure decreased from 0.45 to 0.32."

The `replay_recommendation` tool returns only the *new* `applied_pressures`. The model has no first-class access to the original row's pressures, so this exemplar invites the model to hallucinate the prior numbers. Phase 6's Dispute Triage owns `diff_pressures`; that is the right home for "X decreased from A to B" narration.

**Remediation:** delete the second example sentence, or change it to "Replayed under fresh coefficients: new price $6.50, expiry now 0.32." The diff phrasing belongs in Phase 6 prompts.

### M3. Pricing tools have no read-only access pattern for "what would this price?" inspection [MED]

`lookup_anchor_tool` exists, but the most common merchant question — "give me a quote without committing the listing" — needs the full formula to run **without** writing a `recommendation_log` row. Currently `price_listing` always inserts. Plan §4.2 doesn't require a dry-run mode, so this isn't a violation, but it's the kind of capability the Beat 1 demo will want and it's cheaper to add now than later.

**Remediation:** add `dry_run: bool = False` to `tools.price_listing` and the underlying adapter. When true, run the formula but skip the INSERT. Keep the row insertion as the default so Listing Intake's lateral path is unchanged.

### M4. Eval runner's "remote" mode pricing path is not tested at all [MED]

`evals/runner.py::_run_remote` is exercised only by the structural local-mode tests; the remote pathway never runs in CI (correct — no GCP creds in CI) and is not exercised by an integration test either. Once the agent is deployed, run `evals.runner --mode remote --threshold 0.85` against the live endpoint at least once to confirm the wire shape (`final_event["recommendation"]["applied_pressures"]`) matches what `_extract_payload` walks for. Today the integration test seeds the right shape, but the model's actual stream output may vary.

### M5. Cloud Build path filter belongs in Terraform but isn't sketched [MED]

The cloudbuild.yaml header says path filter lives in Terraform. `infra/terraform/` doesn't have the trigger config (only IAM + Secret Manager + SQL user per `f3b4ad0`). Phase 2 specifies the filter:

> Path filter: `agents/pricing/**`, `shared/**`, `evals/golden/pricing.jsonl`.

**Remediation:** add `infra/terraform/cloudbuild_pricing.tf` defining a `google_cloudbuild_trigger` with the include filter, source repo, and substitutions. Otherwise the trigger never fires.

### M6. `_handle_cache` cleanup is brittle in the integration test [MED]

`tests/integration/test_a2a_concierge_pricing.py:48` does `a2a._handle_cache.clear()` on yield. `tests/unit/test_a2a.py:42` does the same. If two test files run in the same pytest session and one forgets to clear, the cached handle from the first leaks. Today both clear, but a future test author might miss it.

**Remediation:** either move the autouse cache-clearing fixture into `tests/conftest.py` (fires for every async test that touches `shared.a2a`), or expose `a2a.clear_handle_cache()` as an explicit helper.

### M7. CI does not run the Phase 2 evals [MED]

`.github/workflows/ci.yml:42-43` runs `pytest tests/unit -v`. Cloud Build runs evals (per `pricing.cloudbuild.yaml`), but Cloud Build only fires on the trigger after merge. So the GitHub PR check does not enforce that evals stay above 0.85. Phase 7's pre-submission gate ("All five agents' golden evals ≥ 0.85") relies on these passing, but day-to-day we'd only know about a regression at Cloud Build time.

**Remediation:** add an `evals` step to the GitHub workflow (after submodule fetch is unblocked):

```yaml
- name: Pricing evals (local mode, no Vertex)
  run: uv run python -m evals.runner --agent pricing --threshold 0.85 --mode local
```

---

## LOW

### L1. Manifest is not consulted by any code path yet [LOW]

`agents/pricing/manifest.yaml` exists and `scripts/deploy_agent.py:35` reads `displayName` + `serviceAccount` from it, but it has no schema validation. Forgetting `serviceAccount` raises a vague `KeyError`. Wrap the dict access in a tiny dataclass + validator.

### L2. `evals/runner.py::_run_concierge_local` is a no-op stub [LOW]

It always passes if `expected_specialist` is one of the four valid peers. That's defensible (route eval needs Gemini), but the name `--mode=local` for Concierge is misleading. Rename to `--mode=structural` for non-pricing agents, or add an explicit log line saying "concierge local-mode is structural-only".

### L3. `engine_adapter.replay_recommendation` SELECT could be a row-locking concern under concurrent dispute load [LOW]

Pure read; not a real issue at contest scale (single-merchant demo). Worth a comment in code that this assumes serial dispute submission per `recommendation_id`.

### L4. The `route_correctness` metric uses `case.get("expected_specialist") or ""` [LOW]

If `expected_specialist` is the empty string in a case file, the metric silently treats it as "no expectation." Either default to `None` and short-circuit, or assert non-empty in `_load_golden`.

### L5. `tests/conftest.py::anyio_backend` is unused [LOW]

`anyio` isn't a dependency; `pytest-asyncio` (via `asyncio_mode = "auto"` in `pyproject.toml`) is what runs async tests. The fixture is harmless but cosmetic dead code. Delete.

---

## Phase 2 acceptance — what's signed off

These exit checks from the plan are demonstrably green:

- ✅ `tests/unit/test_pricing_engine_adapter.py --cov-fail-under=100` passes.
- ✅ `evals.runner --agent pricing --threshold 0.85 --mode local` passes (52/52, pass_rate 1.000).
- ✅ Pricing agent module loads and exports an `Agent(...)` instance with three tools wired.
- ✅ Adapter is the only writer to `agents.recommendation_log`; replay always inserts a NEW row with `replay_of` set; original row never UPDATEd (verified by code review + tests).
- ✅ Cloud Build YAML drafted (modulo B3).

These exit checks are **not** demonstrated:

- ❌ `vertexai.agent_engines.get(<pricing-resource>).async_stream_query(...)` returns a non-empty `applied_pressures` dict (H4).
- ❌ `SELECT count(*) FROM agents.recommendation_log` increments by 1 after a smoke call (depends on H4 + H5 grants).
- ❌ Cloud Build pipeline goes green end-to-end (B3).
- ❌ CI is green (B2).

---

## Recommended sequencing

1. Resolve **B1** (commit everything) — 30 minutes. Do this first; everything else is recoverable, an uncommitted laptop is not.
2. Resolve **H1** (`applied_pressures` schema fix) — 20 minutes. Surfaces a tiny but important guardrail-#2 hole.
3. Resolve **B2 / H5** (provision SUBMODULE_TOKEN + flip CI submodule flag) — depends on the human gate. Until done, B2 keeps the GitHub workflow red and B3 keeps Cloud Build red.
4. Resolve **H2 / H3** (deploy_agent requirements + extra_packages) — 1–2 hours, then a real Agent Engine deploy + the H4 smoke call.
5. Resolve **H6** (decide on demo-HTML provenance) — before Phase 5; not blocking Phase 3.
6. Resolve **B4** (A2A protocol mandate) — biggest unknown; investigate ADK 2.0 `to_a2a()` and the contest FAQ in parallel with Phase 3 so it's locked by Phase 5.
7. MED items can be folded into Phase 3 or addressed during the Phase 7 hardening pass.

The Pricing agent code itself does not need rework — only the surrounding plumbing (commits, CI, deploy, contest-mandate) needs to land before Phase 3 starts.
