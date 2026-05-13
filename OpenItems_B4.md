# B4: A2A Protocol Mandate — Findings

> **Methodology note.** Verified via web research on 2026-05-07 (current date); the previous
> version of this doc was written without web access and over-assumed the gap. Replaced with
> verified facts cited inline. The analysis of the rules text and the local code is
> first-hand; SDK / Vertex behavior claims are anchored to URLs in the Sources section.

## The contest's exact wording

From `Google for Startups AI Agents Challenge Rules FINAL.pdf`, page 4, Track 3 architectural
mandates (verbatim):

> **A2A Interoperability:** Your agent's communication layer must utilize the Agent-to-Agent
> (A2A) protocol, ensuring it can be seamlessly discovered by and coordinate with other
> enterprise agents.

Two more contextual lines from the same PDF that constrain interpretation:

> Track 3: Refactor for Google Cloud Marketplace & Gemini Enterprise … This is your
> opportunity to transform an MVP into a scalable, monetizable asset prepped for listing on
> the Google Cloud Marketplace and within Gemini Enterprise. *(p. 4)*

> Cloud-Native Runtime: You must migrate the agent's runtime natively to Google Cloud
> infrastructure (e.g., deployed via Agent Engine, Google Kubernetes Engine (GKE), or
> Cloud Run). *(p. 4)*

The mandate uses the proper-noun capitalisation "Agent-to-Agent (A2A) protocol" and binds
it to the goal of being **discoverable by and able to coordinate with other enterprise
agents** — i.e. interop across vendors / frameworks, not just inside one's own deployment.

## What "A2A protocol" actually means today

A2A is the open agent-interop protocol Google announced at Cloud Next '25 (April 2025) and
subsequently donated to the Linux Foundation (June 2025). It is now developed in the open at
the Linux Foundation under the A2A Project. Verified facts:

- **Spec home:** `https://a2a-protocol.org/latest/` (canonical site).
- **Status:** **A2A v1.0 is GA**, governed by the Linux Foundation.
- **Python SDK:** `a2a-sdk >= 0.3.4` on PyPI.
- **Transport.** A2A is HTTP-based and uses JSON-RPC 2.0 as the wire format (the spec also
  defines optional gRPC and HTTP+JSON variants). It is *not* a Google-internal SDK.
- **Discovery.** Each compliant agent publishes an **Agent Card**. The community
  well-known URL is `/.well-known/agent-card.json`; Vertex's A2A surface uses its own path
  (`{a2a_url}/v1/card` — see below). The card declares the agent's name, skills,
  input/output modalities, auth schemes, and the JSON-RPC endpoint URL. This is the
  "seamlessly discovered by … other enterprise agents" hook the rules call out.
- **Core RPCs.** `message/send`, `message/stream` (SSE), `tasks/get`, `tasks/cancel`, plus
  push-notification subscriptions. Tasks are first-class, long-running, and have a state
  machine (`submitted → working → input-required → completed/canceled/failed`).

The Linux-Foundation governance and the Agent Card discovery story are what make A2A
"interop" — any A2A-speaking client can call any A2A-speaking server regardless of
framework (ADK, LangGraph, CrewAI, semantic-kernel, etc.).

## A2A on Vertex Agent Engine (the key correction)

**A2A is natively integrated into Vertex AI Agent Engine** as of the launch announcement
on **2025-09-10** (Google Developers community post — see Sources). The integration ships as
a deployment wrapper exposed by the Vertex Python SDK:

```python
from vertexai.preview.reasoning_engines import A2aAgent
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

card = create_agent_card(...)        # name, skills, auth, etc.
a2a_agent = A2aAgent(
    agent_card=card,
    agent_executor_builder=BuilderClass,
)
```

When deployed via `agent_engines.create(a2a_agent, requirements=[...])`, the resulting
Agent Engine resource exposes a real A2A surface:

- **Agent Card endpoint:** `{a2a_url}/v1/card` — Vertex's flavor of the well-known card
  URL. Not `/.well-known/agent-card.json`; consumers must read the card from the Vertex
  path. The card itself is standard A2A content.
- **JSON-RPC handlers** wired to SDK methods on the deployed engine handle:
  `on_message_send`, `on_get_task`, `handle_authenticated_agent_card`. These implement the
  A2A core RPCs so any A2A-speaking client (not just the Vertex SDK) can call the agent.
- **Required runtime deps in `agent_engines.create(..., requirements=[...])`:**
  `google-cloud-aiplatform[agent_engines,adk]>=1.112.0`, `a2a-sdk >= 0.3.4`, plus the
  agent's own deps.

This invalidates the previous report's central conclusion — that Agent Engine had no
native A2A and we'd need a Cloud Run hybrid via ADK's `to_a2a()` helper. That helper still
exists (it's the path for non-managed deploys, e.g. self-hosting an ADK agent on Cloud
Run / GKE), but **on Agent Engine the wrapper is `A2aAgent` and we don't need it.**

