# SurplusAS gateway: FastAPI REST + webhooks, all 5 ADK agents in-process.
#
# Runs `uvicorn service.app:app` (the gateway). The open A2A surface
# (service/a2a_app.py) shares this image — override the CMD with
# `uvicorn service.a2a_app:app` + $A2A_AGENT to serve one agent's card.
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
    PYTHONPATH=/app

EXPOSE 8080
CMD ["sh", "-c", "uvicorn service.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
