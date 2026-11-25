SHELL := /bin/bash
COMPOSE := docker compose
PY := .venv/bin/python
SERVICES := engine intelligence api

.PHONY: help venv up obs-up down down-v ps logs smoke test test-common lint fixtures fe-test fe-build seed-data train

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

.env:
	cp .env.example .env

venv: ## create local venv with all dev deps
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/pip install -q -e libs/fsip_common

up: .env ## build + start core stack
	$(COMPOSE) up -d --build

obs-up: .env ## start core + observability (prometheus, grafana)
	$(COMPOSE) --profile obs up -d --build

down: ## stop stack (keeps data)
	$(COMPOSE) --profile obs down

down-v: ## stop stack and DELETE volumes
	$(COMPOSE) --profile obs down -v

ps:
	$(COMPOSE) --profile obs ps

logs: ## make logs s=engine
	$(COMPOSE) logs -f --tail=200 $(s)

smoke: ## health-check every service
	bash scripts/smoke_test.sh

test: test-common ## run all python unit tests locally
	@for s in $(SERVICES); do echo "== $$s"; (cd services/$$s && ../../.venv/bin/python -m pytest -q) || exit 1; done

test-common:
	cd libs/fsip_common && ../../.venv/bin/python -m pytest -q

lint:
	.venv/bin/ruff check libs services

fixtures: ## capture simulator fixtures (RESETS the simulator)
	bash scripts/capture_fixtures.sh

seed-data: ## generate demand dataset from offline simulator runs
	$(PY) services/intelligence/training/generate_dataset.py

train: ## train seasonal, LightGBM, and benchmark models on generated dataset
	$(PY) services/intelligence/training/train.py

fe-test:
	cd frontend && npm ci && npm test

fe-build:
	cd frontend && npm ci && npm run build

eval: ## run full evaluation matrix across all scenarios and policies
	$(PY) evaluation/harness.py --scenario all --policy all --sim-url http://localhost:8005 --assert-service-level 0.90

release: ## create new release tag (usage: make release VERSION=v1.0.0)
	@if [ -z "$(VERSION)" ]; then echo "ERROR: VERSION is required (e.g. make release VERSION=v1.0.0)"; exit 1; fi
	@echo "$(VERSION)" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+' >/dev/null || (echo "ERROR: VERSION must follow semver 'vX.Y.Z'"; exit 1)
	@if [ -n "$$(git status --porcelain)" ]; then echo "ERROR: Working tree is not clean. Commit or stash changes before release."; exit 1; fi
	@echo "Updating deploy/versions.env to $(VERSION)..."
	@sed -i.bak "s/^APP_VERSION=.*/APP_VERSION=$(VERSION)/" deploy/versions.env && rm -f deploy/versions.env.bak
	@git add deploy/versions.env
	@git commit -m "release: $(VERSION)"
	@git tag -a "$(VERSION)" -m "Release $(VERSION)"
	@echo "Release $(VERSION) created successfully. Push with: git push origin main --tags"

rollback: ## rollback deployment to specified version (usage: make rollback VERSION=v1.0.0)
	@if [ -z "$(VERSION)" ]; then echo "ERROR: VERSION is required (e.g. make rollback VERSION=v1.0.0)"; exit 1; fi
	bash scripts/deploy.sh "$(VERSION)"


