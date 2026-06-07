# SurplusAS — Demo Video Script & Storyboard

**Google for Startups AI Agents Challenge — Track 3 (Refactor for Marketplace + Gemini Enterprise)**
Target length: **≤ 120 seconds (hard cap).** Planned runtime: **118s.**

---

## Logline

> SurplusAS is a hub-and-spoke multi-agent service on Vertex AI Agent Engine that gives SMB grocers and restaurants **deterministic, audit-grade dynamic pricing** for surplus inventory — a Concierge agent routes merchant turns to four ADK/Gemini specialists over A2A, and every price is reproducible, logged append-only, and disputable.

---

## Rubric coverage map (so nothing is dropped)

| Rubric (weight) | Where it lands in the cut |
|---|---|
| Business Case (30%) | Beat 0 (problem + who the customer is) |
| Technical Implementation (30%) | Beat 1 (A2A routing + deterministic engine), Beat 3 (open A2A Agent Card) |
| Innovation (20%) | Beat 1 (audit trail) + Beat 2 (replay / `replay_of` / pressure diff + webhook) |
| Demo & Presentation (20%) | Beat 4 (architecture diagram + ADK/Gemini/Agent Engine/Cloud Run callout) |

---

## Shot table

| Window | On-screen action | Voiceover (exact) | Rubric target |
|---|---|---|---|
| **0:00–0:14** | Title card "SurplusAS" → quick montage: a bakery at closing time, trays of unsold croissants, a half-empty grocery cooler. Lower-third: *"~$1T of food wasted yearly."* | "Every night, grocers and restaurants throw out good food — because pricing surplus by hand is slow, inconsistent, and impossible to defend. SurplusAS fixes that for small merchants with a team of AI agents." | Business Case |
| **0:14–0:24** | Cut to architecture mermaid (docs/architecture.md topology). Highlight Concierge node, then the four specialists lighting up. | "It's hub-and-spoke on Vertex AI Agent Engine. One customer-facing Concierge, built on Gemini 2.5 Pro, routes each merchant turn to four specialists over the A2A protocol." | Demo & Presentation / Technical |
| **0:24–0:30** | Switch to the phone-frame merchant demo UI (already reset, empty state). Cursor pastes a merchant note into "Merchant note". | "Here's the merchant app. A baker pastes a plain-language note about what's left." | Business Case / Technical |
| **0:30–0:46** | Click **Generate listing**. Spinner → result card slides up: title, description, **green $ surplus price**, discount %, "Pricing logic" box, confidence bar. Briefly highlight the price + pricing-logic box. | "The Concierge hands off to Listing Intake, which makes a lateral A2A call straight to the Pricing agent for a live anchor price. Notice the agent didn't invent this number." | Technical / Innovation |
| **0:46–0:58** | Zoom the "Pricing logic" / pressures area. Overlay a small code chip: `engine_adapter.recommend → INSERT recommendation_log` and `applied_pressures · formula_version`. | "Every price comes from one deterministic engine call — never the language model. The applied pressures and formula version are written to an append-only audit log. Same inputs, same price, every time." | Innovation / Technical |
| **0:58–1:04** | Click **Publish Listing** → confirm → status flips to "Published! ID: …". The "Later that day — dispute the price" panel appears. | "Publish, and a signed listing-created webhook fires to the merchant's systems." | Technical |
| **1:04–1:20** | Type a dispute reason ("Customer says it's too high for this neighborhood"), click **Open dispute**. Status: "Replaying recommendation with current anchors…" → amber result card with narration, `tool path: … → replay_recommendation → emit_price_update_webhook`, and "🔔 price.updated webhook fired". | "Open a dispute and Dispute Triage replays the original recommendation against today's anchors — a brand-new audit row, linked by replay-of. It narrates exactly which pressures moved, and if the price shifts more than twenty-five cents, a price-updated webhook ships." | Innovation |
| **1:20–1:38** | Cut to a terminal. Run the live curl: `curl -s https://surplusas-a2a-pricing-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json`. JSON Agent Card scrolls — highlight `name`, `url`, `skills`. | "This is Track-3's interoperability mandate. Every one of our five agents is published over the open A2A protocol — a discoverable Agent Card and a JSON-RPC endpoint. The Pricing agent is live on Cloud Run right now; any external enterprise agent, on any framework, can discover and call it." | Technical |
| **1:38–1:50** | Quick cut: `uv run python -m scripts.verify_a2a pricing` printing "Discovered Agent Card via standard a2a-sdk A2ACardResolver … A2A verification: PASS". Optional: flash a Cloud Trace screenshot of an inter-agent span. | "We prove it with the stock a2a-sdk client — discovery and JSON-RPC conformance, no Google credentials needed. And every A2A hop is traced end-to-end in Cloud Trace." | Technical / Demo & Presentation |
| **1:50–1:58** | Back to architecture diagram, full topology with the two lateral A2A edges glowing. End card: "SurplusAS · ADK + Gemini 2.5 · Vertex AI Agent Engine · Cloud Run". | "Five ADK agents, Gemini 2.5, on Vertex Agent Engine and Cloud Run — deterministic, auditable pricing that small merchants can actually trust. That's SurplusAS." | Demo & Presentation |

> **Pacing note:** voiceover is written at ~2.6 words/sec. If any window runs long when recording, trim Beat 0:00–0:14 first (it has the most flex), then the Beat 1:38–1:50 verify clip.

---

## Recording setup checklist (do this BEFORE hitting record)

Run from the repo root: `C:\Users\Mitch\surplusAS-agents-2.0`

