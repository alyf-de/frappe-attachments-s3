import re
import urllib.parse
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint

from frappe_s3_attachment import controller

_REAL_FRAPPE_GET_DOC = frappe.get_doc
DEFAULT_MIGRATE_JOB_TIMEOUT = 1500


def _filetype_kind(mime):
	return MagicMock(mime=mime)


def _migration_job_timeout_from_settings(value):
	return cint(value) or DEFAULT_MIGRATE_JOB_TIMEOUT


def _make_settings(**overrides):
	secret_password = overrides.pop("_secret_password", "")
	merged = {
		"access_key": "",
		"endpoint_url": "",
		"region_name": "fsn1",
		"bucket_name": "test-bucket",
		"folder_name": "",
		"ignored_doctypes": [],
		"signed_url_expiry_time": 300,
		"delete_file_from_cloud": 0,
	}
	merged.update({k: v for k, v in overrides.items() if k != "_secret_password"})
	settings = frappe._dict(merged)

	def get_password(fieldname, raise_exception=False):
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

	def test_key_generator_hook_bytes_return_decoded(self):
		with patch(
			"frappe_s3_attachment.controller.frappe.get_hooks", return_value={"s3_key_generator": ["x.y.z"]}
		):
			with patch("frappe_s3_attachment.controller.frappe.get_attr") as get_attr:
				get_attr.return_value = lambda **kwargs: b"custom/path/from-bytes/"
				s3 = controller.S3Operations()
				key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertEqual(key, "custom/path/from-bytes")

	def test_key_generator_hook_exception_logs_and_falls_back(self):
		with patch(
			"frappe_s3_attachment.controller.frappe.get_hooks", return_value={"s3_key_generator": ["x.y.z"]}
		):
			with patch("frappe_s3_attachment.controller.frappe.get_attr") as get_attr:

				def _boom(**kwargs):
					raise RuntimeError("hook failed")

				get_attr.return_value = _boom
				mock_log = MagicMock()
				with patch(
					"frappe_s3_attachment.controller.frappe.logger", return_value=MagicMock(error=mock_log)
				):
					s3 = controller.S3Operations()
					key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertRegex(
			key,
			r"^\d{4}/\d{2}/\d{2}/Sales Invoice/[A-Z0-9]{8}_invoice\.pdf$",
		)
		mock_log.assert_called_once()
		call_kw = mock_log.call_args.kwargs
		self.assertTrue(call_kw.get("exc_info"))

	def test_key_generator_hook_only_slashes_falls_back(self):
		with patch(
			"frappe_s3_attachment.controller.frappe.get_hooks", return_value={"s3_key_generator": ["x.y.z"]}
		):
			with patch("frappe_s3_attachment.controller.frappe.get_attr") as get_attr:
				get_attr.return_value = lambda **kwargs: "///"
				mock_warn = MagicMock()
				with patch(
					"frappe_s3_attachment.controller.frappe.logger", return_value=MagicMock(warning=mock_warn)
				):
					s3 = controller.S3Operations()
					key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertRegex(
			key,
			r"^\d{4}/\d{2}/\d{2}/Sales Invoice/[A-Z0-9]{8}_invoice\.pdf$",
		)
		mock_warn.assert_called_once()

	def test_key_generator_hook_empty_return_logs_warning_and_falls_back(self):
		with patch(
			"frappe_s3_attachment.controller.frappe.get_hooks", return_value={"s3_key_generator": ["x.y.z"]}
		):
			with patch("frappe_s3_attachment.controller.frappe.get_attr") as get_attr:
				get_attr.return_value = lambda **kwargs: ""
				mock_warn = MagicMock()
				with patch(
					"frappe_s3_attachment.controller.frappe.logger", return_value=MagicMock(warning=mock_warn)
				):
					s3 = controller.S3Operations()
					key = s3.key_generator("invoice.pdf", "Sales Invoice", "SINV-0001")
		self.assertRegex(
			key,
			r"^\d{4}/\d{2}/\d{2}/Sales Invoice/[A-Z0-9]{8}_invoice\.pdf$",
		)
		mock_warn.assert_called_once()

	def test_is_ignored_doctype_true_for_configured_rows(self):
		self.settings.ignored_doctypes = [
			frappe._dict({"doctype_name": "Sales Invoice"}),
			frappe._dict({"doctype_name": "Data Import"}),
		]
		s3 = controller.S3Operations()
		self.assertTrue(s3.is_ignored_doctype("Sales Invoice"))
		self.assertTrue(s3.is_ignored_doctype("Data Import"))
		self.assertFalse(s3.is_ignored_doctype("Customer"))

	def test_is_ignored_doctype_false_when_child_table_empty(self):
		self.settings.ignored_doctypes = []
		s3 = controller.S3Operations()
		self.assertFalse(s3.is_ignored_doctype("Data Import"))

	def test_run_migrate_existing_files_no_rows_does_not_call_upload(self):
		with patch("frappe_s3_attachment.controller.frappe.get_all", return_value=[]):
			with patch("frappe_s3_attachment.controller.frappe.get_doc") as get_doc_fn:
				with patch("frappe_s3_attachment.controller.file_upload_to_s3") as upload_fn:
					controller.run_migrate_existing_files()
		get_doc_fn.assert_not_called()
		upload_fn.assert_not_called()

	def test_run_migrate_existing_files_skips_when_not_on_disk(self):
		rows = [{"name": "F-1", "file_url": "/files/x.txt"}]
		doc = MagicMock()
		doc.exists_on_disk.return_value = False
		with patch("frappe_s3_attachment.controller.frappe.get_all", return_value=rows):
			with patch("frappe_s3_attachment.controller.frappe.get_doc", return_value=doc) as get_doc_fn:
				with patch("frappe_s3_attachment.controller.file_upload_to_s3") as upload_fn:
					controller.run_migrate_existing_files()
		get_doc_fn.assert_called_once_with("File", "F-1")
		upload_fn.assert_not_called()
		doc.exists_on_disk.assert_called_once_with()

	def test_run_migrate_existing_files_skips_remote_http_urls(self):
		"""``http:`` / ``https:`` rows are skipped by regex before ``get_doc``; local rows upload when on disk."""
		rows = [
			{"name": "F-HTTPS", "file_url": "https://other.example/x.bin"},
			{"name": "F-HTTP", "file_url": "http://legacy.example/x.bin"},
			{"name": "F-LOCAL", "file_url": "/files/y.txt"},
		]
		local_doc = MagicMock()
		local_doc.exists_on_disk.return_value = True
		with patch("frappe_s3_attachment.controller.frappe.get_all", return_value=rows) as get_all_fn:
			with patch(
				"frappe_s3_attachment.controller.frappe.get_doc", return_value=local_doc
			) as get_doc_fn:
				with patch("frappe_s3_attachment.controller.file_upload_to_s3") as upload_fn:
					controller.run_migrate_existing_files()
		get_all_fn.assert_called_once_with(
			"File",
			fields=["name", "file_url"],
			filters=[
				["file_url", "is", "set"],
				["s3_object_key", "is", "not set"],
			],
		)
		get_doc_fn.assert_called_once_with("File", "F-LOCAL")
		upload_fn.assert_called_once_with(local_doc, "migrate_existing_files")

	def test_migrate_existing_files_enqueues_long_job(self):
		job = MagicMock(id="test.localhost::frappe_s3_attachment.migrate_existing_files")
		with patch("frappe_s3_attachment.controller.frappe.db.get_single_value", return_value=None):
			with patch("frappe_s3_attachment.controller.enqueue", return_value=job) as enqueue_fn:
				result = controller.migrate_existing_files()
		enqueue_fn.assert_called_once_with(
			"frappe_s3_attachment.controller.run_migrate_existing_files",
			queue="long",
			timeout=DEFAULT_MIGRATE_JOB_TIMEOUT,
			job_id=controller.MIGRATE_EXISTING_FILES_JOB_ID,
			deduplicate=True,
		)
		self.assertEqual(result, {"job_id": job.id, "queued": True})

	def test_migrate_existing_files_uses_settings_timeout(self):
		job = MagicMock(id="test.localhost::frappe_s3_attachment.migrate_existing_files")
		with patch("frappe_s3_attachment.controller.frappe.db.get_single_value", return_value=3600):
			with patch("frappe_s3_attachment.controller.enqueue", return_value=job) as enqueue_fn:
				controller.migrate_existing_files()
		self.assertEqual(enqueue_fn.call_args.kwargs["timeout"], 3600)

	def test_migration_job_timeout_defaults_when_falsy(self):
		self.assertEqual(_migration_job_timeout_from_settings(0), DEFAULT_MIGRATE_JOB_TIMEOUT)
		self.assertEqual(_migration_job_timeout_from_settings(None), DEFAULT_MIGRATE_JOB_TIMEOUT)

	def test_migrate_existing_files_skips_when_job_already_enqueued(self):
		namespaced_job_id = "test.localhost::frappe_s3_attachment.migrate_existing_files"
		with patch("frappe_s3_attachment.controller.frappe.db.get_single_value", return_value=None):
			with patch("frappe_s3_attachment.controller.create_job_id", return_value=namespaced_job_id):
				with patch("frappe_s3_attachment.controller.enqueue", return_value=None) as enqueue_fn:
					result = controller.migrate_existing_files()
		enqueue_fn.assert_called_once()
		self.assertEqual(result, {"job_id": namespaced_job_id, "queued": False})

	def test_upload_public_sets_acl_public_read(self):
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("application/pdf"),
		):
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
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("application/pdf"),
		):
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

	def test_upload_uses_filetype_for_mime_type(self):
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("application/json"),
		) as mock_filetype_guess:
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/test.json",
				"test.json",
				1,
				"File",
				"FILE-0001",
			)

		mock_filetype_guess.assert_called_once_with("/tmp/test.json")
		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.kwargs["ExtraArgs"]["ContentType"], "application/json")

	def test_upload_falls_back_to_filename_mime_when_filetype_unknown(self):
		with patch("frappe_s3_attachment.controller.filetype.guess", return_value=None):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/test.csv",
				"test.csv",
				1,
				"File",
				"FILE-0001",
			)

		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.kwargs["ExtraArgs"]["ContentType"], "text/csv")

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

	def test_file_upload_to_s3_skips_upload_when_parent_doctype_is_ignored(self):
		for ignored_dt in ("Data Import", "Sales Invoice"):
			with self.subTest(ignored_doctype=ignored_dt):
				doc = frappe._dict(
					{
						"name": f"FILE-IGNORE-{ignored_dt}",
						"file_url": "/private/files/my.pdf",
						"attached_to_doctype": ignored_dt,
						"attached_to_name": "REF-1",
						"file_name": "my.pdf",
						"is_private": 1,
					}
				)
				mock_s3_ops = MagicMock()
				mock_s3_ops.is_ignored_doctype.side_effect = lambda dt: dt == ignored_dt
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
		mock_s3_ops.is_ignored_doctype.return_value = False
		mock_s3_ops.upload_files_to_s3_with_key.return_value = "shop/path/logo.png"
		mock_s3_ops.S3_CLIENT.meta.endpoint_url = "https://s3.local"
		mock_s3_ops.BUCKET = "test-bucket"
		with patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops):
			with patch("frappe_s3_attachment.controller.os.remove"):
				with patch("frappe_s3_attachment.controller.frappe.db.sql") as db_sql:
					with patch("frappe_s3_attachment.controller.frappe.db.commit"):
						with patch("frappe_s3_attachment.controller.frappe.db.set_value") as set_value:
							with patch(
								"frappe_s3_attachment.controller.frappe.get_meta",
								return_value={"image_field": "brand_image"},
							):
								controller.file_upload_to_s3(doc, "after_insert")

		set_value.assert_called_once_with("Website Settings", "Website Settings", "brand_image", doc.file_url)
		self.assertEqual(doc.file_url, "https://s3.local/test-bucket/shop/path/logo.png")
		self.assertEqual(doc.s3_object_key, "shop/path/logo.png")
		self.assertIsNone(doc.content_hash)
		update_sql, update_params = db_sql.call_args.args
		self.assertIn("s3_object_key=", update_sql)
		self.assertIn("content_hash=NULL", "".join(update_sql.split()))
		self.assertEqual(update_params[1], "shop/path/logo.png")

	def test_delete_from_cloud_uses_s3_object_key(self):
		doc = frappe._dict({"s3_object_key": "shop/path/logo.png", "content_hash": "real-sha-256"})
		mock_s3_ops = MagicMock()
		with patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops):
			controller.delete_from_cloud(doc, "on_trash")
		mock_s3_ops.delete_from_s3.assert_called_once_with("shop/path/logo.png")

	def test_delete_from_cloud_skips_when_no_s3_object_key(self):
		doc = frappe._dict({"s3_object_key": None, "content_hash": "real-sha-256"})
		mock_s3_class = MagicMock()
		with patch("frappe_s3_attachment.controller.S3Operations", mock_s3_class):
			controller.delete_from_cloud(doc, "on_trash")
		mock_s3_class.assert_not_called()

	def test_generate_file_lookup_uses_s3_object_key(self):
		"""generate_file must resolve the File row by s3_object_key, not content_hash."""
		settings = _make_settings()

		def selective_get_doc(*args, **kwargs):
			if args and args[0] == "S3 File Attachment":
				return settings
			return MagicMock(check_permission=MagicMock())

		with patch("frappe_s3_attachment.controller.frappe.db.get_value") as db_get_value:
			db_get_value.return_value = "FILE-LOOKUP-1"
			with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=selective_get_doc):
				frappe.local.response = frappe._dict()
				self.mock_s3_client.generate_presigned_url.return_value = "https://signed.example/x"
				controller.generate_file(key="some/s3/key", file_name="x.pdf")

		filters = db_get_value.call_args.args[1]
		self.assertEqual(filters, {"s3_object_key": "some/s3/key"})

	def test_s3_file_regex_match_accepts_public_and_private_urls(self):
		self.assertTrue(controller._s3_file_regex_match("https://fsn1.your-objectstorage.com/bucket/key"))
		self.assertTrue(controller._s3_file_regex_match("http://127.0.0.1:9000/bucket/key"))
		self.assertTrue(
			controller._s3_file_regex_match(
				"/api/method/frappe_s3_attachment.controller.generate_file?key=abc&file_name=test.pdf"
			)
		)
		self.assertIsNone(controller._s3_file_regex_match("/files/plain-local-file.pdf"))


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
		self.assertEqual(kwargs["config"].s3.get("addressing_style"), "path")

	def test_endpoint_url_omitted_when_blank(self):
		self.settings.endpoint_url = ""

		controller.S3Operations()

		_, kwargs = controller.boto3.client.call_args
		self.assertNotIn("endpoint_url", kwargs)
		self.assertEqual(kwargs["config"].s3, None)


