# AgentRail root task runner.
#
# `make verify` is the contract: it runs exactly the deterministic checks that
# CI runs, needs no model-provider credentials, and needs no running services.
# Anything requiring PostgreSQL, Redis or a browser lives behind `integration`
# or `e2e`.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose -f infra/compose/docker-compose.yml
UV := uv
PNPM := pnpm
PY_SRC := packages/core-py/src services/api/src services/worker/src services/cloudops-sandbox/src

.PHONY: help bootstrap dev verify format format-check lint typecheck test test-integration integration e2e \
        build contracts contracts-check benchmark-smoke benchmark-quality benchmark-failures \
        benchmark-tribunal benchmark-load benchmark-report migrate migrate-status compose-up \
        compose-up-apps compose-down compose-logs clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Install every toolchain dependency and create a local .env
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	$(UV) sync --all-packages
	$(PNPM) install
	@echo "Bootstrap complete. Next: make compose-up && make migrate && make dev"

dev: ## Run the API, worker, sandbox and web app locally (Ctrl-C to stop)
	@echo "Starting AgentRail. Dependencies must already be up (make compose-up)."
	@trap 'kill 0' EXIT INT TERM; \
	$(UV) run agentrail-cloudops-sandbox & \
	$(UV) run agentrail-api & \
	$(UV) run agentrail-worker & \
	$(PNPM) --filter @agentrail/web dev & \
	wait

verify: format-check lint typecheck test contracts-check ## Run every deterministic check CI runs

format: ## Format Python and TypeScript sources
	$(UV) run ruff format .
	$(UV) run ruff check --fix-only .
	$(PNPM) run format

format-check: ## Verify formatting without rewriting files
	$(UV) run ruff format --check .
	$(PNPM) run format:check

lint: ## Lint Python and TypeScript sources
	$(UV) run ruff check .
	$(PNPM) run lint

typecheck: ## Strict type checking for Python and TypeScript
	$(UV) run mypy $(PY_SRC)
	$(PNPM) run typecheck

test: ## Unit tests (integration tests are skipped when dependencies are absent)
	$(UV) run pytest
	$(PNPM) run test

benchmark-smoke: ## Generate the deterministic smoke benchmark artifacts
	$(UV) run python scripts/benchmark.py smoke

benchmark-quality: ## Generate the frozen quality benchmark artifacts
	$(UV) run python scripts/benchmark.py quality

benchmark-failures: ## Generate failure-injected benchmark artifacts
	$(UV) run python scripts/benchmark.py failures

benchmark-tribunal: ## Generate tribunal quality benchmark artifacts
	$(UV) run python scripts/benchmark.py tribunal

benchmark-load: ## Generate offline load benchmark artifacts
	$(UV) run python scripts/benchmark.py load

benchmark-report: ## Generate all benchmark artifacts plus docs/benchmarks/RESUME_METRICS.md
	$(UV) run python scripts/benchmark.py report

integration: ## Tests that require real PostgreSQL and Redis (make compose-up first)
	AGENTRAIL_REQUIRE_INTEGRATION=1 $(UV) run pytest -m integration

test-integration: integration ## Alias kept for build-plan terminology

chaos-duplicate: ## Redeliver a run id to the queue N times (RUN_ID=... [TIMES=3])
	$(UV) run python scripts/chaos.py duplicate-delivery --run-id $(RUN_ID) --times $(or $(TIMES),3)

chaos-strand: ## Expire every live lease on a run, as a killed worker would (RUN_ID=...)
	$(UV) run python scripts/chaos.py strand-leases --run-id $(RUN_ID)

chaos-report: ## Report side effects vs attempts for a run; non-zero exit on a duplicate (RUN_ID=...)
	$(UV) run python scripts/chaos.py report --run-id $(RUN_ID)

e2e: ## Playwright end-to-end tests against a running stack (make compose-up-apps first)
	$(PNPM) --filter @agentrail/web exec playwright install chromium
	$(PNPM) --filter @agentrail/web build
	$(PNPM) --filter @agentrail/web e2e

build: ## Build the web app and the Python distributions
	$(PNPM) --filter @agentrail/web build
	$(UV) build --all-packages

contracts: ## Regenerate the OpenAPI snapshot and the TypeScript client types
	$(UV) run python scripts/export_openapi.py
	$(PNPM) --filter @agentrail/contracts generate
	$(PNPM) run format

contracts-check: ## Fail if the committed contracts are stale
	$(UV) run python scripts/export_openapi.py --check
	$(PNPM) --filter @agentrail/contracts check

migrate: ## Apply database migrations (run from the repo root so .env is honoured)
	$(UV) run alembic -c services/api/alembic.ini upgrade head

migrate-status: ## Show the current and available migration revisions
	$(UV) run alembic -c services/api/alembic.ini current
	$(UV) run alembic -c services/api/alembic.ini heads

compose-up: ## Start PostgreSQL, Redis and MinIO
	$(COMPOSE) up -d postgres redis minio
	@$(COMPOSE) ps

compose-up-apps: ## Start the full stack including the AgentRail services
	$(COMPOSE) --profile apps up -d --build
	@$(COMPOSE) ps

compose-down: ## Stop the local stack (volumes are preserved)
	$(COMPOSE) --profile apps --profile observability down

compose-logs: ## Tail logs from the local stack
	$(COMPOSE) logs -f --tail=100

clean: ## Remove build outputs, caches and local volumes
	$(COMPOSE) --profile apps --profile observability down -v || true
	rm -rf .venv node_modules apps/web/.next apps/web/node_modules \
		packages/*/node_modules apps/web/playwright-report apps/web/test-results \
		.pytest_cache .ruff_cache .mypy_cache dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
