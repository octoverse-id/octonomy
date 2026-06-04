# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Open-source project scaffolding: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  this changelog, GitHub Actions CI, and issue/PR templates.

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

[Unreleased]: https://github.com/octoverse-id/octonomy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/octoverse-id/octonomy/releases/tag/v0.1.0
