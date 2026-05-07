import unittest
from unittest.mock import MagicMock, patch

from frappe_s3_attachment.install import ensure_default_ignored_doctype


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