class TestNonAsciiFilenames(FrappeTestCase):
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

	def test_metadata_filename_is_ascii(self):
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("application/pdf"),
		):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/Pflanzenrückgabe.pdf",
				"Pflanzenrückgabe.pdf",
				1,
				"Sales Invoice",
				"SINV-0001",
			)

		meta_name = self.mock_s3_client.upload_file.call_args.kwargs["ExtraArgs"]["Metadata"]["file_name"]
		self.assertIsNotNone(re.fullmatch(r"[\x00-\x7f]+", meta_name))
		self.assertEqual(meta_name, "Pflanzenruckgabe.pdf")

	def test_content_disposition_rfc5987(self):
		self.mock_s3_client.generate_presigned_url.return_value = "https://signed.example/presigned"
		s3 = controller.S3Operations()
		s3.get_url("2026/05/06/Sales Invoice/AB12CD34_Pflanzenrückgabe.pdf", "Pflanzenrückgabe.pdf")

		params = self.mock_s3_client.generate_presigned_url.call_args.kwargs["Params"]
		disposition = params["ResponseContentDisposition"]
		self.assertRegex(
			disposition,
			r"^attachment; filename\*=UTF-8''.+$",
		)
		encoded = disposition.split("''", 1)[1]
		self.assertEqual(urllib.parse.unquote(encoded), "Pflanzenrückgabe.pdf")


