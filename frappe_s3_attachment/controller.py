import datetime
import mimetypes
import os
import random
import re
import string
import unicodedata
import urllib.parse

import boto3
import filetype
import frappe
from botocore.client import Config
from botocore.exceptions import ClientError
from frappe import _
from frappe.utils import cint, get_link_to_form
from frappe.utils.background_jobs import create_job_id, enqueue

MIGRATE_EXISTING_FILES_JOB_ID = "frappe_s3_attachment.migrate_existing_files"


class S3Operations:
	def __init__(self):
		"""
		Function to initialise the S3 client from the S3 File Attachment singleton.
		"""
		self.s3_settings_doc = frappe.get_doc(
			"S3 File Attachment",
			"S3 File Attachment",
		)
		config_kwargs = {"signature_version": "s3v4"}
		client_kwargs = dict(
			region_name=self.s3_settings_doc.region_name,
		)
		endpoint_url = (self.s3_settings_doc.endpoint_url or "").strip().rstrip("/")
		# Custom S3 endpoints (for example Hetzner) are most reliable with path-style requests.
		if endpoint_url:
			client_kwargs["endpoint_url"] = endpoint_url
			config_kwargs["s3"] = {"addressing_style": "path"}

		# use credentials from the S3 File Attachment singleton, if available
		# otherwise fall back to boto3 default credential resolution
		secret = self.s3_settings_doc.get_password("secret_key", raise_exception=False)
		if self.s3_settings_doc.access_key and secret:
			client_kwargs["aws_access_key_id"] = self.s3_settings_doc.access_key
			client_kwargs["aws_secret_access_key"] = secret

		client_kwargs["config"] = Config(**config_kwargs)
		self.S3_CLIENT = boto3.client("s3", **client_kwargs)
		self.BUCKET = self.s3_settings_doc.bucket_name
		self.folder_name = self.s3_settings_doc.folder_name

	def is_ignored_doctype(self, parent_doctype: str) -> bool:
		"""Return whether *Attached To DocType* ``parent_doctype`` is listed on **S3 File Attachment**."""
		ignored = {
			row.doctype_name for row in (self.s3_settings_doc.ignored_doctypes or []) if row.doctype_name
		}
		return parent_doctype in ignored

	def strip_special_chars(self, file_name):
		"""
		Strip characters from *file_name* that do not match the allowed regex.
		"""
		regex = re.compile("[^0-9a-zA-Z._-]")
		file_name = regex.sub("", file_name)
		return file_name

	def key_generator(self, file_name, parent_doctype, parent_name):
		"""Build the S3 object key used for a new upload.

		Resolution order:

		1. **Hook** — If any app defines the ``s3_key_generator`` hook, the first dotted
		   path is loaded and called as ``callable(file_name=..., parent_doctype=...,
		   parent_name=...)``. The return value is coerced with ``frappe.cstr``
		   (so ``bytes`` are decoded as UTF-8 text, not Python's ``str(bytes)``
		   repr), leading and trailing ``/`` are stripped, and a non-empty string is
		   returned as the key. On exception, a log line is emitted with
		   ``exc_info`` and the built-in layout below is used. If the hook returns a
		   falsy value or only slashes, a **warning** is logged and the built-in layout
		   is used.
		2. **Default** — ``{folder}/{year}/{month}/{day}/{parent_doctype}/{random}_{file_name}``
		   with ``folder`` omitted when unset; spaces in ``file_name`` become ``_``;
		   the file-name tail is reduced to ``[0-9a-zA-Z._-]`` via
		   ``strip_special_chars``.

		Args:
			file_name: Original **File** *File Name* (hook receives this unchanged).
			parent_doctype: *Attached To DocType*, or ``"File"`` when the **File** is not linked to a row.
			parent_name: *Attached To Name* from the **File** row.

		Returns:
			Object key string (no leading ``/``).
		"""
		hook_cmd = frappe.get_hooks().get("s3_key_generator")
		if hook_cmd:
			hook_path = hook_cmd[0]
			try:
				hook_return = frappe.get_attr(hook_path)(
					file_name=file_name, parent_doctype=parent_doctype, parent_name=parent_name
				)
				# cstr (as_unicode) handles bytes; str(bytes) would render as b'...' literals.
				normalised = frappe.cstr(hook_return).strip("/")
				if normalised:
					return normalised
				else:
					frappe.logger("frappe_s3_attachment").warning(
						f"s3_key_generator hook {hook_path} returned no usable key after normalisation; using default key layout"
					)
			except Exception:
				frappe.logger("frappe_s3_attachment").error(
					f"s3_key_generator hook {hook_path} failed; using default key layout",
					exc_info=True,
				)

		file_name = file_name.replace(" ", "_")
		file_name = self.strip_special_chars(file_name)
		prefix = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))

		today = datetime.datetime.now()
		year = today.strftime("%Y")
		month = today.strftime("%m")
		day = today.strftime("%d")

		if self.folder_name:
			key = f"{self.folder_name}/{year}/{month}/{day}/{parent_doctype}/{prefix}_{file_name}"
		else:
			key = f"{year}/{month}/{day}/{parent_doctype}/{prefix}_{file_name}"
		return key

	def upload_files_to_s3_with_key(self, file_path, file_name, is_private, parent_doctype, parent_name):
		"""
		Uploads a new file to S3.
		Strips the file extension to set the content_type in metadata.
		"""
		key = self.key_generator(file_name, parent_doctype, parent_name)
		kind = filetype.guess(file_path)
		content_type = (kind.mime if kind else None) or mimetypes.guess_type(file_name)[0]
		content_type = content_type or "application/octet-stream"
		ascii_file_name = unicodedata.normalize("NFKD", file_name).encode("ascii", "ignore").decode("ascii")
		try:
			if is_private:
				self.S3_CLIENT.upload_file(
					file_path,
					self.BUCKET,
					key,
					ExtraArgs={
						"ContentType": content_type,
						"Metadata": {
							"ContentType": content_type,
							"file_name": ascii_file_name,
						},
					},
				)
			else:
				self.S3_CLIENT.upload_file(
					file_path,
					self.BUCKET,
					key,
					ExtraArgs={
						"ContentType": content_type,
						"ACL": "public-read",
						"Metadata": {
							"ContentType": content_type,
						},
					},
				)

		except boto3.exceptions.S3UploadFailedError:
			frappe.throw(_("File Upload Failed. Please try again."))
		return key

	def delete_from_s3(self, key):
		"""Delete file from s3"""
		if self.s3_settings_doc.delete_file_from_cloud:
			try:
				self.S3_CLIENT.delete_object(Bucket=self.s3_settings_doc.bucket_name, Key=key)
			except ClientError:
				frappe.throw(_("Access denied: Could not delete file"))

	def read_file_from_s3(self, key):
		"""
		Function to read file from a s3 file.
		"""
		return self.S3_CLIENT.get_object(Bucket=self.BUCKET, Key=key)

	def get_url(self, key, file_name=None):
		"""
		Return url.

		:param bucket: s3 bucket name
		:param key: s3 object key
		"""
		if self.s3_settings_doc.signed_url_expiry_time:
			self.signed_url_expiry_time = self.s3_settings_doc.signed_url_expiry_time  # noqa
		else:
			self.signed_url_expiry_time = 120
		params = {
			"Bucket": self.BUCKET,
			"Key": key,
		}
		if file_name:
			quoted = urllib.parse.quote(file_name)
			params["ResponseContentDisposition"] = f"attachment; filename*=UTF-8''{quoted}"

		url = self.S3_CLIENT.generate_presigned_url(
			"get_object",
			Params=params,
			ExpiresIn=self.signed_url_expiry_time,
		)

		return url


