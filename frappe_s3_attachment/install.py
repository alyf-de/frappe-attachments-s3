import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

S3_OBJECT_KEY_FIELD = "s3_object_key"
S3_OBJECT_KEY_INDEX_NAME = "s3_object_key_index"
S3_OBJECT_KEY_LENGTH = 255
# 191 keeps the index byte size safe under utf8mb4 across MariaDB/MySQL configurations.
S3_OBJECT_KEY_INDEX_PREFIX = 191


def after_install():
	ensure_default_ignored_doctype()
	ensure_s3_object_key_custom_field()
	ensure_s3_object_key_index()


def after_migrate():
	ensure_s3_object_key_custom_field()
	ensure_s3_object_key_index()


def ensure_default_ignored_doctype():
	settings = frappe.get_single("S3 File Attachment")
	configured_ignored = {row.doctype_name for row in (settings.ignored_doctypes or []) if row.doctype_name}
	if "Data Import" in configured_ignored:
		return

	settings.append("ignored_doctypes", {"doctype_name": "Data Import"})
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)


def ensure_s3_object_key_custom_field():
	"""Ensure the File DocType carries the s3_object_key locator field.

	Stored as Data with explicit length 255 so realistic key paths
	(`{folder}/YYYY/MM/DD/{doctype}/{rand}_{filename}`) fit comfortably.
	Read-only because the controller manages the value, but visible in the
	Desk so administrators can audit which S3 object backs a File row.
	"""
	create_custom_fields(
		{
			"File": [
				{
					"fieldname": S3_OBJECT_KEY_FIELD,
					"label": "S3 Object Key",
					"fieldtype": "Data",
					"length": S3_OBJECT_KEY_LENGTH,
					"insert_after": "content_hash",
					"read_only": 1,
					"hidden": 0,
					"no_copy": 1,
					"print_hide": 1,
					"description": (
						"S3 object key managed by frappe_s3_attachment. "
						"Used to resolve presigned URLs and cloud deletion."
					),
					"module": "Frappe S3 Attachment",
				}
			]
		}
	)


def ensure_s3_object_key_index():
	"""Add a fast-lookup index on File.s3_object_key.

	On MariaDB/MySQL we use a prefix index of length 191 so the index size
	stays well within utf8mb4 byte limits across DB configurations.
	On Postgres we add a plain index on the (varchar 255) column.
	"""
	if (frappe.db.db_type or "mariadb") == "postgres":
		frappe.db.add_index("File", [S3_OBJECT_KEY_FIELD], index_name=S3_OBJECT_KEY_INDEX_NAME)
	else:
		frappe.db.add_index(
			"File",
			[f"{S3_OBJECT_KEY_FIELD}({S3_OBJECT_KEY_INDEX_PREFIX})"],
			index_name=S3_OBJECT_KEY_INDEX_NAME,
		)
