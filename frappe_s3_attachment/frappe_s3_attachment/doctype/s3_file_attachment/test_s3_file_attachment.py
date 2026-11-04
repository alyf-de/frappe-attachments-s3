# Copyright (c) 2018, Frappe and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase

from frappe_s3_attachment.frappe_s3_attachment.doctype.s3_file_attachment.s3_file_attachment import (
	S3FileAttachment,
)


class TestS3FileAttachment(UnitTestCase):
	def _make_settings_doc(
		self,
		*,
		access_key="",
		secret_key="",
		endpoint_url="",
	):
		doc = frappe._dict(
			{
				"access_key": access_key,
				"endpoint_url": endpoint_url,
			}
		)
		doc.get_password = lambda fieldname, raise_exception=False: (
			secret_key if fieldname == "secret_key" else ""
		)
		return doc

	def test_validate_credentials_requires_access_and_secret_together(self):
		doc = self._make_settings_doc(access_key="AKIA123", secret_key="")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_credentials(doc)

		doc = self._make_settings_doc(access_key="", secret_key="secret123")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_credentials(doc)

	def test_validate_credentials_accepts_both_or_neither(self):
		doc = self._make_settings_doc(access_key="", secret_key="")
		S3FileAttachment.validate_credentials(doc)

		doc = self._make_settings_doc(access_key="AKIA123", secret_key="secret123")
		S3FileAttachment.validate_credentials(doc)

	def test_validate_endpoint_url_accepts_valid_https_url(self):
		doc = self._make_settings_doc(endpoint_url="https://fsn1.your-objectstorage.com")
		S3FileAttachment.validate_endpoint_url(doc)

	def test_validate_endpoint_url_rejects_non_https_or_malformed(self):
		doc = self._make_settings_doc(endpoint_url="http://fsn1.your-objectstorage.com")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

		doc = self._make_settings_doc(endpoint_url="not-a-url")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

	def test_validate_endpoint_url_accepts_http_on_localhost(self):
		"""For local development, e.g. MinIO on the same machine as the browser."""
		for endpoint_url in ("http://localhost:9000", "http://127.0.0.1:9000", "http://[::1]:9000"):
			doc = self._make_settings_doc(endpoint_url=endpoint_url)
			S3FileAttachment.validate_endpoint_url(doc)

		doc = self._make_settings_doc(endpoint_url="http://localhost.example.com:9000")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

	def test_validate_endpoint_url_rejects_query_and_fragment(self):
		doc = self._make_settings_doc(endpoint_url="https://fsn1.your-objectstorage.com?x=1")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

		doc = self._make_settings_doc(endpoint_url="https://fsn1.your-objectstorage.com#frag")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)