## Where we are vs. where the mandate points

**What we have.** `shared/a2a.py:88` calls
`vertexai.agent_engines.get(resource).async_stream_query(message=…, user_id=…)`. That is the
Agent Engine SDK's native streaming RPC against the proprietary `reasoningEngines` REST
surface — audience-scoped, ID-token-gated, *not* JSON-RPC, no Agent Card endpoint. Each of
our agents is currently wrapped with `AdkApp` at deploy time (the default), so none of them
expose A2A today.

**The gap.** A reasonable judge will read the rule as "at least one externally visible
inter-agent edge speaks the open A2A protocol such that a third-party agent could wire into
the system by reading an Agent Card." `async_stream_query` against an `AdkApp`-wrapped
engine plainly does not satisfy that — no card, no JSON-RPC. Strict reading assumed.

## Recommendation

**Stay on Vertex Agent Engine. Swap each agent's deployment wrapper from `AdkApp` to
`A2aAgent`.** Concretely:

1. Add `a2a-sdk >= 0.3.4` to `pyproject.toml` and to the `requirements=[...]` list passed to
   `agent_engines.create()` (alongside the bumped
   `google-cloud-aiplatform[agent_engines,adk]>=1.112.0`).
2. In each `agents/<name>/agent.py`, build an `AgentCard` via `create_agent_card(...)` and
   wrap the agent with `A2aAgent(agent_card=card, agent_executor_builder=...)` instead of
   `AdkApp`. Redeploy via the existing `scripts/deploy_agent.py` flow.
3. Update `shared/a2a.py` to talk JSON-RPC (`message/send`, `tasks/get`) against the
   deployed engine's A2A endpoint, discovering it by GET on `{a2a_url}/v1/card`. Use
   `a2a-sdk`'s `A2AClient` / `ClientFactory`. Keep ID-token caching and trace propagation
   exactly as today; only the wire format changes.
4. Cover **all four** internal edges (Concierge↔Pricing/Onboarding/Listing-Intake/Dispute,
   plus the two lateral edges) with the new transport — there's no longer a reason to keep
   one edge on the SDK fast-path now that A2A is the managed-runtime native channel.
5. Demo evidence: `curl {pricing_a2a_url}/v1/card` in the demo video; show JSON-RPC
   `message/send` payloads in Cloud Trace.

**Phase-5 lift estimate:** a few hours per agent — no second runtime, no second container,
no Cloud Run mixing. New deps, ~10-line wrapper change in each `agent.py`, ~50 LoC client
swap in `shared/a2a.py`, and a redeploy. Down from the previous estimate of ~2 eng-days for
the abandoned Cloud Run hybrid.

Why not keep `AdkApp` + `async_stream_query`: fails the proper-noun "A2A protocol" reading
and the "discovered by other enterprise agents" clause. A2A is one of four named
architectural mandates under the 30%-weighted Technical Implementation rubric — not worth
the risk when the fix is a wrapper swap.

## Open questions for the human

1. **Migration sequencing across the five agents.** All five wrappers need to flip from
   `AdkApp` to `A2aAgent`. Do we cut over per-agent (Concierge last, since it's the
   external entrypoint and any client-side regressions show up there), or in one bulk
   redeploy? Recommend per-agent, Concierge last, with a feature flag in `shared/a2a.py`
   selecting transport per target agent.
2. **Card path: `/v1/card` vs `/.well-known/agent-card.json`.** Vertex serves the card at
   `{a2a_url}/v1/card`. Strict A2A clients may probe the well-known path first. If a judge's
   tooling does that, do we need a redirect / proxy in front? Worth a 30-min spike with a
   stock `a2a-sdk` client pointed at a deployed Vertex `A2aAgent` to confirm discovery
   works end-to-end.
3. *(resolved)* **Cloud Run vs. Agent Engine for the A2A-wrapped service.** **Resolved in
   favor of Agent Engine.** Native `A2aAgent` wrapper means we keep one runtime; Cloud Run
   hybrid is no longer needed.

---

## Sources

- `https://a2a-protocol.org/latest/` — canonical A2A spec site (v1.0 GA).
- `https://discuss.google.dev/t/launched-the-a2a-protocol-is-now-natively-integrated-on-vertex-ai-agent-engine/264045`
  — launch announcement, 2025-09-10.
- `https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/runtime/create-an-a2a-agent`
  — `A2aAgent` wrapper, `create_agent_card`, `agent_engines.create()` requirements list.
- `https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/use-an-a2a-agent`
  — deployed-engine handle methods (`on_message_send`, `on_get_task`,
  `handle_authenticated_agent_card`) and the `{a2a_url}/v1/card` endpoint.
