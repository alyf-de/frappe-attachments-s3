from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from frappe_s3_attachment import controller


def _make_settings(**overrides):
	secret_password = overrides.pop("_secret_password", "")
	merged = {
		"access_key": "",
		"endpoint_url": "",
		"region_name": "fsn1",
		"bucket_name": "test-bucket",
		"folder_name": "",
		"signed_url_expiry_time": 300,
		"delete_file_from_cloud": 0,
	}
	merged.update({k: v for k, v in overrides.items() if k != "_secret_password"})
	settings = frappe._dict(merged)

	def get_password(fieldname):
		if fieldname == "secret_key":
			return secret_password
		return ""

	settings.get_password = get_password
	return settings


class TestControllerCharacterization(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.settings = _make_settings()
		self.mock_s3_client = MagicMock()
		self.mock_s3_client.meta.endpoint_url = "https://s3.local"
		self.patch_get_doc = patch(
			"frappe_s3_attachment.controller.frappe.get_doc",
			return_value=self.settings,
		)
		self.patch_boto3_client = patch(
			"frappe_s3_attachment.controller.boto3.client",
			return_value=self.mock_s3_client,
		)
		self.patch_hooks = patch(
			"frappe_s3_attachment.controller.frappe.get_hooks",
			return_value={},
		)

		self.patch_get_doc.start()
		self.patch_boto3_client.start()
		self.patch_hooks.start()
		self.addCleanup(self.patch_get_doc.stop)
		self.addCleanup(self.patch_boto3_client.stop)
		self.addCleanup(self.patch_hooks.stop)

	def test_strip_special_chars_keeps_alphanumeric_dot_dash_underscore(self):
		s3 = controller.S3Operations()
		self.assertEqual(
			s3.strip_special_chars("hello.world-file_v1!@#.pdf"),
			"hello.world-file_v1.pdf",
		)

	def test_key_generator_default_format_no_folder(self):
		s3 = controller.S3Operations()
		key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertRegex(
			key,
			r"^\d{4}/\d{2}/\d{2}/Sales Invoice/[A-Z0-9]{8}_invoice\.pdf$",
		)

	def test_key_generator_with_folder_name_prefix(self):
		self.settings.folder_name = "shop1"
		s3 = controller.S3Operations()
		key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertRegex(
			key,
			r"^shop1/\d{4}/\d{2}/\d{2}/Sales Invoice/[A-Z0-9]{8}_invoice\.pdf$",
		)

	def test_key_generator_replaces_spaces_with_underscores(self):
		s3 = controller.S3Operations()
		key = s3.key_generator("my file.pdf", "Quotation", "QTN-0001")
		self.assertRegex(
			key,
			r"^\d{4}/\d{2}/\d{2}/Quotation/[A-Z0-9]{8}_my_file\.pdf$",
		)

	def test_key_generator_uses_s3_key_generator_hook(self):
		with patch(
			"frappe_s3_attachment.controller.frappe.get_hooks", return_value={"s3_key_generator": ["x.y.z"]}
		):
			with patch("frappe_s3_attachment.controller.frappe.get_attr") as get_attr:
				get_attr.return_value = lambda **kwargs: "custom/path/foo/"
				s3 = controller.S3Operations()
				key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertEqual(key, "custom/path/foo")

	def test_upload_public_sets_acl_public_read(self):
		with patch("frappe_s3_attachment.controller.magic.from_file", return_value="application/pdf"):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/invoice.pdf",
				"invoice.pdf",
				0,
				"Sales Invoice",
				"SINV-0001",
			)

		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.args[1], "test-bucket")
		self.assertEqual(call.kwargs["ExtraArgs"]["ACL"], "public-read")

	def test_upload_private_no_acl_kwarg(self):
		with patch("frappe_s3_attachment.controller.magic.from_file", return_value="application/pdf"):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/private.pdf",
				"private.pdf",
				1,
				"File",
				"FILE-0001",
			)

		call = self.mock_s3_client.upload_file.call_args
		self.assertNotIn("ACL", call.kwargs["ExtraArgs"])

	def test_upload_uses_magic_for_mime_type(self):
		with patch(
			"frappe_s3_attachment.controller.magic.from_file", return_value="application/json"
		) as mock_magic:
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/test.json",
				"test.json",
				1,
				"File",
				"FILE-0001",
			)

		mock_magic.assert_called_once_with("/tmp/test.json", mime=True)
		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.kwargs["ExtraArgs"]["ContentType"], "application/json")

	def test_delete_from_s3_no_op_when_flag_disabled(self):
		self.settings.delete_file_from_cloud = 0
		s3 = controller.S3Operations()
		s3.delete_from_s3("key-1")
		self.mock_s3_client.delete_object.assert_not_called()

	def test_delete_from_s3_calls_delete_object_when_flag_enabled(self):
		self.settings.delete_file_from_cloud = 1
		s3 = controller.S3Operations()
		s3.delete_from_s3("key-1")
		self.mock_s3_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="key-1")

	def test_file_upload_to_s3_skips_ignored_doctype(self):
		doc = frappe._dict(
			{
				"name": "FILE-TEST-1",
				"file_url": "/private/files/my.pdf",
				"attached_to_doctype": "Data Import",
				"attached_to_name": "DI-0001",
				"file_name": "my.pdf",
				"is_private": 1,
			}
		)
		mock_s3_ops = MagicMock()
		mock_s3_class = MagicMock(return_value=mock_s3_ops)
		with patch("frappe_s3_attachment.controller.S3Operations", mock_s3_class):
			controller.file_upload_to_s3(doc, "after_insert")

		mock_s3_ops.upload_files_to_s3_with_key.assert_not_called()
		self.assertEqual(doc.file_url, "/private/files/my.pdf")

	def test_file_upload_to_s3_updates_image_field_when_present(self):
		doc = frappe._dict(
			{
				"name": "FILE-TEST-2",
				"file_url": "/public/files/logo.png",
				"attached_to_doctype": "Website Settings",
				"attached_to_name": "Website Settings",
				"file_name": "logo.png",
				"is_private": 0,
			}
		)
		mock_s3_ops = MagicMock()
		mock_s3_ops.upload_files_to_s3_with_key.return_value = "shop/path/logo.png"
		mock_s3_ops.S3_CLIENT.meta.endpoint_url = "https://s3.local"
		mock_s3_ops.BUCKET = "test-bucket"
		with patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops):
			with patch("frappe_s3_attachment.controller.os.remove"):
				with patch("frappe_s3_attachment.controller.frappe.db.sql"):
					with patch("frappe_s3_attachment.controller.frappe.db.commit"):
						with patch("frappe_s3_attachment.controller.frappe.db.set_value") as set_value:
							with patch(
								"frappe_s3_attachment.controller.frappe.get_meta",
								return_value={"image_field": "brand_image"},
							):
								controller.file_upload_to_s3(doc, "after_insert")

		set_value.assert_called_once_with("Website Settings", "Website Settings", "brand_image", doc.file_url)
		self.assertEqual(doc.file_url, "https://s3.local/test-bucket/shop/path/logo.png")

	def test_s3_file_regex_match_accepts_public_and_private_urls(self):
		self.assertTrue(controller.s3_file_regex_match("https://fsn1.your-objectstorage.com/bucket/key"))
		self.assertTrue(
			controller.s3_file_regex_match(
				"/api/method/frappe_s3_attachment.controller.generate_file?key=abc&file_name=test.pdf"
			)
		)
		self.assertIsNone(controller.s3_file_regex_match("/files/plain-local-file.pdf"))


class TestEndpointUrl(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.settings = _make_settings()
		self.mock_s3_client = MagicMock()
		self.mock_s3_client.meta.endpoint_url = "https://s3.local"
		self.patch_get_doc = patch(
			"frappe_s3_attachment.controller.frappe.get_doc",
			return_value=self.settings,
		)
		self.patch_boto3_client = patch(
			"frappe_s3_attachment.controller.boto3.client",
			return_value=self.mock_s3_client,
		)
		self.patch_get_doc.start()
		self.patch_boto3_client.start()
		self.addCleanup(self.patch_get_doc.stop)
		self.addCleanup(self.patch_boto3_client.stop)

	def test_endpoint_url_threaded_when_set(self):
		self.settings.endpoint_url = "https://fsn1.your-objectstorage.com"

		controller.S3Operations()

		_, kwargs = controller.boto3.client.call_args
		self.assertEqual(kwargs.get("endpoint_url"), "https://fsn1.your-objectstorage.com")

	def test_endpoint_url_omitted_when_blank(self):
		self.settings.endpoint_url = ""

		controller.S3Operations()

		_, kwargs = controller.boto3.client.call_args
		self.assertNotIn("endpoint_url", kwargs)