_PERM_TEST_USER = "s3_attach_perm_test@test.local"


class TestGenerateFilePermissions(FrappeTestCase):
	"""Gate generate_file behind File read permission (s3_object_key → File row)."""

	def setUp(self):
		super().setUp()
		self.settings = _make_settings()
		self.mock_s3_client = MagicMock()
		self.mock_s3_client.meta.endpoint_url = "https://s3.local"
		self.mock_s3_client.generate_presigned_url.return_value = "https://signed.example/presigned"
		self.patch_boto3_client = patch(
			"frappe_s3_attachment.controller.boto3.client",
			return_value=self.mock_s3_client,
		)
		self.patch_boto3_client.start()
		self.addCleanup(self.patch_boto3_client.stop)
		# Inserting File runs after_insert hook → file_upload_to_s3 / real S3 settings; not under test here.
		self.patch_skip_upload_hook = patch("frappe_s3_attachment.controller.file_upload_to_s3")
		self.patch_skip_upload_hook.start()
		self.addCleanup(self.patch_skip_upload_hook.stop)

	def _selective_get_doc(self, *args, **kwargs):
		if args and args[0] == "S3 File Attachment":
			return self.settings
		return _REAL_FRAPPE_GET_DOC(*args, **kwargs)

	def _ensure_perm_test_user(self):
		if not frappe.db.exists("User", _PERM_TEST_USER):
			u = frappe.new_doc("User")
			u.email = _PERM_TEST_USER
			u.first_name = "S3 Perm"
			u.send_welcome_email = 0
			u.insert()
			u.add_roles("Desk User")

	def test_generate_file_denies_unauthorized_user(self):
		frappe.set_user("Administrator")
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "secret.txt",
				"content": "x",
				"is_private": 1,
			}
		).insert()
		frappe.db.set_value("File", f.name, "s3_object_key", "deny-key-perm-test")
		self._ensure_perm_test_user()
		frappe.set_user(_PERM_TEST_USER)
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			with self.assertRaises(frappe.PermissionError):
				controller.generate_file(key="deny-key-perm-test", file_name="secret.txt")

	def test_generate_file_redirects_when_authorized(self):
		self._ensure_perm_test_user()
		frappe.set_user("Administrator")
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "mine.txt",
				"content": "y",
				"is_private": 1,
			}
		).insert()
		frappe.db.set_value("File", f.name, {"s3_object_key": "ok-key-perm-test", "owner": _PERM_TEST_USER})

		frappe.set_user(_PERM_TEST_USER)
		frappe.local.response = frappe._dict()
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			controller.generate_file(key="ok-key-perm-test", file_name="mine.txt")

		self.assertEqual(frappe.local.response["type"], "redirect")
		self.assertEqual(frappe.local.response["location"], "https://signed.example/presigned")

	def test_generate_file_missing_key_raises_does_not_exist(self):
		frappe.set_user("Administrator")
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			with self.assertRaises(frappe.DoesNotExistError):
				controller.generate_file(key="no-such-file-row-key-zzz")


