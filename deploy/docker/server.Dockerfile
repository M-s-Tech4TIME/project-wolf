FROM python:3.13-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy the workspace-level pyproject.toml (needed for uv workspace resolution)
COPY pyproject.toml /workspace/pyproject.toml
COPY .python-version /workspace/.python-version

# Copy package sources
COPY packages/ /workspace/packages/
COPY services/server/ /workspace/services/server/

WORKDIR /workspace/services/server

# Install all deps for this service (including workspace packages)
RUN uv sync --no-dev

# Default command — production wolf-server (Phase 5.4-c launcher;
# auto-HTTPS when /etc/wolf-server/certs/{cert,key}.pem exist).
CMD ["uv", "run", "python", "-m", "wolf_server"]
