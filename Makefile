.PHONY: up down dev build test lint typecheck migrate seed fmt help

# ─── Docker ───────────────────────────────────────────────────────────────────

up: ## Start the full stack (Postgres, orchestrator, gateway)
	docker compose up --build -d
	@echo "Waiting for orchestrator to be healthy..."
	@docker compose exec orchestrator python -m app.management.wait_ready || true
	@echo "Stack is up. Visit http://localhost:8000/docs"

down: ## Stop the stack
	docker compose down

dev: ## Start stack with dev overlays (hot reload)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

logs: ## Tail stack logs
	docker compose logs -f

# ─── Database ─────────────────────────────────────────────────────────────────

migrate: ## Run Alembic migrations inside the orchestrator container
	docker compose exec orchestrator alembic upgrade head

migrate-local: ## Run Alembic migrations against local DB (requires DATABASE_URL env)
	cd services/orchestrator && uv run alembic upgrade head

revision: ## Create a new Alembic migration (MSG="description")
	docker compose exec orchestrator alembic revision --autogenerate -m "$(MSG)"

# ─── Testing ──────────────────────────────────────────────────────────────────

test: ## Run the full test suite
	uv run pytest services/orchestrator/tests packages/ -v --tb=short

test-isolation: ## Run cross-tenant isolation test suite
	uv run pytest services/orchestrator/tests/test_cross_tenant_isolation.py tools/tenant_isolation_test/ -v --tb=short

test-cov: ## Run tests with coverage (targets: tenancy, audit, auth, models, schema)
	uv run pytest services/orchestrator/tests packages/ \
		--cov=services/orchestrator/app \
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
	           services/orchestrator/app/tenancy \
	           services/orchestrator/app/audit \
	           services/orchestrator/app/models \
	           services/orchestrator/app/wazuh \
	           services/orchestrator/app/guardrails \
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
