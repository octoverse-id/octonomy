.PHONY: install run test test-sqlite lint format check migrate migration-check makemigrations openapi openapi-check audit release-check seed db-up db-down

install:
	uv sync --extra dev

run:
	uv run python manage.py runserver 0.0.0.0:8000

test:
	uv run pytest --cov-fail-under=90

test-sqlite:
	DATABASE_URL=sqlite:////tmp/octonomy-test.sqlite3 uv run pytest --cov-fail-under=90

lint:
	uv run ruff check .

format:
	uv run ruff format .

check:
	uv run python manage.py check

migrate:
	uv run python manage.py migrate

migration-check:
	uv run python manage.py makemigrations --check --dry-run

makemigrations:
	uv run python manage.py makemigrations

openapi:
	uv run python manage.py spectacular --file docs/openapi.yaml --format openapi

openapi-check:
	uv run python manage.py spectacular --file docs/openapi.yaml --format openapi
	git diff --exit-code docs/openapi.yaml

audit:
	uv export --format requirements-txt --no-emit-project --frozen | uv run pip-audit --no-deps -r /dev/stdin

release-check: lint check migration-check test openapi-check audit

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
