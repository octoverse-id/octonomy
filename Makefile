.PHONY: install run test test-sqlite lint format check migrate migration-check makemigrations openapi openapi-check release-check seed db-up db-down

install:
	uv sync --extra dev

run:
	uv run python manage.py runserver 0.0.0.0:8000

test:
	uv run pytest

test-sqlite:
	DATABASE_URL=sqlite:////tmp/octonomy-test.sqlite3 uv run pytest

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
	uv run python manage.py spectacular --file /tmp/octonomy-openapi.yaml --format openapi

release-check: lint check migration-check test openapi-check

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
