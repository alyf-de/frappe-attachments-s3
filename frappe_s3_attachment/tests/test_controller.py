import re
import urllib.parse
from functools import partial
from unittest.mock import MagicMock, call, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from rq.timeouts import JobTimeoutException

from frappe_s3_attachment import controller

_REAL_FRAPPE_GET_DOC = frappe.get_doc
DEFAULT_MIGRATE_JOB_TIMEOUT = 1500


def _filetype_kind(mime):
	return MagicMock(mime=mime)


def _make_settings(**overrides):
	secret_password = overrides.pop("_secret_password", "test-secret-key")
	merged = {
		"access_key": "test-access-key",
		"endpoint_url": "",
		"region_name": "fsn1",
		"bucket_name": "test-bucket",
		"public_bucket_name": "",
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

	def test_run_migrate_existing_files_continues_after_failed_upload(self):
		rows = [
			{"name": "F-FAIL", "file_url": "/files/logo.png"},
			{"name": "F-OK", "file_url": "/files/ok.txt"},
		]
		rows.insert(1, {"name": "F-FAIL-2", "file_url": "/files/other.png"})
		docs = {name: MagicMock(name=name) for name in ("F-FAIL", "F-FAIL-2", "F-OK")}
		for name, doc in docs.items():
			doc.name = name

		def upload(doc, _method):
			if doc.name.startswith("F-FAIL"):
				raise ConnectionError("upload rejected")

		with patch("frappe_s3_attachment.controller.frappe.get_all", return_value=rows):
			with patch(
				"frappe_s3_attachment.controller.frappe.get_doc",
				side_effect=lambda _doctype, name: docs[name],
			):
				with patch(
					"frappe_s3_attachment.controller.file_upload_to_s3", side_effect=upload
				) as upload_fn:
					db = MagicMock()
					with patch("frappe_s3_attachment.controller.frappe.db.rollback", db.rollback):
						with patch("frappe_s3_attachment.controller.frappe.db.commit", db.commit):
							with patch("frappe_s3_attachment.controller.frappe.log_error", db.log_error):
								controller.run_migrate_existing_files()

		self.assertEqual(upload_fn.call_count, 3)
		# Each log is committed before the next failure rolls back.
		self.assertEqual(
			[call[0] for call in db.method_calls],
			["rollback", "log_error", "commit", "rollback", "log_error", "commit"],
		)
		self.assertEqual(
			[call.kwargs["reference_name"] for call in db.log_error.call_args_list],
			["F-FAIL", "F-FAIL-2"],
		)

	def test_run_migrate_existing_files_stops_on_job_timeout(self):
		rows = [
			{"name": "F-SLOW", "file_url": "/files/slow.bin"},
			{"name": "F-NEXT", "file_url": "/files/next.txt"},
		]
		doc = MagicMock()
		with patch("frappe_s3_attachment.controller.frappe.get_all", return_value=rows):
			with patch("frappe_s3_attachment.controller.frappe.get_doc", return_value=doc):
				with patch(
					"frappe_s3_attachment.controller.file_upload_to_s3",
					side_effect=JobTimeoutException("timed out"),
				) as upload_fn:
					with self.assertRaises(JobTimeoutException):
						controller.run_migrate_existing_files()

		upload_fn.assert_called_once()

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

	def test_migrate_existing_files_timeout_defaults_when_falsy(self):
		job = MagicMock(id="test.localhost::frappe_s3_attachment.migrate_existing_files")
		for falsy in (0, None):
			with self.subTest(falsy=falsy):
				with patch("frappe_s3_attachment.controller.frappe.db.get_single_value", return_value=falsy):
					with patch("frappe_s3_attachment.controller.enqueue", return_value=job) as enqueue_fn:
						controller.migrate_existing_files()
				self.assertEqual(enqueue_fn.call_args.kwargs["timeout"], DEFAULT_MIGRATE_JOB_TIMEOUT)

	def test_migrate_existing_files_skips_when_job_already_enqueued(self):
		namespaced_job_id = "test.localhost::frappe_s3_attachment.migrate_existing_files"
		with patch("frappe_s3_attachment.controller.frappe.db.get_single_value", return_value=None):
			with patch("frappe_s3_attachment.controller.create_job_id", return_value=namespaced_job_id):
				with patch("frappe_s3_attachment.controller.enqueue", return_value=None) as enqueue_fn:
					result = controller.migrate_existing_files()
		enqueue_fn.assert_called_once()
		self.assertEqual(result, {"job_id": namespaced_job_id, "queued": False})

	def test_enqueue_move_file_rejects_second_job_for_same_file(self):
		with patch("frappe_s3_attachment.controller.enqueue", return_value=None) as enqueue_fn:
			with self.assertRaises(frappe.ValidationError):
				controller.enqueue_move_file("F-1", to_s3=True)
		enqueue_fn.assert_called_once()
		self.assertEqual(enqueue_fn.call_args.kwargs["job_id"], "frappe_s3_attachment.move_file::F-1")
		self.assertTrue(enqueue_fn.call_args.kwargs["deduplicate"])

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

	def test_upload_public_uses_public_bucket_without_acl(self):
		self.settings.public_bucket_name = "public-bucket"
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("image/png"),
		):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key(
				"/tmp/logo.png", "logo.png", 0, "Navbar Settings", "Navbar Settings"
			)

		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.args[1], "public-bucket")
		self.assertNotIn("ACL", call.kwargs["ExtraArgs"])

	def test_upload_private_ignores_public_bucket(self):
		self.settings.public_bucket_name = "public-bucket"
		with patch(
			"frappe_s3_attachment.controller.filetype.guess",
			return_value=_filetype_kind("application/pdf"),
		):
			s3 = controller.S3Operations()
			s3.upload_files_to_s3_with_key("/tmp/private.pdf", "private.pdf", 1, "File", "FILE-0001")

		call = self.mock_s3_client.upload_file.call_args
		self.assertEqual(call.args[1], "test-bucket")

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
		s3.delete_from_s3("key-1", "test-bucket")
		self.mock_s3_client.delete_object.assert_not_called()

	def test_delete_from_s3_calls_delete_object_when_flag_enabled(self):
		self.settings.delete_file_from_cloud = 1
		s3 = controller.S3Operations()
		s3.delete_from_s3("key-1", "test-bucket")
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
		mock_s3_ops.get_file_url.return_value = "https://s3.local/test-bucket/shop/path/logo.png"
		with (
			patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops),
			patch("frappe_s3_attachment.controller.os.remove"),
			patch("frappe_s3_attachment.controller.frappe.db.commit"),
			patch("frappe_s3_attachment.controller.frappe.db.set_value") as set_value,
			patch(
				"frappe_s3_attachment.controller.frappe.get_meta",
				return_value={"image_field": "brand_image"},
			),
		):
			controller.file_upload_to_s3(doc, "after_insert")

		self.assertEqual(doc.file_url, "https://s3.local/test-bucket/shop/path/logo.png")
		self.assertEqual(doc.s3_object_key, "shop/path/logo.png")
		self.assertIsNone(doc.content_hash)
		set_value.assert_has_calls(
			[
				call(
					"File",
					"FILE-TEST-2",
					{"file_url": doc.file_url, "s3_object_key": "shop/path/logo.png", "content_hash": None},
					update_modified=False,
				),
				call("Website Settings", "Website Settings", "brand_image", doc.file_url),
			]
		)

	def test_file_upload_to_s3_encodes_private_url(self):
		doc = frappe._dict(
			{
				"name": "FILE-TEST-ENCODE",
				"file_url": "/private/files/a&b #1?+.pdf",
				"attached_to_doctype": None,
				"attached_to_name": None,
				"file_name": "a&b #1?+.pdf",
				"is_private": 1,
			}
		)
		key = "2026/05/08/Sales Invoice/AAAAAAAA_ab_1.pdf"
		mock_s3_ops = MagicMock()
		mock_s3_ops.is_ignored_doctype.return_value = False
		mock_s3_ops.upload_files_to_s3_with_key.return_value = key
		# The real URL builder, because this test checks its encoding.
		mock_s3_ops.get_file_url = partial(controller.S3Operations.get_file_url, mock_s3_ops)
		with (
			patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops),
			patch("frappe_s3_attachment.controller.os.remove"),
			patch("frappe_s3_attachment.controller.frappe.db.commit"),
			patch("frappe_s3_attachment.controller.frappe.db.set_value"),
		):
			controller.file_upload_to_s3(doc, "after_insert")

		parsed_url = urllib.parse.urlparse(doc.file_url)
		self.assertEqual(parsed_url.path, "/api/method/frappe_s3_attachment.controller.generate_file")
		self.assertEqual(
			urllib.parse.parse_qs(parsed_url.query),
			{"key": [key], "file_name": ["a&b #1?+.pdf"]},
		)

	def test_move_to_s3_keeps_other_parent_image(self):
		doc = frappe._dict(
			{
				"name": "FILE-TEST-3",
				"file_url": "/files/old.png",
				"attached_to_doctype": "Website Settings",
				"attached_to_name": "Website Settings",
				"file_name": "old.png",
				"is_private": 0,
			}
		)
		mock_s3_ops = MagicMock()
		mock_s3_ops.is_ignored_doctype.return_value = False
		mock_s3_ops.upload_files_to_s3_with_key.return_value = "shop/path/old.png"
		mock_s3_ops.S3_CLIENT.meta.endpoint_url = "https://s3.local"
		mock_s3_ops.get_bucket.return_value = "test-bucket"
		with (
			patch("frappe_s3_attachment.controller.S3Operations", return_value=mock_s3_ops),
			patch("frappe_s3_attachment.controller.os.remove"),
			patch("frappe_s3_attachment.controller.frappe.db.commit"),
			patch("frappe_s3_attachment.controller.frappe.db.set_value") as set_value,
			patch("frappe_s3_attachment.controller.frappe.db.get_value", return_value="/files/current.png"),
			patch(
				"frappe_s3_attachment.controller.frappe.get_meta",
				return_value={"image_field": "brand_image"},
			),
		):
			controller.file_upload_to_s3(doc, "move_to_s3")

		parent_calls = [c for c in set_value.call_args_list if c.args[0] == "Website Settings"]
		self.assertEqual(parent_calls, [])

	def test_delete_from_cloud_uses_s3_object_key(self):
		self.settings.delete_file_from_cloud = 1
		doc = frappe._dict(
			{
				"s3_object_key": "shop/path/logo.png",
				"file_url": "https://s3.local/test-bucket/shop/path/logo.png",
				"content_hash": "real-sha-256",
			}
		)
		controller.delete_from_cloud(doc, "on_trash")
		frappe.db.after_commit.run()
		self.mock_s3_client.delete_object.assert_called_once_with(
			Bucket="test-bucket", Key="shop/path/logo.png"
		)

	def test_delete_from_cloud_uses_bucket_from_file_url(self):
		self.settings.delete_file_from_cloud = 1
		self.settings.public_bucket_name = "public-bucket"
		cases = (
			("https://s3.local/public-bucket/2026/logo.png", "public-bucket"),
			("https://s3.local/test-bucket/2026/logo.png", "test-bucket"),
			("/api/method/frappe_s3_attachment.controller.generate_file?key=2026/logo.png", "test-bucket"),
		)
		for file_url, bucket in cases:
			with self.subTest(file_url=file_url):
				self.mock_s3_client.reset_mock()
				doc = frappe._dict({"s3_object_key": "2026/logo.png", "file_url": file_url})
				controller.delete_from_cloud(doc, "on_trash")
				frappe.db.after_commit.run()
				self.mock_s3_client.delete_object.assert_called_once_with(Bucket=bucket, Key="2026/logo.png")

	def test_delete_from_cloud_keeps_bucket_after_public_bucket_setting_changes(self):
		self.settings.delete_file_from_cloud = 1
		file_url = "https://s3.local/old-public-bucket/2026/logo.png"
		for public_bucket_name in ("", "new-public-bucket"):
			with self.subTest(public_bucket_name=public_bucket_name):
				self.settings.public_bucket_name = public_bucket_name
				self.mock_s3_client.reset_mock()
				doc = frappe._dict({"s3_object_key": "2026/logo.png", "file_url": file_url})
				controller.delete_from_cloud(doc, "on_trash")
				frappe.db.after_commit.run()
				self.mock_s3_client.delete_object.assert_called_once_with(
					Bucket="old-public-bucket", Key="2026/logo.png"
				)

	def test_delete_from_cloud_uses_main_bucket_for_foreign_endpoint(self):
		self.settings.delete_file_from_cloud = 1
		doc = frappe._dict(
			{"s3_object_key": "2026/logo.png", "file_url": "https://other.example/other-bucket/2026/logo.png"}
		)
		controller.delete_from_cloud(doc, "on_trash")
		frappe.db.after_commit.run()
		self.mock_s3_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="2026/logo.png")

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
			return MagicMock(check_permission=MagicMock(), file_name="x.pdf")

		with patch("frappe_s3_attachment.controller.frappe.get_all") as get_all:
			get_all.return_value = ["FILE-LOOKUP-1"]
			with (
				patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=selective_get_doc),
				patch("frappe_s3_attachment.controller.make_access_log"),
			):
				frappe.local.response = frappe._dict()
				self.mock_s3_client.generate_presigned_url.return_value = "https://signed.example/x"
				controller.generate_file(key="some/s3/key", file_name="x.pdf")

		filters = get_all.call_args.kwargs["filters"]
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

	def _s3_file(self, file_url):
		return controller.S3File(
			{"doctype": "File", "file_name": "a.txt", "file_url": file_url, "s3_object_key": "2026/a.txt"}
		)

	def test_get_content_of_private_file_reads_main_bucket(self):
		self.mock_s3_client.get_object.return_value = {
			"Body": MagicMock(read=MagicMock(return_value=b"hello"))
		}
		file_doc = self._s3_file(
			"/api/method/frappe_s3_attachment.controller.generate_file?key=2026%2Fa.txt&file_name=a.txt"
		)

		self.assertEqual(file_doc.get_content(), "hello")
		self.mock_s3_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="2026/a.txt")

	def test_get_content_of_public_file_reads_bucket_from_url(self):
		self.settings.public_bucket_name = "public-bucket"
		self.mock_s3_client.get_object.return_value = {
			"Body": MagicMock(read=MagicMock(return_value=b"hello"))
		}
		file_doc = self._s3_file("https://s3.local/public-bucket/2026/a.txt")

		self.assertEqual(file_doc.get_content(), "hello")
		self.mock_s3_client.get_object.assert_called_once_with(Bucket="public-bucket", Key="2026/a.txt")

	def test_get_content_keeps_binary_content_as_bytes(self):
		self.mock_s3_client.get_object.return_value = {
			"Body": MagicMock(read=MagicMock(return_value=b"\x89PNG\xff"))
		}
		file_doc = self._s3_file("https://s3.local/test-bucket/2026/a.txt")

		self.assertEqual(file_doc.get_content(), b"\x89PNG\xff")

	def test_get_content_of_local_file_uses_core(self):
		file_doc = controller.S3File({"doctype": "File", "file_name": "a.txt", "content": "local"})

		self.assertEqual(file_doc.get_content(), "local")
		self.mock_s3_client.get_object.assert_not_called()


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

		controller.S3Operations().S3_CLIENT

		_, kwargs = controller.boto3.client.call_args
		self.assertEqual(kwargs.get("endpoint_url"), "https://fsn1.your-objectstorage.com")
		self.assertEqual(kwargs["config"].s3.get("addressing_style"), "path")

	def test_endpoint_url_omitted_when_blank(self):
		self.settings.endpoint_url = ""

		controller.S3Operations().S3_CLIENT

		_, kwargs = controller.boto3.client.call_args
		self.assertNotIn("endpoint_url", kwargs)
		self.assertEqual(kwargs["config"].s3, None)


