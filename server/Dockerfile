# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy workspace config and dependency files first (layer cache)
COPY pyproject.toml uv.lock ./
COPY shared/pyproject.toml shared/
COPY server/pyproject.toml server/

# Copy source code
COPY shared/ shared/
COPY server/ server/

# Install dependencies (locked = assert lockfile is up to date)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package agentburg-server

# =============================================================================
# Stage 2: Production runtime
# =============================================================================
FROM python:3.13-slim-bookworm AS runtime

# Security: install only required runtime deps, then clean up
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and group
RUN groupadd -r agentburg && useradd -r -g agentburg -d /app -s /sbin/nologin agentburg

WORKDIR /app

# Copy venv and workspace from builder
COPY --from=builder /app /app

# Ensure venv binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/.venv"

# Create writable directories for tmp and cache (read-only root FS compatible)
RUN mkdir -p /tmp/agentburg && chown agentburg:agentburg /tmp/agentburg

# Set ownership
RUN chown -R agentburg:agentburg /app

USER agentburg

EXPOSE 8000

# Health check endpoint
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["sh", "-c", "python -m uvicorn agentburg_server.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
