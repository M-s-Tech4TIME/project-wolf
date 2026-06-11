.PHONY: up down dev build test test-isolation test-isolation-live test-cov \
        lint typecheck fmt check migrate migrate-local revision probe install \
        smoke-mtls smoke-database smoke-systemd smoke-deb install-user-systemd help \
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

test-isolation: ## Run cross-organization isolation test suite (unit-level — runs in CI)
	uv run pytest services/server/tests/test_cross_organization_isolation.py services/server/tests/test_organization_scoped_cache.py -v --tb=short

test-isolation-live: ## Live two-organization smoke (Phase 4 Slice 4). Requires DATABASE_URL + bootstrapped 'acme' + 'beta'.
	@bash -c 'set -a && source .env && set +a && uv run python -m tools.cross_organization_isolation'

test-cov: ## Run tests with coverage (targets: organization, audit, auth, models, schema)
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
	           services/server/wolf_server/api \
	           services/server/wolf_server/auth \
	           services/server/wolf_server/organization \
	           services/server/wolf_server/audit \
	           services/server/wolf_server/bootstrap \
	           services/server/wolf_server/models \
	           services/server/wolf_server/wazuh \
	           services/server/wolf_server/guardrails \
	           services/server/wolf_server/agent \
	           --strict

probe: ## Run the model probe (PROVIDER=ollama MODEL=llama3.2)
	uv run python -m tools.model_probe --provider $(PROVIDER) --model $(MODEL)

# ─── .deb packaging smoke (Phase 5.9-e) ───────────────────────────────────────

# End-to-end .deb build smoke. Spins up a clean debian:trixie
# container, installs the Build-Depends from debian/control, runs
# dpkg-buildpackage, verifies the four .debs are produced. Output
# .debs land in packaging/build/debs/ on the host (bind-mount).
#
# Takes ~5-10 minutes because of apt-get update + the install of
# debhelper + python3-pip + nodejs in the fresh container. Use
# before any push that touches debian/. CI runs the equivalent
# job natively on ubuntu-latest (faster, no Docker-in-Docker).
#
# Requires Docker installed locally. If you don't have it, the CI
# job is the canonical gate — push and let CI verify.
smoke-deb: ## Build all four wolf-*.deb in a clean debian container (Phase 5.9-e)
	@command -v docker >/dev/null || \
	  { echo "FAIL: docker not installed. CI is the canonical gate; push to let it run."; exit 2; }
	@mkdir -p packaging/build/debs
	@echo "=== smoke-deb: building wolf-*.deb in debian:trixie ==="
	@docker run --rm \
	    --volume "$(PWD):/src:ro" \
	    --volume "$(PWD)/packaging/build/debs:/out" \
	    debian:trixie \
	    bash -c "set -eu; \
	      apt-get update >/dev/null; \
	      apt-get install -y --no-install-recommends \
	        debhelper devscripts dh-python \
	        python3 python3-pip python3-venv python3-build \
	        nodejs npm \
	        ca-certificates \
	        >/dev/null; \
	      cp -r /src /tmp/wolf; \
	      cd /tmp/wolf; \
	      dpkg-buildpackage -b -us -uc 2>&1 | tail -50; \
	      cp /tmp/wolf-*.deb /tmp/wolf*.deb /out/ 2>/dev/null || true; \
	      ls -la /out/"
	@echo ""
	@echo "=== smoke-deb: results ==="
	@ls -la packaging/build/debs/
	@count=$$(ls packaging/build/debs/wolf*.deb 2>/dev/null | wc -l); \
	  if [ "$$count" -lt 4 ]; then \
	    echo "FAIL: expected 4 .debs (wolf-database, wolf-server, wolf-dashboard, wolf), got $$count"; \
	    exit 1; \
	  fi; \
	  echo "OK: all four .debs produced"
	@echo ""
	@echo "=== smoke-deb: PASS ==="

# ─── systemd dev units (Phase 5.8-a) ──────────────────────────────────────────