class TestPrivateDuplicateContentHashRegression(FrappeTestCase):
	"""Regression for alyf-de/frappe-attachments-s3#12.

	After an S3 private upload, ``content_hash`` is cleared so core
	``save_file`` duplicate detection does not match a prior S3-backed row and
	walk ``exists_on_disk`` for a ``/api/method/...`` URL.

	This test simulates that post-upload row state (hook patched) and asserts a
	second private upload with identical bytes succeeds.
	"""

	_PRIVATE_API_URL = (
		"/api/method/frappe_s3_attachment.controller.generate_file"
		"?key=fake/2026/05/08/File/AAAAAAAA_seed.txt&file_name=seed.txt"
	)

	def setUp(self):
		super().setUp()
		# Skip the after_insert S3 upload hook so the test does not need a bucket.
		self._patch_upload_hook = patch("frappe_s3_attachment.controller.file_upload_to_s3")
		self._patch_upload_hook.start()
		self.addCleanup(self._patch_upload_hook.stop)
		self._created: list[str] = []

	def tearDown(self):
		# Bypass on_trash to avoid contacting the live bucket configured on
		# the test site (delete_from_cloud is registered as a doc_event hook
		# and resolves through frappe.get_attr at delete time, so a
		# unittest.mock patch does not always apply). The on-disk private
		# files are small text bytes and stay under sites/<site>/private/files
		# until next bench cleanup, which is acceptable for a regression
		# marker test.
		for name in self._created:
			frappe.db.delete("File", {"name": name})
		if self._created:
			frappe.db.commit()
		super().tearDown()

	def test_two_private_uploads_with_same_content_do_not_crash(self):
		content = "duplicate-private-content-#12"

		first = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "first.txt",
				"content": content,
				"is_private": 1,
			}
		).insert()
		self._created.append(first.name)

		# Simulate post-upload state: S3 hook clears content_hash (see controller).
		frappe.db.set_value(
			"File",
			first.name,
			{
				"file_url": self._PRIVATE_API_URL,
				"s3_object_key": "fake/2026/05/08/File/AAAAAAAA_seed.txt",
				"content_hash": None,
			},
			update_modified=False,
		)
		frappe.db.commit()

		second = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "second.txt",
				"content": content,
				"is_private": 1,
			}
		).insert()
		self._created.append(second.name)

		first_hash = frappe.db.get_value("File", first.name, "content_hash")
		second_hash = frappe.db.get_value("File", second.name, "content_hash")
		self.assertIsNone(first_hash)
		self.assertTrue(second_hash)
		self.assertNotEqual(first_hash, second_hash)
