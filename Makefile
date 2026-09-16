# The venv deliberately lives outside this directory: ~/Documents is iCloud-synced,
# and iCloud evicts files to dataless stubs and injects hidden .pth files, which
# breaks virtualenvs in ways that look like hangs rather than errors.
VENV ?= $(HOME)/.venvs/sensor-ingestion
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

IMAGE ?= sensor-ingestion-service
APP_NAME ?= sensor-ingestion-service

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: venv
venv: ## Create the virtualenv outside iCloud
	python3.12 -m venv $(VENV)

.PHONY: install
install: ## Install dev dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

.PHONY: run
run: ## Run the API locally with autoreload
	$(VENV)/bin/uvicorn app.main:app --reload --port $${PORT:-8080}

.PHONY: test
test: ## Run the suite against in-memory SQLite
	$(PY) -m pytest

.PHONY: test-pg
test-pg: ## Run the same suite against a local Postgres (needs docker compose up -d)
	TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/sensors $(PY) -m pytest

.PHONY: lint
lint: ## Check formatting and lint rules
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

.PHONY: fmt
fmt: ## Auto-fix formatting and lint rules
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

.PHONY: build
build: ## Build the container image
	docker build -t $(IMAGE):latest .

.PHONY: docker-run
docker-run: ## Run the container locally on :8080
	docker run --rm -p 8080:8080 -e LOG_LEVEL=DEBUG $(IMAGE):latest

.PHONY: deploy
deploy: ## Push the app spec to DigitalOcean App Platform
	doctl apps update $$(doctl apps list --format ID,Spec.Name --no-header \
		| grep $(APP_NAME) | awk '{print $$1}') --spec .do/app.yaml

.PHONY: clean
clean: ## Remove caches and local database files
	rm -rf .pytest_cache .ruff_cache .data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
