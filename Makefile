.PHONY: install run test lint format migrate makemigrations openapi seed db-up db-down

install:
	uv sync --extra dev

run:
	uv run python manage.py runserver 0.0.0.0:8000

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

migrate:
	uv run python manage.py migrate

makemigrations:
	uv run python manage.py makemigrations

openapi:
	uv run python manage.py spectacular --file docs/openapi.yaml --format openapi

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
