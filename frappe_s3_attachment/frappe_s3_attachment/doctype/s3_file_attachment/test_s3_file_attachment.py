# Copyright (c) 2018, Frappe and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

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
		download_mode="Redirect",
		public_bucket_name="",
		public_download_mode="Redirect",
	):
		doc = frappe._dict(
			{
				"access_key": access_key,
				"endpoint_url": endpoint_url,
				"download_mode": download_mode,
				"public_bucket_name": public_bucket_name,
				"public_download_mode": public_download_mode,
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

	def test_validate_endpoint_url_accepts_http_if_all_buckets_use_proxy_mode(self):
		"""Browsers never see the endpoint in proxy mode, and internal endpoints often use plain HTTP."""
		doc = self._make_settings_doc(endpoint_url="http://minio:9000", download_mode="Proxy")
		S3FileAttachment.validate_endpoint_url(doc)

		doc.public_bucket_name = "public"
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

		doc.public_download_mode = "Proxy"
		S3FileAttachment.validate_endpoint_url(doc)

		doc.download_mode = "Redirect"
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

	def test_validate_endpoint_url_rejects_query_and_fragment(self):
		doc = self._make_settings_doc(endpoint_url="https://fsn1.your-objectstorage.com?x=1")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)

		doc = self._make_settings_doc(endpoint_url="https://fsn1.your-objectstorage.com#frag")
		with self.assertRaises(frappe.ValidationError):
			S3FileAttachment.validate_endpoint_url(doc)


class TestDownloadModeChange(IntegrationTestCase):
	def test_mode_change_queues_rewrite_of_public_file_urls(self):
		settings = frappe.get_doc("S3 File Attachment")
		settings.update({"bucket_name": "test-bucket", "region_name": "fsn1", "download_mode": "Redirect"})
		with patch(f"{S3FileAttachment.__module__}.enqueue") as enqueue:
			settings.save()
			enqueue.reset_mock()
			settings.save()
			enqueue.assert_not_called()

			settings.download_mode = "Proxy"
			settings.save()

		enqueue.assert_called_once()
		self.assertEqual(
			enqueue.call_args.args[0], "frappe_s3_attachment.controller.rewrite_public_file_urls"
		)
