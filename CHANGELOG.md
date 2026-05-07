# Changelog

All notable changes to this fork are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-07

Initial ALYF fork release on `version-15` and `develop`, including all changes since forking from upstream `zerodha/frappe-attachments-s3`.

### Added

- Support for custom S3-compatible endpoint URLs via **S3 File Attachment** _Endpoint URL_.
- Characterization tests for upstream controller behavior and dedicated TDD slices for fork features.
- Configurable ignored DocTypes for S3 upload behavior via child table instead of `site_config.json`
- German locale support and updated translation templates.
- CI additions on top of baseline linting (server test workflow, Dependabot, CodeQL, vulnerability check job).

### Changed

- Repository tooling aligned with ALYF baseline (Ruff, pre-commit, Semgrep, commitlint, modern `pyproject.toml` layout).
- Improved **S3 File Attachment** field descriptions and validation behavior.
- `README.md` expanded with upstream delta documentation and branch guidance.
- Package version advanced from pre-release line (`0.0.x`) to stable `0.1.0`.

### Fixed

- Non-ASCII filename handling for metadata and download content disposition (RFC 5987).
- Signed URL generation now enforces **File** read permission checks.
- Internal upload hook is no longer exposed as a whitelisted API endpoint.
- Credential retrieval uses robust password access handling.
- Custom endpoint client setup now uses path-style S3 addressing for compatibility with endpoint-based providers.

### Security

- Closed a privilege-escalation path in signed URL generation by checking **File** permissions before redirecting.

[0.1.0]: https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.1.0
