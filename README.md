<a href="https://zerodha.tech"><img src="https://zerodha.tech/static/images/github-badge.svg" align="right" /></a>

## Frappe S3 Attachment

Frappe app to make file upload automatically upload and read from s3.

#### Features.

1. Upload both public and private files to s3.
2. Stream files from S3, when file is viewed everytime.
3. Lets you add S3 credentials
    (access key, secret key, bucket name, folder name, optional endpoint URL) through ui and migrate existing
    files.
4. Deletes from s3 whenever a file is deleted in ui.
5. Files are uploaded categorically in the format.
    {s3_folder_path}/{year}/{month}/{day}/{doctype}/{file_hash}

#### Installation.

1. bench get-app https://github.com/alyf-de/frappe-attachments-s3 --branch version-15
2. bench install-app frappe_s3_attachment

#### Branches.

- `develop`: default development branch.
- `version-15`: stable branch for Frappe v15 installations.

#### Changes vs upstream.

- Fork base branch: `upstream/develop`.
- ALYF CI baseline commit: `2274ea1` (`chore(ci): adopt ALYF baseline configuration`).
- One-time formatting anchor commit: `b595155` (`chore: apply ruff format and lint to upstream baseline`).
- Use the formatting anchor to review semantic deltas cleanly:
  `git diff b595155..HEAD -- <path>`

#### Configuration Setup.

1. Open single doctype "s3 File Attachment"
2. Enter bucket name, access key, secret key, region, optional S3 endpoint URL, and folder name
    Folder Name- folder name is the default folder path in s3.
3. Migrate existing files lets all the existing files in private and public folders
    to be migrated to s3.
4. Delete From Cloud when selected deletes the file form s3 bucket whenever a file
    is deleted from ui. By default the Delete from cloud will be unchecked.

#### License

MIT
