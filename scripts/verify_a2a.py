"""Verify the open A2A surface end-to-end with a standard a2a-sdk client.

Proves Track-3 mandate #4 (A2A Interoperability) concretely: it starts an
agent's `to_a2a()` server on a real localhost socket, then uses the stock
`a2a.client.A2ACardResolver` — the same client any third-party enterprise
agent would use — to DISCOVER the Agent Card over HTTP, and prints what a peer
sees (name, JSON-RPC URL, transport, skills). It also confirms the JSON-RPC
endpoint answers with a spec-compliant envelope.

This needs NO Google Cloud credentials: card discovery + JSON-RPC routing are
served locally by the ADK `to_a2a()` Starlette app. (A full `message/send`
that runs the agent would additionally need Vertex access for the Gemini call;
discovery + protocol conformance is what the mandate turns on.)

    uv run python -m scripts.verify_a2a            # pricing (default)
    uv run python -m scripts.verify_a2a concierge  # any agent in the mesh
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import httpx
import uvicorn

from service.a2a_app import VALID_AGENTS, build_a2a_app

HOST = "127.0.0.1"
PORT = 8765
WELL_KNOWN = "/.well-known/agent-card.json"


async def _probe(base_url: str) -> int:
    from a2a.client import A2ACardResolver

    async with httpx.AsyncClient(timeout=20.0) as hc:
        # 1) Discovery via the stock a2a-sdk resolver (framework-agnostic client).
        resolver = A2ACardResolver(httpx_client=hc, base_url=base_url)
        card = await resolver.get_agent_card()
        print("Discovered Agent Card via standard a2a-sdk A2ACardResolver:")
        print(f"  name                : {card.name}")
        print(f"  url (JSON-RPC)      : {card.url}")
        print(f"  preferredTransport  : {getattr(card, 'preferred_transport', None)}")
        print(f"  protocolVersion     : {getattr(card, 'protocol_version', None)}")
        print(f"  skills              : {[s.name for s in card.skills]}")

        # 2) Raw well-known fetch (what a curl / browser would see).
        r = await hc.get(base_url + WELL_KNOWN)
        print(f"  GET {WELL_KNOWN} -> HTTP {r.status_code}")

        # 3) JSON-RPC conformance: an unknown method must yield -32601.
        rpc = await hc.post(
            base_url + "/",
            json={"jsonrpc": "2.0", "id": "1", "method": "nonexistent/method", "params": {}},
        )
        body = rpc.json()
        code = body.get("error", {}).get("code")
        print(f"  JSON-RPC POST / unknown-method -> error code {code} "
              f"({'OK: spec-compliant' if code == -32601 else 'UNEXPECTED'})")

        ok = (
            r.status_code == 200
            and card.url.endswith(f"{HOST}:{PORT}")
            and bool(card.skills)
            and code == -32601
        )
        return 0 if ok else 1


def main() -> int:
    agent = sys.argv[1] if len(sys.argv) > 1 else "pricing"
    if agent not in VALID_AGENTS:
        print(f"unknown agent {agent!r}; choose one of {VALID_AGENTS}", file=sys.stderr)
        return 2

    app = build_a2a_app(agent, host=HOST, port=PORT, protocol="http")
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the socket to come up (lifespan builds the card on startup).
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    else:
        print("server failed to start", file=sys.stderr)
        return 1

    print(f"A2A server for agent={agent!r} live at http://{HOST}:{PORT}\n")
    try:
        rc = asyncio.run(_probe(f"http://{HOST}:{PORT}"))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    print("\nA2A verification:", "PASS" if rc == 0 else "FAIL")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