1. **Fresh ADC** (so the gateway can mint A2A ID tokens and reach Cloud SQL):
   ```powershell
   gcloud auth application-default login
   ```
   (If A2A token minting fails from a plain user, use the impersonation flag noted in `shared/a2a.py` / CLAUDE.md "Operational gotchas".)
2. **DB creds for the schema-owner role** (used by the seed/reset script):
   ```powershell
   $env:PG_USER='surplusas_app'; $env:PG_PASSWORD='<db-app-password from Secret Manager>'
   ```
3. **Start the gateway locally** on port 8080 (serves the static UI + the `/demo/v1/*` shims the page calls):
   ```powershell
   uv run python -m service.main
   ```
4. **Reset demo state + open the UI** — the real shoot driver. This runs `scripts/seed_demo_merchant.py` (wipes `agents.*` tables, confirms the `sk_demo_surplus_2026` / `demo_001` partner key) then opens `surplusas-merchant-demo.html`:
   ```powershell
   .\scripts\shoot_demo.ps1
   # (bash equivalent: ./scripts/shoot_demo.sh)
   ```
   **Re-run this between every take** for a clean slate.
5. **Stage the merchant note** you'll paste (have it on the clipboard). Example:
   > "Closing in 30 mins — 18 butter croissants and 6 baguettes left from this morning's bake. Downtown location."
   Use **manual paste into the "Merchant note" box → Generate listing.** Do **NOT** use the "✨ Try It" sample button (the bundled sample manifest path 404s per the submission-readiness audit; the manual paste path is the working demo flow).
6. **Stage the dispute reason** (clipboard, ready for Beat 2):
   > "Customer complained the price is too high for this neighborhood."
7. **Pre-warm the A2A terminal** in a second window so the live curl is instant on camera:
   ```powershell
   curl.exe -s https://surplusas-a2a-pricing-dcsgbetuga-uc.a.run.app/.well-known/agent-card.json | python -m json.tool
   ```
   And pre-run the local verifier once so the PASS output is fresh:
   ```powershell
   uv run python -m scripts.verify_a2a pricing
   ```
8. **Render the architecture diagram** ahead of time (the two mermaid blocks in `docs/architecture.md`): topology + Beat 1/Beat 2 sequence. Export to PNG/SVG so it's crisp on screen (e.g. paste into mermaid.live or the VSCode Mermaid preview and screenshot).
9. **(Optional) Cloud Trace span screenshot** — open Cloud Trace, find a recent inter-agent trace (Concierge → Listing Intake → Pricing) and screenshot the waterfall for the Beat 1:38–1:50 flash.

---

## B-roll / screenshots to capture

- [ ] **Stock food-waste B-roll** for Beat 0 (closing-time bakery, unsold trays, grocery cooler). Lower-third stat overlay.
- [ ] **Architecture topology** — mermaid block in `docs/architecture.md` (the hub-and-spoke flowchart with the two dotted lateral A2A edges). PNG/SVG.
- [ ] **Beat 1 result card** — clean screen recording of paste → Generate → result card with green surplus price, discount tag, "Pricing logic" box, confidence bar, listing-ID chip.
- [ ] **Audit-trail overlay chip** — small graphic citing `agents/pricing/engine_adapter.py` (`recommend → INSERT recommendation_log`, `applied_pressures`, `formula_version`, append-only).
- [ ] **Publish flow** — "Published! ID: …" success state + the "Later that day — dispute" panel revealing.
- [ ] **Beat 2 dispute card** — amber result card: narration, `tool path: … → replay_recommendation → emit_price_update_webhook`, "🔔 price.updated webhook fired (delta > $0.25)".
- [ ] **Live Agent Card** — terminal recording of `curl …/.well-known/agent-card.json` against the live Cloud Run Pricing endpoint, JSON scrolling, `name` / `url` / `skills` highlighted.
- [ ] **A2A verifier PASS** — terminal recording of `uv run python -m scripts.verify_a2a pricing` ending in "A2A verification: PASS".
- [ ] **(Optional) Cloud Trace** — inter-agent span waterfall screenshot.
- [ ] **End card** — "SurplusAS · ADK + Gemini 2.5 · Vertex AI Agent Engine · Cloud Run".

---

## Notes on accuracy (kept honest to the repo)

- The demo UI calls **same-origin `/demo/v1/*` shims** (`service/routes_demo.py`), which force `partner_id=sk_demo_surplus_2026` so the static page needs no API key. The authenticated public surface is `/v1/concierge`, `/v1/listings/{id}/dispute` (`service/routes_rest.py`, `service/routes_disputes.py`).
- **Beat 1** = `/demo/v1/agent` (Concierge → Listing Intake → lateral A2A → Pricing) then `/demo/v1/listings/publish`. **Beat 2** = `/demo/v1/listings/{id}/dispute` (Concierge → Dispute Triage → lateral A2A replay → Pricing).
- The `price.updated` webhook only fires when `|new − old| > $0.25` (`agents/dispute_triage/tools.py::_PRICE_UPDATE_THRESHOLD`). If the take's replay lands under threshold the card shows "Replay produced no material price change — no webhook"; re-run `shoot_demo.ps1` and retry until you get a take that crosses the threshold for the on-camera webhook beat.
- The **open A2A surface** (`service/a2a_app.py`, ADK `to_a2a()`) is distinct from the **internal mesh transport** (`shared/a2a.py`, Vertex `async_stream_query`). The video showcases the open surface for the Track-3 mandate.
