"""Give copies of S3 files the `s3_object_key` of their original.

A copy is a File row with the `file_url` of an S3 file, for example a Communication
attachment. Earlier versions did not store the key on copies, so a copy stopped
working when its original was deleted, and the object was never deleted.
"""

from urllib.parse import unquote

import frappe

GENERATE_FILE_URL = "/api/method/frappe_s3_attachment.controller.generate_file?%"


def execute():
	# Copies of a public file have its exact file_url.
	public_copies = frappe.db.sql(
		"""
		SELECT copy_file.name, original.s3_object_key
		FROM `tabFile` copy_file
		JOIN `tabFile` original ON original.file_url = copy_file.file_url
		WHERE (copy_file.s3_object_key IS NULL OR copy_file.s3_object_key = '')
		  AND original.s3_object_key IS NOT NULL AND original.s3_object_key != ''
		  AND copy_file.is_private = 0 AND original.is_private = 0
		""",
		as_dict=True,
	)
	for row in public_copies:
		frappe.db.set_value("File", row.name, "s3_object_key", row.s3_object_key, update_modified=False)

	# Core unquoted the file_url of a copy of a private file and made the copy public. Do what
	# S3File.before_insert now does for a new copy: if the owner of the copy can read the original,
	# give the copy the URL of the original, its key and Is Private. Other rows stay as they are,
	# because a user could insert such a row for a file they cannot read.
	originals = {
		unquote(row.file_url): row
		for row in frappe.get_all(
			"File",
			filters={
				"is_private": 1,
				"s3_object_key": ("is", "set"),
				"file_url": ("like", GENERATE_FILE_URL),
			},
			fields=["name", "file_url", "s3_object_key"],
		)
	}
	private_copies = frappe.get_all(
		"File",
		filters={"s3_object_key": ("is", "not set"), "file_url": ("like", GENERATE_FILE_URL)},
		fields=["name", "file_url", "owner"],
	)
	for copy_file in private_copies:
		original = originals.get(unquote(copy_file.file_url))
		if original and frappe.has_permission("File", "read", original.name, user=copy_file.owner):
			frappe.db.set_value(
				"File",
				copy_file.name,
				{"file_url": original.file_url, "s3_object_key": original.s3_object_key, "is_private": 1},
				update_modified=False,
			)
