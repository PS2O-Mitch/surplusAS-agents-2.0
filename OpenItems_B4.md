# B4: A2A Protocol Mandate — Findings

> **Methodology note.** Web search and fetch were denied in this research session, so the
> citations below are URLs the human must independently verify before final submission.
> The analysis of the rules text and the local code is first-hand. Claims about the A2A spec,
> ADK 2.0 `to_a2a`, and Agent Engine's native surface reflect the agent's training-data
> knowledge (cutoff Jan 2026) and are flagged where verification is critical.

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
the Linux Foundation under the A2A Project. Key facts (verify):

- **Spec home:** `https://a2a-protocol.org/` (canonical site) and the GitHub org
  `https://github.com/a2aproject/A2A` (spec) plus `https://github.com/a2aproject/a2a-python`
  (Python SDK).
- **Latest spec line as of the knowledge cutoff:** v0.3.x (the spec hit v0.2 in mid-2025 and
  saw further iterative releases through early 2026). The exact "current" version on the day
  the contest closes (June 5 2026) needs a live check.
- **Transport.** A2A is HTTP-based and uses JSON-RPC 2.0 as the wire format (spec also defines
  optional gRPC and HTTP+JSON variants in v0.3+). It is *not* a Google-internal SDK.
- **Discovery.** Each compliant agent publishes an **Agent Card** at the well-known URL
  `/.well-known/agent-card.json` (older drafts: `/.well-known/agent.json`). The card
  declares the agent's name, skills, input/output modalities, auth schemes, and the JSON-RPC
  endpoint URL. This is the "seamlessly discovered by … other enterprise agents" hook the
  rules call out.
- **Core RPCs.** `message/send`, `message/stream` (SSE), `tasks/get`, `tasks/cancel`, plus
  push-notification subscriptions. Tasks are first-class, long-running, and have a state
  machine (`submitted → working → input-required → completed/canceled/failed`).

The Linux-Foundation governance and the Agent Card discovery story are what make A2A
"interop" — any A2A-speaking client can call any A2A-speaking server regardless of
framework (ADK, LangGraph, CrewAI, semantic-kernel, etc.).

## ADK 2.0 `to_a2a()`: what it does, what it doesn't

`google-adk==2.0.0b1` (currently pinned in `uv.lock:457`) ships a helper, typically imported
as `from google.adk.a2a.utils.agent_to_a2a import to_a2a` (path may vary by build).
`to_a2a(root_agent, port=…)` wraps an ADK `Agent` (or `LlmAgent`) and returns a Starlette
ASGI app that exposes:

1. The Agent Card at `/.well-known/agent-card.json`, auto-generated from the agent's
   `name`, `description`, `tools`, etc.
2. The JSON-RPC endpoint that implements `message/send`, `message/stream`, and the task
   lifecycle methods, translating each call into ADK's internal `Runner.run_async` execution.

