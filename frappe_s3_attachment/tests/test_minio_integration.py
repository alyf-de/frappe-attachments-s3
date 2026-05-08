import os
import urllib.parse
from unittest import SkipTest
from unittest.mock import patch

import boto3
import frappe
from botocore.client import Config
from botocore.exceptions import ClientError
from frappe.tests.utils import FrappeTestCase

from frappe_s3_attachment import controller

_REAL_FRAPPE_GET_DOC = frappe.get_doc


class TestMinioIntegration(FrappeTestCase):
	"""Integration tests against a real MinIO endpoint."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if os.environ.get("RUN_MINIO_INTEGRATION_TESTS") != "1":
			raise SkipTest("set RUN_MINIO_INTEGRATION_TESTS=1 to run MinIO integration tests")

		cls.endpoint_url = os.environ.get("FRAPPE_S3_ATTACHMENT_MINIO_ENDPOINT", "http://127.0.0.1:9000")
		cls.access_key = os.environ.get("FRAPPE_S3_ATTACHMENT_MINIO_ACCESS_KEY", "minioadmin")
		cls.secret_key = os.environ.get("FRAPPE_S3_ATTACHMENT_MINIO_SECRET_KEY", "minioadmin")
		cls.bucket_name = os.environ.get("FRAPPE_S3_ATTACHMENT_MINIO_BUCKET", "frappe-s3-attachment-test")
		cls.region_name = os.environ.get("FRAPPE_S3_ATTACHMENT_MINIO_REGION", "us-east-1")
		cls.minio_client = boto3.client(
			"s3",
			endpoint_url=cls.endpoint_url,
			aws_access_key_id=cls.access_key,
			aws_secret_access_key=cls.secret_key,
			region_name=cls.region_name,
			config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
		)
		try:
			cls.minio_client.list_buckets()
		except Exception as exc:  # noqa: BLE001
			raise RuntimeError(
				"minio endpoint not reachable at "
				f"{cls.endpoint_url}. Ensure MinIO is running and "
				"FRAPPE_S3_ATTACHMENT_MINIO_* env vars are correct."
			) from exc
		cls._ensure_bucket_exists()

	def setUp(self):
		super().setUp()
		self.uploaded_keys = set()
		self.settings = self._build_settings(delete_file_from_cloud=0)
		self.patch_get_doc = patch(
			"frappe_s3_attachment.controller.frappe.get_doc",
			side_effect=self._selective_get_doc,
		)
		self.patch_get_doc.start()
		self.addCleanup(self.patch_get_doc.stop)

	def tearDown(self):
		for key in self.uploaded_keys:
			try:
				self.minio_client.delete_object(Bucket=self.bucket_name, Key=key)
			except ClientError:
				pass
		super().tearDown()

	@classmethod
	def _ensure_bucket_exists(cls):
		existing_buckets = {bucket["Name"] for bucket in cls.minio_client.list_buckets().get("Buckets", [])}
		if cls.bucket_name not in existing_buckets:
			cls.minio_client.create_bucket(Bucket=cls.bucket_name)

	def _build_settings(self, delete_file_from_cloud=0):
		settings = frappe._dict(
			{
				"access_key": self.access_key,
				"endpoint_url": self.endpoint_url,
				"region_name": self.region_name,
				"bucket_name": self.bucket_name,
				"folder_name": "",
				"ignored_doctypes": [],
				"signed_url_expiry_time": 300,
				"delete_file_from_cloud": delete_file_from_cloud,
			}
		)

		def get_password(fieldname, raise_exception=False):
			if fieldname == "secret_key":
				return self.secret_key
			return ""

		settings.get_password = get_password
		return settings

	def _selective_get_doc(self, *args, **kwargs):
		if args and args[0] == "S3 File Attachment":
			return self.settings
		return _REAL_FRAPPE_GET_DOC(*args, **kwargs)

	def _create_file_doc(self, *, file_name, content, is_private):
		with patch("frappe_s3_attachment.controller.file_upload_to_s3"):
			return frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"content": content,
					"is_private": is_private,
				}
			).insert()

	def _assert_object_exists(self, key):
		self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.add(key)

	def test_minio_public_upload_stores_object_and_removes_local_file(self):
		file_doc = self._create_file_doc(file_name="minio-public.txt", content="public", is_private=0)
		local_path = frappe.utils.get_site_path("public", file_doc.file_url.lstrip("/"))
		self.assertTrue(os.path.exists(local_path))

		controller.file_upload_to_s3(file_doc, "after_insert")
		uploaded_url = file_doc.file_url
		parsed_url = urllib.parse.urlparse(uploaded_url)
		bucket_and_key = parsed_url.path.lstrip("/")
		_, key = bucket_and_key.split("/", 1)

		self._assert_object_exists(key)
		self.assertFalse(os.path.exists(local_path))
		self.assertIn(f"/{self.bucket_name}/", uploaded_url)

	def test_minio_private_upload_generates_redirect_and_object_exists(self):
		file_doc = self._create_file_doc(file_name="minio-private.txt", content="private", is_private=1)
		local_path = frappe.utils.get_site_path(file_doc.file_url.lstrip("/"))
		self.assertTrue(os.path.exists(local_path))

		controller.file_upload_to_s3(file_doc, "after_insert")
		uploaded_url = file_doc.file_url
		query = urllib.parse.parse_qs(urllib.parse.urlparse(uploaded_url).query)
		key = query["key"][0]

		self._assert_object_exists(key)
		self.assertFalse(os.path.exists(local_path))
		frappe.local.response = frappe._dict()
		controller.generate_file(key=key, file_name=file_doc.file_name)
		redirect_url = frappe.local.response["location"]
		self.assertEqual(frappe.local.response["type"], "redirect")
		self.assertIn("X-Amz-Expires=300", redirect_url)

	def test_minio_delete_from_cloud_removes_object(self):
		file_doc = self._create_file_doc(file_name="minio-delete.txt", content="delete", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		self.settings = self._build_settings(delete_file_from_cloud=1)
		key = file_doc.s3_object_key
		self._assert_object_exists(key)

		controller.delete_from_cloud(file_doc, "on_trash")

		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.discard(key)
