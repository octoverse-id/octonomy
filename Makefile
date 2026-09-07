.PHONY: install run test test-sqlite lint format check static-check migrate migration-check makemigrations openapi openapi-check audit version-check release-check seed db-up db-down collectstatic

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

# Gather the bundled static assets (admin, Unfold, DRF, and the Swagger UI / Redoc
# bundles) into STATIC_ROOT, which the app then serves itself via WhiteNoise.
# Non-destructive: --noinput and no --clear, so it never wipes an existing STATIC_ROOT.
# Needed on any DEBUG=false host that does not run the container image (which collects at
# build time) — and not only for the optional admin: the always-on DRF browsable API needs
# /static/rest_framework/* too, and so do the /api/docs/ Swagger and Redoc pages.
collectstatic:
	uv run python manage.py collectstatic --noinput

# Drift gate for the deploy channels' static story. Text-level: it cannot tell you an
# asset is reachable, only that a channel still says how it would be served. Correctness
# lives in CI's `docker` job, which fetches real URLs from a real container.
static-check:
	uv run python scripts/check_static_serving.py \
		Dockerfile \
		deploy/docker/compose.yaml \
		deploy/kubernetes/deployment.yaml

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

# Asserts every place the release version is stamped agrees. Runs on every PR and push
# via the CI `checks` job, not only at release time — a release tag can never be moved
# (the `release tags: never move` ruleset has no bypass), so a gate that first ran at tag
# time ran after the irreversible step.
#
# The SECURITY.md block encodes a POLICY, not just a format: exactly one release line is
# supported at a time. Listing a second line is a commitment to backport into it, which is
# a decision to make deliberately rather than by editing a table row. If the project ever
# does decide to support two lines, this gate has to be updated in the same change — that
# coupling is the point.
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
	env_count=$$(grep -cE '^OCTONOMY_API_VERSION=' .env.example || true); \
	if [ "$$env_count" != "1" ]; then \
		echo "version-check FAILED: .env.example has $$env_count active OCTONOMY_API_VERSION lines, expected exactly 1"; exit 1; \
	fi; \
	env_version=$$(grep -E '^OCTONOMY_API_VERSION=' .env.example | sed -E 's/^OCTONOMY_API_VERSION=//'); \
	if [ "$$env_version" != "$$semver" ]; then \
		echo "version-check FAILED: .env.example pins OCTONOMY_API_VERSION=$$env_version, expected $$semver"; exit 1; \
	fi; \
	lock_version=$$(awk '/^\[\[package\]\]/ { name=""; ver="" } \
		/^name = / { name=$$0 } \
		/^version = / { ver=$$0 } \
		/^source = \{ virtual = "\." \}$$/ { if (name == "name = \"octonomy\"") { sub(/^version = "/, "", ver); sub(/"$$/, "", ver); print ver; exit } }' uv.lock); \
	if [ -z "$$lock_version" ]; then \
		echo "version-check FAILED: could not read the octonomy package version from uv.lock"; exit 1; \
	fi; \
	if [ "$$lock_version" != "$$pyproject_version" ]; then \
		echo "version-check FAILED: uv.lock has octonomy $$lock_version, pyproject.toml has $$pyproject_version — run 'uv lock'"; exit 1; \
	fi; \
	case "$$semver" in \
	*[!0-9.]*) \
		echo "version-check: $$semver is a prerelease — skipping the image gate (publish-image.yml's tag glob only publishes vX.Y.Z, so no image exists to point at)"; \
		echo "version-check: $$semver is a prerelease — skipping the SECURITY.md gate (a prerelease is not a supported line)"; \
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
		minor_line=$$(echo "$$semver" | sed -E 's/^([0-9]+)\.([0-9]+)\..*/\1.\2.x/'); \
		minor_re=$$(echo "$$minor_line" | sed 's/\./\\./g'); \
		supported_rows=$$(grep -cE '^\| *[0-9]+\.[0-9]+\.x *\| *✅' SECURITY.md || true); \
		if [ "$$supported_rows" != "1" ]; then \
			echo "version-check FAILED: SECURITY.md marks $$supported_rows release lines as supported, expected exactly 1 — a second supported line is a backport commitment nobody decided to make"; exit 1; \
		fi; \
		if ! grep -qE "^\| *$$minor_re *\| *✅" SECURITY.md; then \
			echo "version-check FAILED: SECURITY.md has no supported-table row marking $$minor_line as supported (a row present but marked unsupported fails here too)"; exit 1; \
		fi; \
		cutoff=$$(echo "$$semver" | sed -E 's/^([0-9]+)\.([0-9]+)\..*/\1.\2/'); \
		cutoff_re=$$(echo "$$cutoff" | sed 's/\./\\./g'); \
		if ! grep -qE "^\| *< *$$cutoff_re *\| *❌" SECURITY.md; then \
			echo "version-check FAILED: SECURITY.md has no '< $$cutoff' unsupported cutoff row — a stale cutoff silently keeps older lines looking covered"; exit 1; \
		fi; \
		if ! grep -qE "latest \`$$minor_re\` line" SECURITY.md; then \
			echo "version-check FAILED: SECURITY.md prose does not name $$minor_line as the supported line (it can drift from the table)"; exit 1; \
		fi; \
		;; \
	esac; \
	echo "version-check OK: $$semver (env=$$env_version lock=$$lock_version)"

release-check: lint check static-check migration-check test openapi-check audit version-check

seed:
	uv run python manage.py seed_demo

db-up:
	docker compose up -d db

db-down:
	docker compose down
