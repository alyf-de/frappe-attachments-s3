## Frappe S3 Attachment

Frappe app to make file upload automatically upload and read from S3.  
Maintained as a fork of [zerodha/frappe-attachments-s3](https://github.com/zerodha/frappe-attachments-s3) under [alyf-de/frappe-attachments-s3](https://github.com/alyf-de/frappe-attachments-s3).

#### Features

1. Upload both public and private files to S3.
2. Stream files from S3 when a file is viewed (private files use a time-limited signed URL).
3. Configure S3 credentials (access key, secret key, bucket name, folder name, optional endpoint URL) from Desk and migrate existing files.
4. Delete objects in S3 when the **File** document is removed in Desk (when enabled).
5. Files are stored under `{s3_folder_path}/{year}/{month}/{day}/{doctype}/{random}_{filename}` (see **S3 File Attachment** _Folder Name_).

#### Installation

1. `bench get-app https://github.com/alyf-de/frappe-attachments-s3 --branch version-15`
2. `bench install-app frappe_s3_attachment`

#### Branches

- `develop`: default development branch.
- `version-15`: stable branch for Frappe v15 installations.

#### Changes vs upstream

Functional and maintenance differences from [zerodha/frappe-attachments-s3](https://github.com/zerodha/frappe-attachments-s3):

| Topic | Upstream behaviour | This fork |
| ----- | ------------------ | --------- |
| **S3-compatible endpoint** | Uses default AWS endpoints only. | **S3 File Attachment** includes _Endpoint URL_; when set, it is passed to `boto3.client(..., endpoint_url=...)` so buckets on providers such as Hetzner Object Storage work without code changes. |
| **Non-ASCII filenames** | Raw names in S3 metadata / content-disposition can break uploads or downloads for some characters. | Metadata `file_name` is ASCII-normalized; presigned `get_object` responses use RFC 5987 `filename*` for `ResponseContentDisposition` so original Unicode names round-trip in browsers. |
| **`generate_file` (signed URL)** | Any authenticated caller could request a presigned URL if they knew or guessed the object key (key stored in **File** `content_hash`). | Resolves the **File** row by `content_hash`, runs **`check_permission('read')`** on that **File**, then redirects; missing row raises **Does Not Exist** (404). |
| **Quality / CI** | Minimal upstream tooling. | Ruff, pre-commit, Semgrep (frappe rules), GitHub Actions (commitlint, linter workflow). |

**Git anchors** (rebases and whitespace-heavy diffs):

- ALYF CI baseline: `2274ea1` — `chore(ci): adopt ALYF baseline configuration`
- One-time format pass (compare semantics after this point): `b595155` — `chore: apply ruff format and lint to upstream baseline`

```bash
git diff b595155..HEAD -- path/to/file.py
```

**Package version** follows pre-release tags during development (`0.0.x`); see releases for the mapping to merged work.

#### Known limitations

These match upstream unless noted; they are candidates for follow-up hardening, not regressions introduced only in this fork:

- **S3 File Attachment** credential fields are still plain **Data** fields in the stock DocType; prefer tightening secret handling in a dedicated change.
- **`file_upload_to_s3`** remains whitelisted like upstream; it is intended as a document hook, not a public API.
- **`content_hash`** stores the S3 object key for uploaded files, which can interact with core **File** validation and future Frappe versions; plan a dedicated field or migration if you rely on strict content-hash semantics.

#### Configuration Setup

1. Open single doctype "s3 File Attachment"
2. Enter bucket name, access key, secret key, region, optional S3 endpoint URL, and folder name
    Folder Name- folder name is the default folder path in s3.
3. Migrate existing files lets all the existing files in private and public folders
    to be migrated to s3.
4. Delete From Cloud when selected deletes the file form s3 bucket whenever a file
    is deleted from ui. By default the Delete from cloud will be unchecked.

#### License

MIT