def file_upload_to_s3(doc, _method):
	"""Upload a **File** row to S3 after insert.

	``doc_events`` handler: uploads the on-disk file, removes the local copy, updates
	``file_url`` and ``s3_object_key``, clears ``content_hash``, and may update the
	parent document's ``image_field`` when the parent **DocType** defines one in Meta.
	Skips upload when *Attached To DocType* is listed as ignored on **S3 File Attachment**.

	Args:
		doc: The inserted **File** document.
		_method (str): Doc event name from Frappe's hook runner (e.g. ``"after_insert"``).
			Present for the hook calling convention only; unused.
	"""
	s3_upload = S3Operations()
	path = doc.file_url
	site_path = frappe.utils.get_site_path()
	parent_doctype = doc.attached_to_doctype or "File"
	parent_name = doc.attached_to_name
	if not s3_upload.is_ignored_doctype(parent_doctype):
		if not doc.is_private:
			file_path = site_path + "/public" + path
		else:
			file_path = site_path + path
		key = s3_upload.upload_files_to_s3_with_key(
			file_path, doc.file_name, doc.is_private, parent_doctype, parent_name
		)

		if doc.is_private:
			generate_method = "frappe_s3_attachment.controller.generate_file"
			file_url = f"""/api/method/{generate_method}?key={key}&file_name={doc.file_name}"""
		else:
			file_url = f"{s3_upload.S3_CLIENT.meta.endpoint_url}/{s3_upload.BUCKET}/{key}"

		# Change file info without triggering any hooks
		query = "UPDATE `tabFile` SET file_url=%s, s3_object_key=%s, content_hash=NULL WHERE name=%s"
		frappe.db.sql(query, (file_url, key, doc.name))

		doc.file_url = file_url
		doc.s3_object_key = key
		doc.content_hash = None

		if parent_doctype and frappe.get_meta(parent_doctype).get("image_field"):
			frappe.db.set_value(
				parent_doctype, parent_name, frappe.get_meta(parent_doctype).get("image_field"), file_url
			)

		frappe.db.commit()
		os.remove(file_path)


