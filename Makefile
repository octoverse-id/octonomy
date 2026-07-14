.PHONY: install run test test-sqlite lint format check migrate migration-check makemigrations openapi openapi-check audit version-check release-check seed db-up db-down

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
	uv run python manage.py spectacular --api-version v1 --file docs/openapi.yaml --format openapi
	uv run python manage.py spectacular --api-version v2 --file docs/openapi-v2.yaml --format openapi

openapi-check:
	uv run python manage.py spectacular --api-version v1 --file docs/openapi.yaml --format openapi
	uv run python manage.py spectacular --api-version v2 --file docs/openapi-v2.yaml --format openapi
	git diff --exit-code docs/openapi.yaml docs/openapi-v2.yaml

audit:
	uv export --format requirements-txt --no-emit-project --frozen | uv run pip-audit --no-deps -r /dev/stdin

version-check:
	@pyproject_version=$$(grep -E '^version = ' pyproject.toml | sed -E 's/version = "([^"]+)"/\1/'); \
	semver=$$(echo "$$pyproject_version" | sed -E 's/(a|b|rc)([0-9]+)$$/-\1.\2/'); \
	settings_version=$$(grep -E 'OCTONOMY_API_VERSION' config/settings.py | sed -E 's/.*"OCTONOMY_API_VERSION", "([^"]+)".*/\1/'); \
	openapi_version=$$(grep -E '^  version: ' docs/openapi.yaml | head -n1 | sed -E 's/^  version: //'); \
	echo "pyproject=$$pyproject_version (semver $$semver) settings=$$settings_version openapi=$$openapi_version"; \
	if [ "$$semver" != "$$settings_version" ] || [ "$$semver" != "$$openapi_version" ]; then \
		echo "version-check FAILED: version strings disagree"; exit 1; \
	fi; \
	if ! grep -q "## \[$$semver\]" CHANGELOG.md; then \
		echo "version-check FAILED: CHANGELOG.md has no '## [$$semver]' section"; exit 1; \
	fi; \
	echo "version-check OK: $$semver"

release-check: lint check migration-check test openapi-check audit version-check

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