class TestCredentials(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.patch_boto3_client = patch("frappe_s3_attachment.controller.boto3.client")
		self.patch_boto3_client.start()
		self.addCleanup(self.patch_boto3_client.stop)

	def _make_client(self, settings):
		with patch("frappe_s3_attachment.controller.frappe.get_doc", return_value=settings):
			return controller.S3Operations().S3_CLIENT

	def test_keys_from_settings(self):
		self._make_client(_make_settings())

		_, kwargs = controller.boto3.client.call_args
		self.assertEqual(kwargs["aws_access_key_id"], "test-access-key")
		self.assertEqual(kwargs["aws_secret_access_key"], "test-secret-key")

	def test_missing_keys_do_not_use_default_credentials(self):
		settings = _make_settings(access_key="", _secret_password="")

		with patch.dict(frappe.local.conf, {"s3_use_default_credentials": 0}):
			with self.assertRaises(frappe.ValidationError):
				self._make_client(settings)
		controller.boto3.client.assert_not_called()

	def test_missing_keys_use_default_credentials_if_site_config_opts_in(self):
		settings = _make_settings(access_key="", _secret_password="")

		with patch.dict(frappe.local.conf, {"s3_use_default_credentials": 1}):
			self._make_client(settings)

		_, kwargs = controller.boto3.client.call_args
		self.assertNotIn("aws_access_key_id", kwargs)
		self.assertNotIn("aws_secret_access_key", kwargs)

	def test_missing_keys_keep_ignored_attachments_local(self):
		settings = _make_settings(
			access_key="",
			_secret_password="",
			ignored_doctypes=[frappe._dict({"doctype_name": "Data Import"})],
		)
		doc = frappe._dict(
			{
				"file_url": "/private/files/import.csv",
				"attached_to_doctype": "Data Import",
				"attached_to_name": "DI-1",
				"file_name": "import.csv",
				"is_private": 1,
			}
		)

		with (
			patch.dict(frappe.local.conf, {"s3_use_default_credentials": 0}),
			patch("frappe_s3_attachment.controller.frappe.get_doc", return_value=settings),
		):
			controller.file_upload_to_s3(doc, "after_insert")

		self.assertEqual(doc.file_url, "/private/files/import.csv")
		controller.boto3.client.assert_not_called()


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
			r"^inline; filename\*=UTF-8''.+$",
		)
		encoded = disposition.split("''", 1)[1]
		self.assertEqual(urllib.parse.unquote(encoded), "Pflanzenrückgabe.pdf")

	def test_content_disposition_forces_download_for_unsafe_extensions(self):
		self.mock_s3_client.generate_presigned_url.return_value = "https://signed.example/presigned"
		s3 = controller.S3Operations()
		s3.get_url("2026/05/06/File/AB12CD34_logo.SVG", "logo.SVG")

		params = self.mock_s3_client.generate_presigned_url.call_args.kwargs["Params"]
		self.assertTrue(params["ResponseContentDisposition"].startswith("attachment; "))


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
		frappe.flags.error_message = None
		frappe.local.message_log = []
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			with self.assertRaises(frappe.PermissionError):
				controller.generate_file(key="deny-key-perm-test", file_name="secret.txt")

		# The response must not name the File, or it differs from the response for an unknown key.
		self.assertIsNone(frappe.flags.error_message)
		self.assertEqual(frappe.local.message_log, [])

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

	def test_generate_file_disposition_ignores_requested_file_name(self):
		frappe.set_user("Administrator")
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "logo.svg",
				"content": "<svg/>",
				"is_private": 1,
			}
		).insert()
		frappe.db.set_value("File", f.name, "s3_object_key", "svg-key-perm-test")

		frappe.local.response = frappe._dict()
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			controller.generate_file(key="svg-key-perm-test", file_name="preview.pdf")

		params = self.mock_s3_client.generate_presigned_url.call_args.kwargs["Params"]
		self.assertEqual(params["ResponseContentDisposition"], "attachment; filename*=UTF-8''logo.svg")

	def test_generate_file_logs_access(self):
		frappe.set_user("Administrator")
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "report.txt",
				"content": "z",
				"is_private": 1,
			}
		).insert()
		frappe.db.set_value("File", f.name, "s3_object_key", "log-key-perm-test")

		frappe.local.response = frappe._dict()
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			controller.generate_file(key="log-key-perm-test")

		self.assertTrue(
			frappe.db.exists(
				"Access Log",
				{
					"user": "Administrator",
					"export_from": "File",
					"reference_document": f.name,
					"file_type": "txt",
				},
			)
		)

	def test_generate_file_missing_key_raises_permission_error(self):
		"""An unknown key must look the same as a denied key, so callers cannot probe which keys exist."""
		frappe.set_user("Administrator")
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			with self.assertRaises(frappe.PermissionError):
				controller.generate_file(key="no-such-file-row-key-zzz")

	def test_generate_file_denies_when_other_file_with_key_is_not_readable(self):
		"""A row with a forged key must not give access to the object of another row."""
		self._ensure_perm_test_user()
		frappe.set_user("Administrator")
		secret = frappe.get_doc(
			{"doctype": "File", "file_name": "victim.txt", "content": "victim-content", "is_private": 1}
		).insert()
		frappe.db.set_value("File", secret.name, "s3_object_key", "forged-key-perm-test")
		forged = frappe.get_doc(
			{"doctype": "File", "file_name": "forged.txt", "content": "forged-content", "is_private": 1}
		).insert()
		# Rows saved before the validate check can have a forged key.
		frappe.db.set_value(
			"File", forged.name, {"s3_object_key": "forged-key-perm-test", "owner": _PERM_TEST_USER}
		)

		frappe.set_user(_PERM_TEST_USER)
		with patch("frappe_s3_attachment.controller.frappe.get_doc", side_effect=self._selective_get_doc):
			with self.assertRaises(frappe.PermissionError):
				controller.generate_file(key="forged-key-perm-test")