@frappe.whitelist()
def generate_file(key: str | None = None, file_name: str | None = None):
	"""
	Function to stream file from s3.
	"""
	if not key:
		frappe.local.response["body"] = "Key not found."
		return

	file_name_in_db = frappe.db.get_value("File", {"s3_object_key": key}, "name")
	if not file_name_in_db:
		raise frappe.DoesNotExistError(doctype="File")

	frappe.get_doc("File", file_name_in_db).check_permission("read")

	s3_upload = S3Operations()
	signed_url = s3_upload.get_url(key, file_name)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = signed_url
	return


def _s3_file_regex_match(file_url):
	"""
	Match the public file regex match.
	"""
	return re.match(r"^(https?:|/api/method/frappe_s3_attachment.controller.generate_file)", file_url)


def run_migrate_existing_files():
	"""Upload local **File** rows to S3 (background worker)."""
	files_list = frappe.get_all(
		"File",
		fields=["name", "file_url"],
		filters=[
			["file_url", "is", "set"],
			["s3_object_key", "is", "not set"],
		],
	)
	for file in files_list:
		if _s3_file_regex_match(file["file_url"]):
			# if file is already a remote file, skip
			continue
		doc = frappe.get_doc("File", file["name"])
		if doc.exists_on_disk():
			file_upload_to_s3(doc, "migrate_existing_files")


@frappe.whitelist()
def migrate_existing_files():
	"""Queue migration of local **File** records to S3 on the long worker queue."""
	job_id = MIGRATE_EXISTING_FILES_JOB_ID
	namespaced_job_id = create_job_id(job_id)
	timeout = frappe.db.get_single_value("S3 File Attachment", "timeout_for_migration_job")
	timeout = cint(timeout) or 1500

	job = enqueue(
		"frappe_s3_attachment.controller.run_migrate_existing_files",
		queue="long",
		timeout=timeout,
		job_id=job_id,
		deduplicate=True,
	)
	if job:
		frappe.msgprint(
			_(
				"Migration of local files to S3 has been queued. This may take a while for large sites. "
				"Track progress in {0}."
			).format(get_link_to_form("RQ Job", job.id)),
			indicator="blue",
			title=_("S3 Migration"),
		)
		job_id = job.id
		queued = True

	else:
		# enqueue returns None, if job is already queued or running
		frappe.msgprint(
			_("S3 migration is already queued or running. Track progress in {0}.").format(
				get_link_to_form("RQ Job", namespaced_job_id)
			),
			indicator="orange",
			title=_("S3 Migration"),
		)
		job_id = namespaced_job_id
		queued = False

	return {"job_id": job_id, "queued": queued}


def delete_from_cloud(doc, method):
	"""Delete file from s3"""
	if not doc.get("s3_object_key"):
		return
	s3 = S3Operations()
	s3.delete_from_s3(doc.s3_object_key)


@frappe.whitelist()
def ping():
	"""
	Test function to check if api function work.
	"""
	return "pong"
