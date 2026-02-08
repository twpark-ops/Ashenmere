FROM python:3.12-slim AS base

RUN groupadd -r agent && useradd -r -g agent agent

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml ./
COPY shared/pyproject.toml shared/
COPY client/pyproject.toml client/

COPY shared/ shared/
COPY client/ client/

RUN uv sync --frozen --no-dev --package agentburg-client 2>/dev/null || \
    uv pip install --system -e ./shared -e ./client

# Agent config is mounted at runtime
VOLUME ["/app/agent_config"]

USER agent

CMD ["python", "-m", "agentburg_client"]
