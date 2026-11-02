# =============================================================================
# TA Platform — developer commands
# =============================================================================
SHELL := /bin/bash

.PHONY: help dev backend backend-stop frontend test test-backend test-frontend \
        lint lint-frontend migrate compose-up compose-down verify clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev: ## Start backend + frontend for local development (two processes)
	@bash scripts/start-backend-bg.sh
	@bun run dev

backend: ## Start the FastAPI backend in the background (auto-provisions local PostgreSQL)
	@bash scripts/start-backend-bg.sh

frontend: ## Start the Next.js frontend dev server
	@bun run dev

test: test-backend test-frontend ## Run all tests (backend + frontend)

test-backend: ## Run pytest (unit + integration on a real PostgreSQL)
	cd backend && python3 -m pytest tests/ -q

test-frontend: ## Run vitest
	bun run test

lint: ## Run ESLint (frontend)
	bun run lint

migrate: ## Apply pending database migrations
	cd backend && python3 -m alembic upgrade head

compose-up: ## Start the full stack with Docker Compose
	docker compose up --build -d
	@echo "frontend: http://localhost:3000  backend: http://localhost:8000/docs"

compose-down: ## Stop the Docker Compose stack
	docker compose down

verify: ## Run the full local verification: lint + both test suites
	bun run lint
	$(MAKE) test

clean: ## Remove local build/test artifacts
	rm -rf .next backend/.pytest_cache backend/**/__pycache__ src/**/__pycache__ || true
