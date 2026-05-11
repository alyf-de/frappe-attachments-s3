import unittest
from unittest.mock import MagicMock, patch

from frappe_s3_attachment import install
from frappe_s3_attachment.install import (
	S3_OBJECT_KEY_FIELD,
	S3_OBJECT_KEY_INDEX_NAME,
	S3_OBJECT_KEY_INDEX_PREFIX,
	S3_OBJECT_KEY_LENGTH,
	ensure_default_ignored_doctype,
	ensure_s3_object_key_custom_field,
	ensure_s3_object_key_index,
)


class TestInstall(unittest.TestCase):
	def test_ensure_default_ignored_doctype_appends_data_import_when_missing(self):
		settings = MagicMock()
		settings.ignored_doctypes = []
		with patch("frappe_s3_attachment.install.frappe.get_single", return_value=settings):
			ensure_default_ignored_doctype()

		settings.append.assert_called_once_with("ignored_doctypes", {"doctype_name": "Data Import"})
		self.assertTrue(settings.flags.ignore_mandatory)
		settings.save.assert_called_once_with(ignore_permissions=True)

	def test_ensure_default_ignored_doctype_noop_when_already_present(self):
		settings = MagicMock()
		settings.ignored_doctypes = [MagicMock(doctype_name="Data Import")]
		with patch("frappe_s3_attachment.install.frappe.get_single", return_value=settings):
			ensure_default_ignored_doctype()

		settings.append.assert_not_called()
		settings.save.assert_not_called()


class TestS3ObjectKeyCustomField(unittest.TestCase):
	def test_ensure_s3_object_key_custom_field_creates_visible_readonly_field(self):
		with patch("frappe_s3_attachment.install.create_custom_fields") as create:
			ensure_s3_object_key_custom_field()

		create.assert_called_once()
		(payload,), _ = create.call_args
		self.assertIn("File", payload)
		(field,) = payload["File"]
		self.assertEqual(field["fieldname"], S3_OBJECT_KEY_FIELD)
		self.assertEqual(field["fieldtype"], "Data")
		self.assertEqual(field["length"], S3_OBJECT_KEY_LENGTH)
		self.assertEqual(field["insert_after"], "content_hash")
		self.assertEqual(field["read_only"], 1)
		self.assertEqual(field["hidden"], 0)
		self.assertEqual(field["no_copy"], 1)
		self.assertEqual(field["module"], "Frappe S3 Attachment")


class TestS3ObjectKeyIndex(unittest.TestCase):
	def test_mariadb_uses_prefix_index(self):
		mock_db = MagicMock()
		mock_db.db_type = "mariadb"
		with patch("frappe_s3_attachment.install.frappe.db", mock_db):
			ensure_s3_object_key_index()

		mock_db.add_index.assert_called_once_with(
			"File",
			[f"{S3_OBJECT_KEY_FIELD}({S3_OBJECT_KEY_INDEX_PREFIX})"],
			index_name=S3_OBJECT_KEY_INDEX_NAME,
		)

	def test_postgres_uses_plain_index(self):
		mock_db = MagicMock()
		mock_db.db_type = "postgres"
		with patch("frappe_s3_attachment.install.frappe.db", mock_db):
			ensure_s3_object_key_index()

		mock_db.add_index.assert_called_once_with(
			"File",
			[S3_OBJECT_KEY_FIELD],
			index_name=S3_OBJECT_KEY_INDEX_NAME,
		)


class TestSetupHooks(unittest.TestCase):
	def test_after_install_chains_setup_steps(self):
		with patch("frappe_s3_attachment.install.ensure_default_ignored_doctype") as ignored:
			with patch("frappe_s3_attachment.install.ensure_s3_object_key_custom_field") as field:
				with patch("frappe_s3_attachment.install.ensure_s3_object_key_index") as index:
					install.after_install()

		ignored.assert_called_once_with()
		field.assert_called_once_with()
		index.assert_called_once_with()

	def test_after_migrate_ensures_field_and_index(self):
		with patch("frappe_s3_attachment.install.ensure_s3_object_key_custom_field") as field:
			with patch("frappe_s3_attachment.install.ensure_s3_object_key_index") as index:
				install.after_migrate()

		field.assert_called_once_with()
		index.assert_called_once_with()
