.PHONY: up down dev build test test-isolation test-isolation-live test-cov \
        lint typecheck fmt check migrate migrate-local revision probe install \
        smoke-mtls smoke-database help \
        wolf-database-init wolf-database-up wolf-database-down \
        wolf-database-status wolf-database-reconfigure

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

# ─── wolf-database (Phase 5.7) ────────────────────────────────────────────────

# Operator-facing wrappers around the `wolf-database` CLI. Each target
# is a thin shell around `python -m wolf_database <sub>` so the
# Makefile is the single source of truth for the dev workflow. Pass
# args via the MAKEFLAGS `--` separator or via `WOLF_DATABASE_*` env
# vars per the CLI's own docs.

wolf-database-init: ## wolf-database init — initdb + config + role + db + pgvector (Phase 5.7)
	uv run --project services/server python -m wolf_database init $(if $(PORT),--port $(PORT))

wolf-database-up: ## wolf-database start — bring the cluster up
	uv run --project services/server python -m wolf_database start

wolf-database-down: ## wolf-database stop — bring the cluster down (mode=fast)
	uv run --project services/server python -m wolf_database stop

wolf-database-status: ## wolf-database status — running state + layout summary
	uv run --project services/server python -m wolf_database status

wolf-database-reconfigure: ## Rewrite wolf-database config from env (operator restarts to apply)
	uv run --project services/server python -m wolf_database reconfigure

# ─── wolf-database smoke (Phase 5.7-d) ────────────────────────────────────────

# End-to-end smoke for the wolf-database CLI against tmp paths so it
# doesn't disturb the operator's real `.local/` cluster. Verifies the
# full lifecycle: status (empty data dir) → init (port 17860 to avoid
# the common 5432 collision) → status (running) → stop → status
# (stopped). Detects the postgresql-17-pgvector-missing case and
# reports it as an environmental prerequisite rather than a smoke
# failure (operators on hosts without pgvector still see that the
# CLI behaves correctly up to that gate).
#
# Use before every Phase 5.7 push to catch regressions in
# wolf-database. The CI smoke-database job installs pgvector and
# runs the full chain.
smoke-database: ## End-to-end smoke for the wolf-database CLI lifecycle (Phase 5.7-d)
	@bash -c '\
		set -eu; \
		ROOT=/tmp/wd-stack-smoke; \
		PORT=17860; \
		cleanup() { uv run --project services/server python -m wolf_database stop 2>/dev/null || true; rm -rf "$$ROOT"; }; \
		trap cleanup EXIT; \
		\
		rm -rf "$$ROOT"; \
		export WOLF_DATABASE_DATA_DIR="$$ROOT/data"; \
		export WOLF_DATABASE_CONFIG_DIR="$$ROOT/cfg"; \
		export WOLF_DATABASE_SOCKET_DIR="$$ROOT/sock"; \
		\
		echo "=== smoke-database: against $$ROOT on port $$PORT ==="; \
		\
		echo "--- 1/5: status on missing data dir ---"; \
		out=$$(uv run --project services/server python -m wolf_database status 2>&1); \
		echo "$$out" | grep -q "DATA DIR MISSING" || { echo "FAIL: status should report DATA DIR MISSING; got: $$out"; exit 1; }; \
		\
		echo "--- 2/5: init (will detect pgvector availability) ---"; \
		init_out=$$(uv run --project services/server python -m wolf_database init --port "$$PORT" 2>&1) || init_rc=$$?; \
		init_rc=$${init_rc:-0}; \
		if [ "$$init_rc" -ne 0 ]; then \
			if echo "$$init_out" | grep -q "pgvector"; then \
				echo "    SKIP: postgresql-17-pgvector not installed on this host."; \
				echo "    The CLI failed gracefully with the install hint, as designed."; \
				echo "    Install pgvector and re-run to validate the full chain:"; \
				echo "      sudo apt install postgresql-17-pgvector"; \
				echo ""; \
				echo "=== smoke-database: PARTIAL PASS (pgvector required for full smoke) ==="; \
				exit 0; \
			fi; \
			echo "FAIL: init exited $$init_rc unexpectedly"; \
			echo "$$init_out"; \
			exit 1; \
		fi; \
		echo "    init succeeded"; \
		\
		echo "--- 3/5: start ---"; \
		uv run --project services/server python -m wolf_database start || { echo "FAIL: start failed"; exit 1; }; \
		\
		echo "--- 4/5: status reports RUNNING ---"; \
		out=$$(uv run --project services/server python -m wolf_database status 2>&1); \
		echo "$$out" | grep -q "RUNNING" || { echo "FAIL: status should report RUNNING; got: $$out"; exit 1; }; \
		\
		echo "--- 5/5: stop + status reports STOPPED ---"; \
		uv run --project services/server python -m wolf_database stop || { echo "FAIL: stop failed"; exit 1; }; \
		out=$$(uv run --project services/server python -m wolf_database status 2>&1); \
		echo "$$out" | grep -q "STOPPED" || { echo "FAIL: status should report STOPPED; got: $$out"; exit 1; }; \
		\
		echo ""; \
		echo "=== smoke-database: PASS ==="; \
	'

