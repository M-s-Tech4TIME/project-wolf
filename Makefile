.PHONY: up down dev build test lint typecheck migrate seed fmt help

# Per ADR 0008 (docs/decisions/0008-native-primary-docker-supplementary.md):
# native delivery is Wolf's PRIMARY channel; Docker is supplementary.
#
# - Native-dev targets (the day-to-day path):
#     test, test-isolation, test-cov, lint, typecheck, fmt, check,
#     migrate-local, probe, install, help
#
# - Container-channel targets (build/run the supplementary stack):
#     up, down, dev, logs, migrate, revision
#
# Native dev assumes system Postgres 17 + pgvector (see ONBOARDING.md §3.4).

# ─── Docker (supplementary container channel) ────────────────────────────────

up: ## Start the full stack (Postgres, wolf-server, wolf-gateway)
	docker compose up --build -d
	@echo "Waiting for wolf-server to be healthy..."
	@docker compose exec server python -m wolf_server.management.wait_ready || true
	@echo "Stack is up. Visit http://localhost:7860/docs"

down: ## Stop the stack
	docker compose down

dev: ## Start stack with dev overlays (hot reload)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

logs: ## Tail stack logs
	docker compose logs -f

# ─── Database ─────────────────────────────────────────────────────────────────

migrate: ## Run Alembic migrations inside the wolf-server container
	docker compose exec server alembic upgrade head

migrate-local: ## Run Alembic migrations against local DB (requires DATABASE_URL env)
	cd services/server && uv run alembic upgrade head

revision: ## Create a new Alembic migration (MSG="description")
	docker compose exec server alembic revision --autogenerate -m "$(MSG)"

# ─── Testing ──────────────────────────────────────────────────────────────────

test: ## Run the full test suite
	uv run pytest services/server/tests packages/ -v --tb=short

test-isolation: ## Run cross-tenant isolation test suite (unit-level — runs in CI)
	uv run pytest services/server/tests/test_cross_tenant_isolation.py services/server/tests/test_tenant_scoped_cache.py -v --tb=short

test-isolation-live: ## Live two-tenant smoke (Phase 4 Slice 4). Requires DATABASE_URL + bootstrapped 'acme' + 'beta'.
	@bash -c 'set -a && source .env && set +a && uv run python -m tools.tenant_isolation_test'

test-cov: ## Run tests with coverage (targets: tenancy, audit, auth, models, schema)
	uv run pytest services/server/tests packages/ \
		--cov=services/server/wolf_server \
		--cov=packages/common/wolf_common \
		--cov=packages/secrets/wolf_secrets \
		--cov=packages/schema/wolf_schema \
		--cov-report=term-missing \
		--cov-fail-under=80

# ─── Code quality ─────────────────────────────────────────────────────────────

lint: ## Lint all Python with ruff
	uv run ruff check .

fmt: ## Format all Python with ruff
	uv run ruff format .

typecheck: ## Type-check safety-critical packages with mypy (strict)
	uv run mypy packages/common/wolf_common \
	           packages/secrets/wolf_secrets \
	           packages/schema/wolf_schema \
	           packages/cert/wolf_cert \
	           services/server/wolf_server/tenancy \
	           services/server/wolf_server/audit \
	           services/server/wolf_server/models \
	           services/server/wolf_server/wazuh \
	           services/server/wolf_server/guardrails \
	           services/server/wolf_server/agent \
	           --strict

probe: ## Run the model probe (PROVIDER=ollama MODEL=llama3.2)
	uv run python -m tools.model_probe --provider $(PROVIDER) --model $(MODEL)

# ─── One-shot quality gate ────────────────────────────────────────────────────

check: lint typecheck test ## Run lint + typecheck + test (CI-equivalent locally)

# ─── Utilities ────────────────────────────────────────────────────────────────

install: ## Install all Python deps via uv
	uv sync --all-packages

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
