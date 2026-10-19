.PHONY: install api web test lint format format-check

install:
	python -m pip install -r requirements.lock
	python -m pip install --no-build-isolation --no-deps -e .
	cd apps/web && npm ci

api:
	python -m agentflow

web:
	cd apps/web && npm run dev

test:
	pytest

lint:
	ruff check agentflow tests
	cd apps/web && npm run lint && npm run typecheck

format:
	ruff format agentflow tests
	cd apps/web && npm run format

format-check:
	ruff format --check agentflow tests
	cd apps/web && npm run format:check
