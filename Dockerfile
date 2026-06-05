# Open A2A surface for SurplusAS agents, served on Cloud Run.
#
# Runs `uvicorn service.a2a_app:app` — ADK's to_a2a() adapter exposing an Agent
# Card (/.well-known/agent-card.json) + JSON-RPC 2.0 for the agent named by
# $A2A_AGENT (default: pricing). See service/a2a_app.py and scripts/verify_a2a.py.
#
# We run from the SOURCE TREE (not an installed wheel) because
# shared/pricing_intel.py puts ./vendor/surplusas-pricing on sys.path relative
# to the shared/ package location, so /app must preserve that layout.
FROM python:3.12-slim

# uv for fast, lockfile-faithful installs.
COPY --from=ghcr.io/astral-sh/uv:0.5.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Install locked dependencies ONLY (not the project itself). Cached unless
#    pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Application source + the vendored pricing engine (a sibling of shared/,
#    required by shared/pricing_intel.py's sys.path insertion).
COPY agents ./agents
COPY shared ./shared
COPY service ./service
COPY vendor ./vendor

# Run inside the synced venv; /app on PYTHONPATH so `import service/shared/agents`
# resolve from the source tree.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH=/app \
    A2A_AGENT=pricing

# Cloud Run injects $PORT and listens on it. The Agent Card advertises the
# externally-reachable URL via A2A_PUBLIC_HOST / A2A_PUBLIC_PROTOCOL (set at deploy).
EXPOSE 8080
CMD ["sh", "-c", "uvicorn service.a2a_app:app --host 0.0.0.0 --port ${PORT:-8080}"]