# Install user-level systemd units for the three Wolf components.
# Templates live under deploy/systemd/dev/; this target substitutes
# @REPO_ROOT@ with the current $PWD and drops the materialised files
# into ~/.config/systemd/user/. After install, enable persistent
# operation with `systemctl --user enable --now wolf-<component>`.
#
# Per ADR 0016 v3, no inter-component After=/Requires=. wolf-server
# has its own DB-reachability retry loop (see wolf_server/main.py's
# `_wait_for_database`) so a fresh boot where wolf-database is still
# coming up doesn't break wolf-server's startup.
install-user-systemd: ## Install user-level systemd units to ~/.config/systemd/user/ (Phase 5.8-a)
	@mkdir -p $${HOME}/.config/systemd/user
	@# Resolve the operator's actual `npm` location at install time
	@# so the dashboard unit works on nvm boxes (npm not in /usr/bin)
	@# as well as system-Node installs. Refuse to install if npm
	@# isn't on PATH — better to fail loudly than to ship a broken
	@# unit the operator only discovers months later.
	@command -v npm >/dev/null || \
	  { echo "FAIL: npm not on PATH. Install Node (e.g. via nvm) first."; exit 2; }
	$(eval NODE_BIN := $(shell dirname $$(command -v npm)))
	@for svc in wolf-database wolf-server wolf-dashboard; do \
	  sed -e "s|@REPO_ROOT@|$(PWD)|g" \
	      -e "s|@NODE_BIN@|$(NODE_BIN)|g" \
	      deploy/systemd/dev/$$svc.service \
	    > $${HOME}/.config/systemd/user/$$svc.service; \
	  echo "  installed: ~/.config/systemd/user/$$svc.service"; \
	done
	@echo "  (substituted @REPO_ROOT@=$(PWD)  @NODE_BIN@=$(NODE_BIN))"
	@systemctl --user daemon-reload
	@echo ""
	@echo "Installed. To enable + start one of them:"
	@echo "  systemctl --user enable --now wolf-database"
	@echo ""
	@echo "For persistent operation across logout/SSH disconnect:"
	@echo "  loginctl enable-linger \$$USER"
	@echo ""
	@echo "See deploy/systemd/dev/README.md for the full workflow."

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

# ─── systemd smoke (Phase 5.8-d) ─────────────────────────────────────────────

