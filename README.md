## Frappe S3 Attachment

Frappe app to make file upload automatically upload and read from S3.  
Maintained as a fork of [zerodha/frappe-attachments-s3](https://github.com/zerodha/frappe-attachments-s3) under [alyf-de/frappe-attachments-s3](https://github.com/alyf-de/frappe-attachments-s3).

The **v15** line ships custom endpoint support (e.g. Hetzner), ASCII-safe filenames, permission-checked signed URLs, a dedicated **File** `s3_object_key` locator field with automatic install/migrate setup, characterization and TDD tests, and tagged releases **v0.2.0** / **v0.2.1**. Optional next steps include a consolidated **v16** forward-compatibility pass and further hardening—not required for normal installs.

#### Features

1. Upload both public and private files to S3.
2. Stream files from S3 when a file is viewed (private files use a time-limited signed URL).
3. Configure credentials and bucket settings from Desk (**S3 File Attachment** singleton): _Bucket Name_, _Access Key_, _Secret Key_ (stored as **Password**), _S3 Bucket Region Name_, optional _Endpoint URL_ for S3-compatible providers, _Folder Name_, and migration of existing files.
4. Delete objects in S3 when the **File** document is removed in Desk when _Delete file from cloud_ is enabled.
5. Files are stored under `{folder}/{year}/{month}/{day}/{doctype}/{random}_{filename}` (see _Folder Name_).
6. Exclude parent DocTypes from automatic S3 upload via the **S3 Ignored DocType Row** child table on **S3 File Attachment**; **Data Import** is listed there by default (patch **v0.2.1** backfills the row on migrate if missing; you may remove it to allow **Data Import** files on S3).

#### Installation

1. `bench get-app https://github.com/alyf-de/frappe-attachments-s3 --branch version-15`
2. `bench install-app frappe_s3_attachment`

To pin an exact revision, checkout tag [`v0.2.1`](https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.2.1) (or [`v0.2.0`](https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.2.0)) after clone or install from the `version-15` branch for the latest fixes on that line. Release notes: [CHANGELOG.md](CHANGELOG.md).

#### Branches

- `develop`: default branch; upstream rebases and feature work land here first.
- `version-15`: stable branch for Frappe v15 (customer installs typically use this branch or tag **v0.2.1**).
- `version-16`: to be created at v16 cutover.

#### Changes vs upstream

Functional and maintenance differences from [zerodha/frappe-attachments-s3](https://github.com/zerodha/frappe-attachments-s3):

| Topic | Upstream behaviour | This fork |
| ----- | ------------------ | --------- |
| **S3-compatible endpoint** | Uses default AWS endpoints only. | **S3 File Attachment** includes an endpoint URL field; when set, it is passed to `boto3.client(..., endpoint_url=...)` with **path-style** addressing so providers such as **Hetzner Object Storage** work reliably. |
| **Non-ASCII filenames** | Raw names in S3 metadata / content-disposition can break uploads or downloads for some characters. | Metadata `file_name` is ASCII-normalized; presigned `get_object` responses use RFC 5987 `filename*` for `ResponseContentDisposition` so original Unicode names round-trip in browsers. |
| **MIME / content type** | Uses **`python-magic`** (`magic.from_file`) to pick `ContentType` for the S3 upload. | Uses **`filetype`** (`filetype.guess`) with a `mimetypes.guess_type` fallback—no `libmagic` dependency and aligned with how newer Frappe versions sniff types. |
| **S3 object key storage** | Stored the S3 object key in the core **File** `content_hash` field, conflicting with Frappe's content-identity / dedupe semantics and risking column-length issues for long keys. | Stores the key in a dedicated **File** custom field `s3_object_key` (Data, length 255, read-only and visible in the Desk for audit, indexed with prefix 191 on MariaDB) ensured automatically on install/migrate; an idempotent backfill patch copies legacy values from `content_hash`. After upload, **File** `content_hash` is cleared so core duplicate detection does not apply to S3-backed rows (same practical outcome as for remote URLs; see [issue #12](https://github.com/alyf-de/frappe-attachments-s3/issues/12)). |
| **`generate_file` (signed URL)** | Any authenticated caller could request a presigned URL if they knew or guessed the object key (key stored in **File** `content_hash`). | Resolves the **File** row by `s3_object_key`, runs **`check_permission('read')`** on that **File**, then redirects; missing row raises **Does Not Exist** (404). |
| **Ignored DocTypes** | Effectively a fixed skip list (e.g. **Data Import**). | Child table **S3 Ignored DocType Row** on the singleton; **Data Import** is seeded by default and can be removed if you want those files on S3. |
| **Upload hook exposure** | `file_upload_to_s3` was whitelisted like other helpers. | Hook is **not** whitelisted; only intentional API entry points (e.g. `generate_file`, `migrate_existing_files`) remain exposed. |
| **Credentials** | Typical upstream installs used plain **Data** for secrets. | _Secret Key_ uses **Password**; reads use `get_password`. |
| **Quality / CI** | Minimal upstream tooling. | Ruff, pre-commit, Semgrep (Frappe rules), commitlint, GitHub Actions server tests (`bench run-tests` with MariaDB/Redis), vulnerable-dependency check (`pip-audit`), CodeQL, Dependabot. |

**Git anchors** (rebases and whitespace-heavy diffs):

- ALYF CI baseline: `2274ea1` — `chore(ci): adopt ALYF baseline configuration`
- One-time format pass (compare semantics after this point): `b595155` — `chore: apply ruff format and lint to upstream baseline`

```bash
git diff b595155..HEAD -- path/to/file.py
```

**Current release**: [`v0.2.1`](https://github.com/alyf-de/frappe-attachments-s3/releases/tag/v0.2.1) — see [CHANGELOG.md](CHANGELOG.md).

#### Known limitations

These match upstream unless noted; further hardening is tracked as follow-up work, not regressions introduced only in this fork:

- **Duplicate uploads**: S3-managed **File** rows intentionally keep `content_hash` empty after upload so Frappe does not deduplicate against them (aligned with remote-file behaviour and [issue #12](https://github.com/alyf-de/frappe-attachments-s3/issues/12)). Handling duplication for remote resources would require adaptions to the core File DocType and is currently considered out of scope for this app. 

Optional automation backlog: v16 compatibility audit (test base classes, explicit **boto3** pin).

#### MinIO integration tests

**S3 File Attachment** validates _Endpoint URL_ as **HTTPS-only** in Desk (TLS for real object stores such as Hetzner). The `http://127.0.0.1:9000` value in the commands below is **for these tests only**: settings come from environment variables and are not saved through the form. Pasting the same URL into Desk will fail validation; use MinIO behind HTTPS (for example a local reverse proxy) for a working Desk setup.

The integration suite in `frappe_s3_attachment/tests/test_minio_integration.py` is opt-in and runs only when `RUN_MINIO_INTEGRATION_TESTS=1`.

Start a local MinIO instance:

```bash
docker run --rm -d \
  --name frappe-s3-minio \
  -p 9000:9000 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  quay.io/minio/minio:latest \
  server /data --address ":9000"
```

Run tests with MinIO enabled:

```bash
export RUN_MINIO_INTEGRATION_TESTS=1
export FRAPPE_S3_ATTACHMENT_MINIO_ENDPOINT="http://127.0.0.1:9000"
export FRAPPE_S3_ATTACHMENT_MINIO_ACCESS_KEY="minioadmin"
export FRAPPE_S3_ATTACHMENT_MINIO_SECRET_KEY="minioadmin"
export FRAPPE_S3_ATTACHMENT_MINIO_BUCKET="frappe-s3-attachment-test"
export FRAPPE_S3_ATTACHMENT_MINIO_REGION="us-east-1"
bench --site <site> run-tests --app frappe_s3_attachment
```

When `RUN_MINIO_INTEGRATION_TESTS` is unset (or not `"1"`), these tests are skipped and the standard mocked suite still runs.

#### Hetzner Object Storage

1. Create a bucket and access keys in your Hetzner Cloud project.
2. In **S3 File Attachment**, set _Endpoint URL_ to `https://<location>.your-objectstorage.com` (for example `fsn1`, `nbg1`, or `hel1`).
3. Set _S3 Bucket Region Name_ to the corresponding region/location code you use with Hetzner.
4. **Public** attachments rely on per-object `ACL: public-read` (existing upstream behaviour). Ensure your project allows object ACLs for public reads if you need **Public** files; bucket policy alone may not be enough.

#### Desk configuration

1. Open the **S3 File Attachment** single.
2. Enter _Bucket Name_, _Access Key_, _Secret Key_, _S3 Bucket Region Name_, optional _Endpoint URL_ (must be `https://` if set), and _Folder Name_ as needed. _Folder Name_ is the default prefix inside the bucket for generated keys.
3. Use _Migrate Existing Files_ to upload files that still live under `sites/<site>/public` and `private` folders into the bucket.
4. Enable _Delete file from cloud_ if removed **File** rows should delete the corresponding S3 object.

#### License

MIT
