import os
import urllib.parse
from unittest import SkipTest
from unittest.mock import patch

import boto3
import frappe
from botocore.client import BaseClient, Config
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
		self.created_file_names = set()
		self.settings = self._build_settings(delete_file_from_cloud=0)
		self.patch_get_doc = patch(
			"frappe_s3_attachment.controller.frappe.get_doc",
			side_effect=self._selective_get_doc,
		)
		self.patch_get_doc.start()
		self.addCleanup(self.patch_get_doc.stop)

	def tearDown(self):
		# controller.file_upload_to_s3 commits mid-test, escaping FrappeTestCase's
		# rollback. Clean up the File rows we created so subsequent runs don't
		# collide on content_hash duplicate detection in core File.save_file.
		for name in self.created_file_names:
			try:
				frappe.delete_doc("File", name, force=True, ignore_permissions=True)
			except frappe.DoesNotExistError:
				pass
		if self.created_file_names:
			frappe.db.commit()
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
				"public_bucket_name": "",
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

	def _use_public_bucket(self):
		public_bucket = f"{self.bucket_name}-public"
		if public_bucket not in {b["Name"] for b in self.minio_client.list_buckets()["Buckets"]}:
			self.minio_client.create_bucket(Bucket=public_bucket)
		self.settings.public_bucket_name = public_bucket
		return public_bucket

	def _create_file_doc(self, *, file_name, content, is_private):
		with patch("frappe_s3_attachment.controller.file_upload_to_s3"):
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": file_name,
					"content": content,
					"is_private": is_private,
				}
			).insert()
		self.created_file_names.add(file_doc.name)
		return file_doc

	def _assert_object_exists(self, key):
		self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.add(key)

	def test_minio_public_upload_stores_object_and_removes_local_file(self):
		file_doc = self._create_file_doc(file_name="minio-public.txt", content="public", is_private=0)
		local_path = frappe.utils.get_site_path("public", file_doc.file_url.lstrip("/"))
		self.assertTrue(os.path.exists(local_path))

		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		self.assertFalse(file_doc.content_hash)
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
		file_doc.reload()
		self.assertFalse(file_doc.content_hash)
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

		frappe.delete_doc("File", file_doc.name)
		# The object stays until the deletion is committed.
		self._assert_object_exists(key)
		frappe.db.commit()

		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.discard(key)

	def test_minio_delete_from_cloud_keeps_object_on_rollback(self):
		file_doc = self._create_file_doc(
			file_name="minio-delete-rollback.txt", content="rollback", is_private=1
		)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		self.settings = self._build_settings(delete_file_from_cloud=1)
		key = file_doc.s3_object_key

		frappe.delete_doc("File", file_doc.name)
		frappe.db.rollback()
		frappe.db.commit()

		self.assertTrue(frappe.db.exists("File", file_doc.name))
		self._assert_object_exists(key)

	def test_minio_delete_from_cloud_keeps_object_used_by_other_file(self):
		file_doc = self._create_file_doc(file_name="minio-delete-shared.txt", content="shared", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		self.settings = self._build_settings(delete_file_from_cloud=1)
		key = file_doc.s3_object_key

		frappe.delete_doc("File", file_doc.name)
		# Like a replacement upload: another File gets the same key before the deletion is committed.
		other_doc = self._create_file_doc(
			file_name="minio-delete-shared-2.txt", content="other", is_private=1
		)
		other_doc.db_set("s3_object_key", key)
		frappe.db.commit()

		self._assert_object_exists(key)

	def test_minio_move_to_local_and_back_to_s3(self):
		file_doc = self._create_file_doc(file_name="minio-move.txt", content="move", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self._assert_object_exists(key)

		with patch("frappe_s3_attachment.controller.frappe.publish_progress") as publish_progress:
			controller.run_move_file(file_doc.name, to_s3=False)
		self.assertEqual(publish_progress.call_args.args[0], 100)
		file_doc.reload()
		self.assertFalse(file_doc.s3_object_key)
		self.assertTrue(file_doc.file_url.startswith("/private/files/"))
		self.assertEqual(file_doc.get_content(), "move")
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.discard(key)

		with patch("frappe_s3_attachment.controller.frappe.publish_progress") as publish_progress:
			controller.run_move_file(file_doc.name, to_s3=True)
		self.assertEqual(publish_progress.call_args.args[0], 100)
		file_doc.reload()
		self._assert_object_exists(file_doc.s3_object_key)

	def test_minio_move_to_local_keeps_object_used_by_other_file(self):
		file_doc = self._create_file_doc(file_name="minio-shared.txt", content="shared", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self._assert_object_exists(key)
		other_doc = self._create_file_doc(file_name="minio-shared-2.txt", content="other", is_private=1)
		other_doc.db_set("s3_object_key", key)

		controller.move_file_to_local(file_doc)

		self._assert_object_exists(key)

	def test_minio_move_to_local_deletes_object_when_same_key_is_in_other_bucket(self):
		file_doc = self._create_file_doc(file_name="minio-other-bucket.txt", content="mine", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self._assert_object_exists(key)
		other_doc = self._create_file_doc(file_name="minio-other-bucket-2.txt", content="other", is_private=0)
		other_doc.db_set({"s3_object_key": key, "file_url": f"{self.endpoint_url}/some-public-bucket/{key}"})

		controller.move_file_to_local(file_doc)

		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
		self.uploaded_keys.discard(key)

	def test_minio_move_to_local_succeeds_when_s3_delete_fails(self):
		file_doc = self._create_file_doc(file_name="minio-no-delete.txt", content="kept", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self._assert_object_exists(key)
		make_api_call = BaseClient._make_api_call

		def deny_delete(client, operation_name, api_params):
			if operation_name == "DeleteObject":
				raise ClientError({"Error": {"Code": "AccessDenied"}}, operation_name)
			return make_api_call(client, operation_name, api_params)

		with patch.object(BaseClient, "_make_api_call", deny_delete):
			controller.move_file_to_local(file_doc)

		file_doc.reload()
		self.assertFalse(file_doc.s3_object_key)
		self.assertEqual(file_doc.get_content(), "kept")
		self.assertTrue(frappe.db.exists("Error Log", {"reference_name": file_doc.name}))

	def test_minio_upload_keeps_local_file_used_by_other_file(self):
		local_doc = self._create_file_doc(file_name="minio-dedup.txt", content="dedup", is_private=1)
		# Frappe reuses the disk file of a File with the same content_hash.
		file_doc = self._create_file_doc(file_name="minio-dedup-2.txt", content="dedup", is_private=1)
		self.assertEqual(file_doc.file_url, local_doc.file_url)

		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()

		self._assert_object_exists(file_doc.s3_object_key)
		self.assertTrue(local_doc.exists_on_disk())
		self.assertEqual(local_doc.get_content(), "dedup")

	def test_minio_make_private_moves_object_out_of_public_bucket(self):
		public_bucket = self._use_public_bucket()
		file_doc = self._create_file_doc(file_name="minio-toggle.txt", content="toggle", is_private=0)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self.addCleanup(self.minio_client.delete_object, Bucket=public_bucket, Key=key)
		self.uploaded_keys.add(key)
		self.minio_client.head_object(Bucket=public_bucket, Key=key)

		file_doc.is_private = 1
		file_doc.save()
		frappe.db.commit()

		file_doc.reload()
		self.assertIn("controller.generate_file?key=", file_doc.file_url)
		self._assert_object_exists(key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=public_bucket, Key=key)

		file_doc.is_private = 0
		file_doc.save()
		frappe.db.commit()

		file_doc.reload()
		self.assertEqual(file_doc.file_url, f"{self.endpoint_url}/{public_bucket}/{key}")
		self.minio_client.head_object(Bucket=public_bucket, Key=key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)

	def test_minio_toggle_private_in_main_bucket_sets_acl(self):
		file_doc = self._create_file_doc(file_name="minio-toggle-acl.txt", content="acl", is_private=0)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		acl_calls = []
		make_api_call = BaseClient._make_api_call

		def record_acl(client, operation_name, api_params):
			if operation_name == "PutObjectAcl":
				# MinIO does not support object ACLs, so only record the call.
				acl_calls.append(api_params)
				return {}
			return make_api_call(client, operation_name, api_params)

		with patch.object(BaseClient, "_make_api_call", record_acl):
			file_doc.is_private = 1
			file_doc.save()
			self.assertIn("controller.generate_file?key=", file_doc.file_url)
			file_doc.is_private = 0
			file_doc.save()

		self.assertEqual(
			acl_calls,
			[
				{"Bucket": self.bucket_name, "Key": key, "ACL": "private"},
				{"Bucket": self.bucket_name, "Key": key, "ACL": "public-read"},
			],
		)
		self._assert_object_exists(key)

	def test_minio_failed_make_public_removes_public_copy(self):
		public_bucket = self._use_public_bucket()
		file_doc = self._create_file_doc(file_name="minio-toggle-fail.txt", content="fail", is_private=1)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key

		file_doc.is_private = 0
		file_doc.save()
		frappe.db.rollback()

		self._assert_object_exists(key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=public_bucket, Key=key)

	def test_minio_privacy_change_of_shared_object_is_rejected(self):
		public_bucket = self._use_public_bucket()
		file_doc = self._create_file_doc(file_name="minio-shared-toggle.txt", content="shared", is_private=0)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self.addCleanup(self.minio_client.delete_object, Bucket=public_bucket, Key=key)
		self.uploaded_keys.add(key)
		other_doc = self._create_file_doc(
			file_name="minio-shared-toggle-2.txt", content="other", is_private=0
		)
		other_doc.db_set({"s3_object_key": key, "file_url": file_doc.file_url})

		file_doc.is_private = 1
		with self.assertRaises(frappe.ValidationError):
			file_doc.save()

		self.minio_client.head_object(Bucket=public_bucket, Key=key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)

	def _toggle_twice_in_one_transaction(self):
		public_bucket = self._use_public_bucket()
		file_doc = self._create_file_doc(file_name="minio-toggle-twice.txt", content="twice", is_private=0)
		controller.file_upload_to_s3(file_doc, "after_insert")
		file_doc.reload()
		key = file_doc.s3_object_key
		self.addCleanup(self.minio_client.delete_object, Bucket=public_bucket, Key=key)
		self.uploaded_keys.add(key)
		frappe.db.commit()

		file_doc.is_private = 1
		file_doc.save()
		file_doc.is_private = 0
		file_doc.save()
		return public_bucket, key

	def test_minio_toggle_twice_and_commit_keeps_final_object(self):
		public_bucket, key = self._toggle_twice_in_one_transaction()
		frappe.db.commit()

		self.minio_client.head_object(Bucket=public_bucket, Key=key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)

	def test_minio_toggle_twice_and_rollback_keeps_first_object(self):
		public_bucket, key = self._toggle_twice_in_one_transaction()
		frappe.db.rollback()

		self.minio_client.head_object(Bucket=public_bucket, Key=key)
		with self.assertRaises(ClientError):
			self.minio_client.head_object(Bucket=self.bucket_name, Key=key)
