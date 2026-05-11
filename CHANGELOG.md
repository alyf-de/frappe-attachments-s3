# Changelog

All notable changes to this fork are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-08

### Added

- Dedicated **File** custom field `s3_object_key` (Data, length 255, hidden + read-only) ensured automatically on install and migrate, with an indexed lookup (prefix 191 on MariaDB, plain on Postgres) for fast presigned-URL resolution.
- Backfill patch (`v0_2_0.backfill_s3_object_key`) that copies legacy `content_hash` values into `s3_object_key` for File rows whose `file_url` was managed by this app, then clears `content_hash` on app-managed S3 rows so existing data matches the upload-hook behaviour below.

### Changed

- Upload, presigned URL generation, and cloud-delete flows now read and write `s3_object_key` instead of overloading the core `content_hash` field.
- After each successful S3 upload, **File** `content_hash` is cleared so Frappe core does not treat subsequent uploads as duplicates of S3-backed rows (same intent as dedupe bypass for remote URLs; avoids the private-file crash in [issue #12](https://github.com/alyf-de/frappe-attachments-s3/issues/12)).
- `delete_from_cloud` is a no-op for File rows without an `s3_object_key`, so unrelated File deletions do not call out to S3.

### Fixed

- Stores the S3 object key in `s3_object_key` instead of the core `content_hash` column, avoiding column-length and misuse issues when the key was previously written into `content_hash` (see [issue #10](https://github.com/alyf-de/frappe-attachments-s3/issues/10)).

## [0.1.1] - 2026-05-08

### Added

- MinIO-backed integration tests for public/private upload, signed URL generation, and delete-from-cloud behavior.

### Changed

- CI workflow now includes a dedicated MinIO integration job and treats MinIO test failures as blocking.
- README documents local MinIO setup and environment variables for running integration tests.

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
[0.1.1]: https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.1.1
[0.2.0]: https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.2.0