class TestProtectS3Fields(FrappeTestCase):
	"""Users cannot point a File at another S3 object. Inserts without a local file do not upload."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def test_insert_drops_s3_object_key(self):
		file_doc = frappe.get_doc(
			{"doctype": "File", "file_url": "https://example.com/remote.png", "s3_object_key": "other/key"}
		).insert()

		self.assertFalse(frappe.db.get_value("File", file_doc.name, "s3_object_key"))

	def test_insert_copy_of_s3_file(self):
		file_url = "/api/method/frappe_s3_attachment.controller.generate_file?key=copy/a.txt&file_name=a.txt"
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "a.txt",
				"file_url": file_url,
				"is_private": 1,
				"s3_object_key": "copy/a.txt",
			}
		).insert()

		self.assertEqual(frappe.db.get_value("File", file_doc.name, "file_url"), file_url)
		self.assertFalse(frappe.db.get_value("File", file_doc.name, "s3_object_key"))

	def _insert_private_s3_original(self, key, file_name):
		"""Insert a File the way the upload leaves it: with an encoded URL and the key, set without hooks."""
		file_url = "/api/method/frappe_s3_attachment.controller.generate_file?" + urllib.parse.urlencode(
			{"key": key, "file_name": file_name}
		)
		original = frappe.get_doc(
			{"doctype": "File", "file_name": file_name, "file_url": file_url, "is_private": 1}
		).insert()
		frappe.db.set_value(
			"File", original.name, {"file_url": file_url, "s3_object_key": key, "is_private": 1}
		)
		return file_url

	def test_get_content_of_copy_reads_key_of_original(self):
		# Core copies attachments with only file_url, e.g. for Communication emails, and unquotes it on insert.
		file_url = self._insert_private_s3_original("R&D/2026/b+c.txt", "my b & c.txt")
		copy = frappe.get_doc(
			{"doctype": "File", "file_name": "my b & c.txt", "file_url": file_url, "is_private": 1}
		).insert()
		copy = frappe.get_doc("File", copy.name)
		self.assertIsInstance(copy, controller.S3File)  # override_doctype_class hook is active
		self.assertEqual(copy.file_url, file_url)
		self.assertEqual(copy.is_private, 1)
		self.assertFalse(copy.s3_object_key)

		with patch("frappe_s3_attachment.controller.S3Operations") as s3_operations:
			s3 = s3_operations.return_value
			s3.get_bucket_of_file.return_value = "test-bucket"
			s3.S3_CLIENT.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"hello"))}
			self.assertEqual(copy.get_content(), "hello")

		s3.S3_CLIENT.get_object.assert_called_once_with(Bucket="test-bucket", Key="R&D/2026/b+c.txt")

	def test_insert_copy_of_private_s3_file_checks_access_to_original(self):
		file_url = self._insert_private_s3_original("copy/2026/secret.txt", "secret.txt")
		if not frappe.db.exists("User", _PERM_TEST_USER):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": _PERM_TEST_USER,
					"first_name": "S3 Perm",
					"send_welcome_email": 0,
				}
			).insert()
			user.add_roles("Desk User")

		frappe.set_user(_PERM_TEST_USER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{"doctype": "File", "file_name": "secret.txt", "file_url": file_url, "is_private": 1}
			).insert()

	def test_insert_folder(self):
		folder = frappe.get_doc(
			{"doctype": "File", "file_name": "S3 Test Folder", "is_folder": 1, "folder": "Home"}
		).insert()

		self.assertTrue(frappe.db.exists("File", folder.name))

	def test_save_rejects_changed_s3_object_key(self):
		file_doc = frappe.get_doc({"doctype": "File", "file_url": "https://example.com/key.png"}).insert()
		file_doc.s3_object_key = "other/key"

		with self.assertRaises(frappe.PermissionError):
			file_doc.save()

	def test_save_rejects_changed_file_url_of_s3_file(self):
		file_doc = frappe.get_doc(
			{"doctype": "File", "file_url": "https://s3.example/bucket/key.png"}
		).insert()
		frappe.db.set_value("File", file_doc.name, "s3_object_key", "key.png")
		file_doc.reload()
		file_doc.save()  # unchanged S3 fields are fine

		file_doc.file_url = "https://s3.example/other-bucket/key.png"
		with self.assertRaises(frappe.PermissionError):
			file_doc.save()


class TestPrivateDuplicateContentHashRegression(FrappeTestCase):
	"""Regression for alyf-de/frappe-attachments-s3#12.

	Core ``save_file`` duplicate detection matches a prior S3-backed row by
	``content_hash`` and calls ``exists_on_disk`` on its ``/api/method/...`` URL.
	Frappe v15.108.0+ accepts that URL in ``is_safe_path``, so this must not raise.

	The upload hook clears ``content_hash``, but other code can leave it set on an
	S3-backed row. This test sets up such a row (hook patched) and asserts a
	second private upload with identical bytes succeeds and gets its own file.
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

		# An S3-backed row that still has its content_hash.
		frappe.db.set_value(
			"File",
			first.name,
			{
				"file_url": self._PRIVATE_API_URL,
				"s3_object_key": "fake/2026/05/08/File/AAAAAAAA_seed.txt",
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

		self.assertTrue(second.content_hash)
		self.assertEqual(frappe.db.get_value("File", first.name, "content_hash"), second.content_hash)
		self.assertTrue(second.file_url.startswith("/private/files/"))
		self.assertTrue(second.exists_on_disk())
