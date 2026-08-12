.PHONY: install run test test-sqlite lint format check migrate migration-check makemigrations openapi openapi-check audit version-check release-check seed db-up db-down collectstatic

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

# Gather the admin's static assets into STATIC_ROOT. Non-destructive: --noinput and
# no --clear, so it never wipes an existing STATIC_ROOT. Needed when serving the
# optional admin (OCTONOMY_ADMIN_ENABLED) with DEBUG=false.
collectstatic:
	uv run python manage.py collectstatic --noinput

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
	case "$$semver" in \
	*[!0-9.]*) \
		echo "version-check: $$semver is a prerelease — skipping the image gate (publish-image.yml's tag glob only publishes vX.Y.Z, so no image exists to point at)"; \
		;; \
	*) \
		./scripts/check-image-refs.sh "$$semver" \
			deploy/kubernetes/deployment.yaml \
			deploy/kubernetes/migrate-job.yaml \
			deploy/kubernetes/dispatcher-cronjob.yaml \
			deploy/docker/compose.yaml \
			docs/deployment.md \
			README.md || exit 1; \
		if grep -lE 'ghcr\.io/octoverse-id/octonomy:(latest|edge)' \
			deploy/kubernetes/deployment.yaml \
			deploy/kubernetes/migrate-job.yaml \
			deploy/kubernetes/dispatcher-cronjob.yaml \
			deploy/docker/compose.yaml; then \
			echo "version-check FAILED: the files above pin a moving tag; example deployments must pin an immutable X.Y.Z"; exit 1; \
		fi; \
		stale_tag_refs=$$(grep -hoE 'refs/tags/v[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9_-]*' \
			docs/deployment.md docs/release.md README.md | sort -u \
			| grep -v "^refs/tags/v$$semver$$" || true); \
		if [ -n "$$stale_tag_refs" ]; then \
			echo "version-check FAILED: stale release-tag reference(s) $$stale_tag_refs — this tree is v$$semver"; exit 1; \
		fi; \
		;; \
	esac; \
	echo "version-check OK: $$semver"

release-check: lint check migration-check test openapi-check audit version-check

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
