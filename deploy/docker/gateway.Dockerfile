FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml /workspace/pyproject.toml
COPY .python-version /workspace/.python-version
COPY packages/ /workspace/packages/
COPY services/gateway/ /workspace/services/gateway/

WORKDIR /workspace/services/gateway

RUN uv sync --no-dev

CMD ["uv", "run", "uvicorn", "wolf_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
