.PHONY: install migrate migration-current api worker web test lint format format-check

install:
	python -m pip install -r requirements.lock
	python -m pip install --no-build-isolation --no-deps -e .
	cd apps/web && npm ci

api:
	python -m agentflow

worker:
	python -m agentflow.worker

migrate:
	alembic upgrade head

migration-current:
	alembic current

web:
	cd apps/web && npm run dev

test:
	pytest

lint:
	ruff check agentflow tests migrations
	cd apps/web && npm run lint && npm run typecheck

format:
	ruff format agentflow tests migrations
	cd apps/web && npm run format

format-check:
	ruff format --check agentflow tests migrations
	cd apps/web && npm run format:check
