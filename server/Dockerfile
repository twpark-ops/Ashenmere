FROM python:3.13-slim AS base

RUN groupadd -r agentburg && useradd -r -g agentburg agentburg

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace config and dependency files
COPY pyproject.toml ./
COPY shared/pyproject.toml shared/
COPY server/pyproject.toml server/

# Copy source code
COPY shared/ shared/
COPY server/ server/

# Install dependencies
RUN uv sync --frozen --no-dev --package agentburg-server 2>/dev/null || \
    uv pip install --system -e ./shared -e ./server

USER agentburg

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "agentburg_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
