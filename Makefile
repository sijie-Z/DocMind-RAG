# DocMind Makefile — common dev & ops commands

.PHONY: help dev dev-backend dev-frontend install-backend install-frontend
.PHONY: test test-backend test-frontend lint build build-frontend
.PHONY: docker-up docker-down docker-logs docker-build db-migrate
.PHONY: clean clean-logs check

SHELL := /bin/bash
BACKEND_DIR := backend
FRONTEND_DIR := frontend

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Development ===

dev: ## Start both backend and frontend in dev mode
	@echo "Starting backend + frontend..."
	@cd $(BACKEND_DIR) && source venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
	@cd $(FRONTEND_DIR) && npm run dev

dev-backend: ## Start backend dev server
	cd $(BACKEND_DIR) && source venv/bin/activate && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Start frontend dev server
	cd $(FRONTEND_DIR) && npm run dev

# === Setup ===

install-backend: ## Install backend dependencies
	cd $(BACKEND_DIR) && python -m venv venv && source venv/bin/activate && pip install -r requirements.txt

install-frontend: ## Install frontend dependencies
	cd $(FRONTEND_DIR) && npm install

install: install-backend install-frontend ## Install all dependencies

# === Testing ===

test: test-backend test-frontend ## Run all tests

test-backend: ## Run backend tests
	cd $(BACKEND_DIR) && source venv/bin/activate && python -m pytest tests/ -v --tb=short

test-backend-coverage: ## Run backend tests with coverage
	cd $(BACKEND_DIR) && source venv/bin/activate && python -m pytest tests/ -v --tb=short --cov=app --cov-report=term --cov-report=html

test-frontend: ## Run frontend tests
	cd $(FRONTEND_DIR) && npx vitest run

test-frontend-coverage: ## Run frontend tests with coverage
	cd $(FRONTEND_DIR) && npx vitest run --coverage

# === Linting & Type Check ===

lint: ## Run all linters
	cd $(FRONTEND_DIR) && npm run lint

type-check: ## Run TypeScript type check
	cd $(FRONTEND_DIR) && npx vue-tsc --noEmit

# === Build ===

build-frontend: ## Build frontend for production
	cd $(FRONTEND_DIR) && npm run build

build: build-frontend ## Build everything for production

# === Docker ===

docker-up: ## Start all services with Docker Compose
	cd $(BACKEND_DIR) && docker compose up -d

docker-down: ## Stop all Docker services
	cd $(BACKEND_DIR) && docker compose down

docker-logs: ## View Docker logs
	cd $(BACKEND_DIR) && docker compose logs -f

docker-build: ## Build Docker images
	cd $(BACKEND_DIR) && docker compose build
	cd $(FRONTEND_DIR) && docker build -t docmind-frontend .

docker-restart: docker-down docker-up ## Restart all Docker services

# === Database ===

db-migrate: ## Run Alembic migrations
	cd $(BACKEND_DIR) && source venv/bin/activate && alembic upgrade head

db-migrate-new: ## Create a new Alembic migration
	cd $(BACKEND_DIR) && source venv/bin/activate && alembic revision --autogenerate -m "$(name)"

# === Utility ===

clean: clean-logs ## Clean all generated files
	cd $(BACKEND_DIR) && find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	cd $(FRONTEND_DIR) && rm -rf dist/

clean-logs: ## Clean log files
	cd $(BACKEND_DIR) && rm -f logs/*.log logs/*.log.* 

check: ## Health check for running services
	@echo "Backend:  $$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health || echo 'down')"
	@echo "Frontend: $$(curl -s -o /dev/null -w '%{http_code}' http://localhost:5173 || echo 'down')"