What it produces is therefore an **A2A-compliant HTTP server**, runnable with `uvicorn` —
not a managed Vertex resource. It does **not** by itself deploy anywhere; you have to host
the resulting ASGI app (e.g. on Cloud Run, GKE, or a container on Agent Engine's "custom
container" path). The companion `RemoteA2aAgent` lets one ADK agent call another A2A
endpoint as if it were a sub-agent, using the Agent Card for discovery.

(URL to verify: `https://google.github.io/adk-docs/a2a/` and the `to_a2a` symbol in
`https://github.com/google/adk-python`.)

## Where we are vs. where the mandate likely points

**What we have.** `shared/a2a.py` (`shared/a2a.py:88`) calls
`vertexai.agent_engines.get(resource).async_stream_query(message=…, user_id=…)`. That is the
**Agent Engine SDK's native streaming RPC**. Under the hood it talks to the Vertex AI
`reasoningEngines` REST surface — a Google-proprietary, audience-scoped, ID-token-gated
endpoint that is *not* JSON-RPC, does *not* publish an Agent Card, and is not addressable by
a non-Google A2A client. There is no `/.well-known/agent-card.json`, no
`message/send` / `tasks/get` endpoints, no agent-card-driven discovery.

**Honest reading of the mandate.** The rule says the *communication layer* must "utilize"
A2A and that the agent must be "seamlessly discovered by and coordinate with other
enterprise agents". A reasonable judge will interpret that as: at least one externally
visible inter-agent edge speaks the open A2A protocol such that a third-party agent could
wire into the system by reading an Agent Card. `agent_engines.async_stream_query` plainly
does not satisfy that strict reading — it's Google-private SDK glue, not the open spec.

It is *plausible* a lenient judge could read "A2A protocol" generically as "agent-to-agent
communication" (lowercase a2a) and accept Agent Engine's native channel, especially given
the rules also explicitly endorse Agent Engine as a deployment target. But the proper-noun
capitalisation, the "discovered by other enterprise agents" clause, and the Marketplace /
Gemini-Enterprise framing of Track 3 all point hard at the formal spec. **Assume the strict
reading.**

## Recommendation

**Adopt option (B): wrap each ADK agent with `to_a2a()` and stand up at least one
A2A-compliant inter-agent edge over HTTP, while keeping `async_stream_query` as the default
fast-path inside the cluster.** Specifically:

1. Add `a2a-sdk` (the official `a2aproject/a2a-python` package) to `pyproject.toml`.
2. In each `agents/<name>/agent.py`, also export `a2a_app = to_a2a(agent, ...)`. Build a
   second container per agent that runs `uvicorn agents.<name>.agent:a2a_app`. Deploy these
   to **Cloud Run** behind IAM auth (the rules permit Cloud Run as the runtime, p. 4).
3. Convert exactly **one** lateral edge — Listing Intake → Pricing — to the A2A path:
   - Pricing's Cloud Run service publishes its Agent Card at
     `/.well-known/agent-card.json`.
   - `shared/a2a.py` grows a second client (via `a2a-sdk`'s `A2AClient` /
     `ClientFactory`) that resolves the Pricing agent by Agent Card URL and calls
     `message/send`. Concierge → Pricing and the other lateral (Dispute → Pricing) can also
     migrate, but one demonstrable edge is the contractual minimum.
4. Document this in the demo video and architecture diagram: show the curl of the Agent
   Card, show the JSON-RPC `message/send` payload in Cloud Trace.

Why not (A) keep as-is: too risky given the proper-noun mandate and the
"discoverable by other enterprise agents" clause. The contest is judged on Technical
Implementation 30%, and A2A is one of four named architectural mandates — failing it is
plausibly fatal.

Why not (C) full migration of `shared/a2a.py` to A2A over HTTP: the project's existing
"Locked decisions" in `familiarize-yourself-with-track3-md-steady-pine.md:18-20` already
chose the SDK path; ripping it out adds substantial deployment, auth, and trace-propagation
work for marginal judging benefit beyond what (B) gives. Hybrid keeps the tested fast path
in place and adds the protocol-compliance surface needed to satisfy the rules.

**Phase-5 lift estimate for option (B):** ~2 engineering days. New deps (`a2a-sdk`),
two-line additions to each `agent.py`, one new Cloud Run service per agent (or one combined
service with mounted sub-apps), Agent Card YAML for each, IAM allow-list, and a 50-LoC
A2A-client branch in `shared/a2a.py` keyed on a `A2A_TRANSPORT={sdk|a2a}` env flag for the
single Intake→Pricing edge.

## Open questions for the human

1. **Live verification of A2A spec version on June 5 2026.** The rules don't pin a version;
   the strictest plausible reading is "the most recent published spec at submission time."
   Verify `https://a2a-protocol.org/latest/` and the `a2aproject/A2A` repo.
2. **Does the Devpost FAQ for the contest add interpretation?** Worth checking
   `https://devpost.team/google-cloud-for-startups/hackathons/3197` discussion threads —
   prior contests have published clarifications that override strict readings.
3. **Does Vertex Agent Engine expose an A2A endpoint natively in 2026?** As of the cutoff
   the answer was *no* — `reasoningEngines` is its own REST surface — but Google has been
   shipping incrementally. If Agent Engine added a per-resource `/agent-card.json` shim
   between Jan 2026 and the submission date, option (A) becomes defensible. Verify
   `https://cloud.google.com/vertex-ai/docs/agent-engine/` release notes.
4. **Will `to_a2a()` survive ADK 2.0's beta → GA transition?** Pinned at `2.0.0b1`. Worth
   spending 30 min validating the `to_a2a` import path against the version that will be
   installed at `uv sync` time on submission day.
5. **Cloud Run vs. Agent Engine for the A2A-wrapped service.** Agent Engine's "managed
   container" mode may host an arbitrary ASGI app; if so, we can keep a single runtime
   instead of mixing Cloud Run + Agent Engine. Worth a 1-hour spike before committing the
   Phase-5 plan.

---

## Sources to verify (web access was denied in this session)

- `https://a2a-protocol.org/` — canonical spec site
- `https://github.com/a2aproject/A2A` — spec repo
- `https://github.com/a2aproject/a2a-python` — Python SDK
- `https://google.github.io/adk-docs/a2a/` — ADK A2A integration docs
- `https://github.com/google/adk-python` — ADK source for `to_a2a`
- `https://cloud.google.com/blog/products/ai-machine-learning/build-and-manage-multi-system-agents-with-vertex-ai`
  — Agent Engine + A2A reference architecture (if it exists; the agent-starter-pack repo at
  `https://github.com/GoogleCloudPlatform/agent-starter-pack` historically contained one)
- `https://devpost.team/google-cloud-for-startups/hackathons/3197` — contest discussion threads / clarifications
- `https://cloud.google.com/vertex-ai/docs/agent-engine/` — Agent Engine release notes (check for native A2A surface)
