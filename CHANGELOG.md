# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0-rc.1] - 2026-06-04

Release candidate for the Octonomy REST v1 API contract.

### Added
- Release readiness documentation, including deployment checks, smoke tests, rollback notes, and
  operational runbooks.
- CI jobs for Django system checks, migration drift checks, OpenAPI schema generation, SQLite
  tests, and PostgreSQL tests across supported Python versions.
- Production readiness Django system checks for default secrets, missing token pepper, wildcard
  hosts, and SQLite usage when `DJANGO_DEBUG=false`.

### Changed
- Project metadata now reports package version `1.0.0rc1`.
- Generated OpenAPI metadata now defaults to API version `1.0.0-rc.1`.
- README project status now describes the v1 release candidate stabilization posture.

## [0.1.0] - 2026-06-04

Initial public release.

### Added
- Tag management service: shared and application-scoped tags with `JSONB` metadata.
- Tag vocabularies for grouping tags.
- Tag aliases and synonym resolution.
- Tag assignments for external resources, including bulk replace endpoints.
- Audit logs and usage counts.
- Transactional event outbox with an outbox dispatch management command.
- Service API key authentication: hashed, peppered tokens scoped by tenant, application, and
  scopes, with create/revoke management commands.
- Multi-tenant and multi-application isolation enforced via `X-Tenant-ID` and `application_id`.
- OpenAPI schema and Swagger/ReDoc docs via drf-spectacular.
- Apache License 2.0.

[Unreleased]: https://github.com/octoverse-id/octonomy/compare/v1.0.0-rc.1...HEAD
[1.0.0-rc.1]: https://github.com/octoverse-id/octonomy/compare/v0.1.0...v1.0.0-rc.1
[0.1.0]: https://github.com/octoverse-id/octonomy/releases/tag/v0.1.0