# ─── mTLS smoke (Phase 5.6-e) ─────────────────────────────────────────────────

# Three-curl smoke that proves wolf-server's mTLS posture is correctly
# enforced end-to-end. Use before every push to catch regressions in the
# mTLS code path. Assumes wolf-server is ALREADY running on :7860 in
# HTTPS + mTLS mode (i.e. `wolf-cert init` has been run and wolf-server
# was started fresh after that). The target fails fast with a helpful
# message when the prerequisites are absent rather than producing a
# confusing curl error.
smoke-mtls: ## mTLS smoke: no-cert → 401 mtls_required, with-cert → 401 auth, /healthz → 200 (Phase 5.6-e)
	@bash -c '\
		set -eu; \
		CA=.local/certs/ca/ca-cert.pem; \
		CC=.local/certs/dashboard-client/cert.pem; \
		CK=.local/certs/dashboard-client/key.pem; \
		\
		[ -f "$$CA" ] || { echo "FAIL: $$CA not found. Run \`wolf-cert init\` first."; exit 2; }; \
		[ -f "$$CC" ] || { echo "FAIL: $$CC not found. Run \`wolf-cert init\` first."; exit 2; }; \
		[ -f "$$CK" ] || { echo "FAIL: $$CK not found. Run \`wolf-cert init\` first."; exit 2; }; \
		\
		curl -s --cacert "$$CA" --max-time 5 -o /dev/null https://localhost:7860/healthz || \
		  { echo "FAIL: wolf-server not reachable on https://localhost:7860 (start it first)"; exit 2; }; \
		\
		echo "=== smoke-mtls: wolf-server is up; running 3-check sequence ==="; \
		\
		echo "--- 1/3: no client cert  → expect 401 mtls_required ---"; \
		body=$$(curl -s --cacert "$$CA" https://localhost:7860/api/v1/auth/me); \
		echo "    response: $$body"; \
		echo "$$body" | grep -q "mtls_required" || \
		  { echo "FAIL: expected mtls_required, got: $$body"; exit 1; }; \
		\
		echo "--- 2/3: dashboard-client cert → expect 401 Not authenticated ---"; \
		body=$$(curl -s --cacert "$$CA" --cert "$$CC" --key "$$CK" https://localhost:7860/api/v1/auth/me); \
		echo "    response: $$body"; \
		echo "$$body" | grep -q "Not authenticated" || \
		  { echo "FAIL: expected \"Not authenticated\", got: $$body"; exit 1; }; \
		\
		echo "--- 3/3: /healthz loopback no-cert → expect status ok ---"; \
		body=$$(curl -s --cacert "$$CA" https://localhost:7860/healthz); \
		echo "    response: $$body"; \
		echo "$$body" | grep -q "\"status\":\"ok\"" || \
		  { echo "FAIL: expected status ok, got: $$body"; exit 1; }; \
		\
		echo "=== smoke-mtls: PASS ==="; \
	'

# ─── One-shot quality gate ────────────────────────────────────────────────────

check: lint typecheck test ## Run lint + typecheck + test (CI-equivalent locally)

# ─── Utilities ────────────────────────────────────────────────────────────────

install: ## Install all Python deps via uv
	uv sync --all-packages

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
