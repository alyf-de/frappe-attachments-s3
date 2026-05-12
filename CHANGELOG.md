# Changelog

All notable changes to this fork are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-05-12

### Added

- Patch `v0_2_1.ensure_data_import_ignored_doctype` appends **Data Import** to **S3 File Attachment** *Ignored DocTypes* when missing, so upgraded sites match fresh installs without controller-side forcing.

### Changed

- Ignored parent DocTypes for S3 upload are read only from the **S3 File Attachment** child table (administrators may remove **Data Import** to allow those files on S3).
- **`migrate_existing_files`** loads **File** candidates with non-empty **`file_url`**, skips rows whose URL already looks off-local (``http:``/``https:`` URLs or this app’s **`generate_file`** API path), checks **`File.exists_on_disk()`** before upload, and calls **`file_upload_to_s3`** so migration matches the insert hook (ignore list, ``attached_to_doctype`` fallback, parent **`image_field`**).
- **`s3_key_generator`** hook integration normalises return values (**`frappe.cstr`**, strip leading slashes), logs hook failures with stack traces instead of a bare ``except``, warns and falls back when the hook yields no usable key, and drops the unused legacy **`doc_path`** call shape; README documents the hook contract (#17).
- README distinguishes production **Endpoint URL** values using **`https://`** from local MinIO tests served over **`http://`** (#14).

### Fixed

- Private **File** rows migrated to S3 use the same **`generate_file`** query string as new uploads (including **`file_name`**) so presigned download filenames stay consistent.

### Removed

- Stale **`doctype_list_js`** hook registration (wrong DocType name and asset path); **S3 File Attachment** Desk behaviour is unchanged because the co-located client script already loads (#13).

## [0.2.0] - 2026-05-08

### Added

- Dedicated **File** custom field `s3_object_key` (Data, length 255, read-only and visible in Desk for audit) ensured automatically on install and migrate, with an indexed lookup (prefix 191 on MariaDB, plain on Postgres) for fast presigned-URL resolution.
- Backfill patch (`v0_2_0.backfill_s3_object_key`) that copies legacy `content_hash` values into `s3_object_key` for File rows whose `file_url` was managed by this app, then clears `content_hash` on app-managed S3 rows so existing data matches the upload-hook behaviour below.

### Changed

- S3 upload MIME detection uses **`filetype`** (`filetype.guess`) with a filename-based fallback instead of **`python-magic`**, removing the **`libmagic`** system dependency.
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
[0.2.1]: https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.2.1