# End-to-end validation of Phase 5.8's systemd + shim artifacts.
# Catches regressions in unit syntax + shim correctness without
# actually starting any service. Five checks:
#
#   1. `make install-user-systemd` materialises the templates
#      with the right substitutions (@REPO_ROOT@, @NODE_BIN@).
#   2. systemd-analyze verifies each installed user-level unit
#      (catches typos in directives, bad paths post-substitution).
#   3. systemd-analyze verifies each system-level unit template
#      (the only expected complaints are about /usr/bin/wolf-*
#      not being executable until Phase 5.9/5.10 ships the .deb).
#   4. Every shim in deploy/bin/ fires its fail-loud branch with
#      exit 2 when the production venv is missing (the pre-5.9
#      state, which is always the state in CI).
#   5. install.sh --help works without sudo.
#
# Use before every Phase 5.8 push to catch regressions in
# systemd + packaging plumbing. CI runs the same target on PRs.
smoke-systemd: ## End-to-end systemd + shim smoke (Phase 5.8-d)
	@bash -c '\
		set -eu; \
		\
		echo "=== smoke-systemd: 5-check sequence ==="; \
		\
		echo "--- 1/5: install-user-systemd installs all three units ---"; \
		make install-user-systemd > /tmp/smoke-systemd-install.log 2>&1 || \
		  { echo "FAIL: install-user-systemd errored"; cat /tmp/smoke-systemd-install.log; exit 1; }; \
		for svc in wolf-database wolf-server wolf-dashboard; do \
		  [ -f $${HOME}/.config/systemd/user/$$svc.service ] || \
		    { echo "FAIL: $$svc.service not installed"; exit 1; }; \
		done; \
		echo "    OK: all three units present in ~/.config/systemd/user/"; \
		\
		echo "--- 2/5: systemd-analyze --user passes on installed dev units ---"; \
		for svc in wolf-database wolf-server wolf-dashboard; do \
		  out=$$(systemd-analyze verify --user --man=no \
		    $${HOME}/.config/systemd/user/$$svc.service 2>&1 || true); \
		  if [ -n "$$out" ]; then \
		    echo "FAIL: ~/.config/systemd/user/$$svc.service has issues:"; \
		    echo "$$out"; exit 1; \
		  fi; \
		done; \
		echo "    OK: all three installed user units are clean"; \
		\
		echo "--- 3/5: systemd-analyze passes on system-level unit templates ---"; \
		echo "    (filtering expected \"/usr/bin/wolf-* is not executable\"; that lands with the .deb)"; \
		for u in deploy/systemd/system/wolf-database.service \
		         deploy/systemd/system/wolf-server.service \
		         deploy/systemd/system/wolf-dashboard.service; do \
		  out=$$(systemd-analyze verify --man=no "$$u" 2>&1 | \
		    grep -v "is not executable" || true); \
		  if [ -n "$$out" ]; then \
		    echo "FAIL: $$u has unexpected issues:"; \
		    echo "$$out"; exit 1; \
		  fi; \
		done; \
		echo "    OK: all three system unit templates have clean directives"; \
		\
		echo "--- 4/5: every shim fails loud with exit 2 when its venv is missing ---"; \
		for shim in deploy/bin/wolf-cert deploy/bin/wolf-database \
		            deploy/bin/wolf-server deploy/bin/wolf-dashboard; do \
		  set +e; \
		  out=$$("$$shim" --help 2>&1); rc=$$?; \
		  set -e; \
		  if [ "$$rc" -ne 2 ]; then \
		    echo "FAIL: $$shim exited $$rc, expected 2"; \
		    echo "$$out"; exit 1; \
		  fi; \
		  echo "$$out" | grep -q "FAIL:" || \
		    { echo "FAIL: $$shim did not print FAIL: prefix"; exit 1; }; \
		done; \
		echo "    OK: all four shims fail-loud as designed"; \
		\
		echo "--- 5/5: install.sh --help works without sudo ---"; \
		bash deploy/bin/install.sh --help > /dev/null 2>&1 || \
		  { echo "FAIL: install.sh --help failed"; exit 1; }; \
		echo "    OK: install.sh --help reachable without root"; \
		\
		echo ""; \
		echo "=== smoke-systemd: PASS ==="; \
	'

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
		echo "--- 3/6: reconfigure (rewrites postgresql.conf + pg_hba.conf) ---"; \
		uv run --project services/server python -m wolf_database reconfigure --port "$$PORT" 2>&1 | tail -3 || \
		  { echo "FAIL: reconfigure failed"; exit 1; }; \
		test -f "$$ROOT/cfg/postgresql.conf" || \
		  { echo "FAIL: postgresql.conf missing post-reconfigure"; exit 1; }; \
		test -f "$$ROOT/cfg/pg_hba.conf" || \
		  { echo "FAIL: pg_hba.conf missing post-reconfigure"; exit 1; }; \
		echo "    OK: reconfigure rewrote both config files"; \
		\
		echo "--- 4/6: start ---"; \
		uv run --project services/server python -m wolf_database start || { echo "FAIL: start failed"; exit 1; }; \
		\
		echo "--- 5/6: status reports RUNNING ---"; \
		out=$$(uv run --project services/server python -m wolf_database status 2>&1); \
		echo "$$out" | grep -q "RUNNING" || { echo "FAIL: status should report RUNNING; got: $$out"; exit 1; }; \
		\
		echo "--- 6/6: stop + status reports STOPPED ---"; \
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
		echo "=== smoke-mtls: wolf-server is up; running 4-check sequence ==="; \
		\
		echo "--- 1/4: wolf-cert status reports CA + 3 leaves ---"; \
		st=$$(uv run --package wolf-cert python -m wolf_cert status 2>&1); \
		echo "$$st" | grep -q "CN=Wolf Root CA" || \
		  { echo "FAIL: wolf-cert status missing CA section"; echo "$$st"; exit 1; }; \
		for leaf in server dashboard dashboard-client; do \
		  echo "$$st" | grep -q "leaf '$$leaf'" || \
		    { echo "FAIL: wolf-cert status missing leaf '$$leaf'"; echo "$$st"; exit 1; }; \
		done; \
		echo "    OK: wolf-cert status reports CA + server + dashboard + dashboard-client"; \
		\
		echo "--- 2/4: no client cert  → expect 401 mtls_required ---"; \
		body=$$(curl -s --cacert "$$CA" https://localhost:7860/api/v1/auth/me); \
		echo "    response: $$body"; \
		echo "$$body" | grep -q "mtls_required" || \
		  { echo "FAIL: expected mtls_required, got: $$body"; exit 1; }; \
		\
		echo "--- 3/4: dashboard-client cert → expect 401 Not authenticated ---"; \
		body=$$(curl -s --cacert "$$CA" --cert "$$CC" --key "$$CK" https://localhost:7860/api/v1/auth/me); \
		echo "    response: $$body"; \
		echo "$$body" | grep -q "Not authenticated" || \
		  { echo "FAIL: expected \"Not authenticated\", got: $$body"; exit 1; }; \
		\
		echo "--- 4/4: /healthz loopback no-cert → expect status ok ---"; \
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
