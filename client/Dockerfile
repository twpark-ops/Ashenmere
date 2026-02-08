# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.13-slim-bookworm AS builder

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy workspace config and dependency files first (layer cache)
COPY pyproject.toml ./
COPY shared/pyproject.toml shared/
COPY client/pyproject.toml client/

# Copy source code
COPY shared/ shared/
COPY client/ client/

# Install dependencies into system Python
RUN uv sync --frozen --no-dev --package agentburg-client 2>/dev/null || \
    uv pip install --system -e ./shared -e ./client

# =============================================================================
# Stage 2: Production runtime
# =============================================================================
FROM python:3.13-slim-bookworm AS runtime

# Create non-root user and group
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /app /app
COPY --from=builder /usr/local/lib/python3.13 /usr/local/lib/python3.13
COPY --from=builder /usr/local/bin /usr/local/bin

# Create writable directories (read-only root FS compatible)
RUN mkdir -p /tmp/agent && chown agent:agent /tmp/agent \
    && mkdir -p /app/agent_config && chown agent:agent /app/agent_config

# Set ownership
RUN chown -R agent:agent /app

# Agent config is mounted at runtime
VOLUME ["/app/agent_config"]

USER agent

# Health check: verify the Python module is importable
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import agentburg_client; print('ok')"]

CMD ["python", "-m", "agentburg_client"]
