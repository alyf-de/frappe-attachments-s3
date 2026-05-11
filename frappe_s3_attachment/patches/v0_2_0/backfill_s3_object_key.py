"""Backfill File.s3_object_key from File.content_hash for S3-managed rows.

Earlier versions of this app stored the S3 object key in `tabFile.content_hash`,
overloading a Frappe core field whose intended use is content identity / dedupe
(see https://github.com/alyf-de/frappe-attachments-s3/issues/10).

This patch copies `content_hash -> s3_object_key` for File rows whose `file_url`
matches the upload URL shapes this app produces (public bucket URL or the
private `generate_file` endpoint), without overwriting `s3_object_key` values
that have already been set by a newer upload path.

Clears `content_hash` in the same ``set_value`` so core duplicate detection
does not match subsequent uploads (see
https://github.com/alyf-de/frappe-attachments-s3/issues/12).
"""

import frappe

from frappe_s3_attachment.install import ensure_s3_object_key_custom_field, ensure_s3_object_key_index


def execute():
	ensure_s3_object_key_custom_field()
	ensure_s3_object_key_index()

	rows = frappe.db.sql(
		"""
		SELECT name, content_hash
		FROM `tabFile`
		WHERE content_hash IS NOT NULL AND content_hash != ''
		  AND (s3_object_key IS NULL OR s3_object_key = '')
		  AND (
		    file_url LIKE 'https://%%'
		    OR file_url LIKE '/api/method/frappe_s3_attachment.controller.generate_file%%'
		  )
		""",
		as_dict=True,
	)

	for row in rows:
		frappe.db.set_value(
			"File",
			row.name,
			{
				"s3_object_key": row.content_hash,
				"content_hash": None,
			},
			update_modified=False,
		)

	if rows:
		frappe.db.commit()
