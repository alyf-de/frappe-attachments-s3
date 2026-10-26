import datetime
import hashlib
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
from boto3.s3.transfer import TransferConfig
from botocore.client import Config
from botocore.exceptions import ClientError
from frappe import _
from frappe.core.doctype.access_log.access_log import make_access_log
from frappe.core.doctype.file.utils import generate_file_name
from frappe.utils import cint, get_files_path, get_link_to_form
from frappe.utils.background_jobs import create_job_id, enqueue
from rq.timeouts import JobTimeoutException

try:
	from frappe.utils.response import FORCE_DOWNLOAD_EXTENSIONS
except ImportError:  # older Frappe v15 releases define this list inside send_private_file
	FORCE_DOWNLOAD_EXTENSIONS = (
		".svg",
		".svgz",
		".html",
		".htm",
		".xhtml",
		".xht",
		".shtml",
		".shtm",
		".mhtml",
		".mht",
		".xml",
		".xsl",
		".xslt",
		".swf",
	)

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
		self.PUBLIC_BUCKET = self.s3_settings_doc.public_bucket_name
		self.folder_name = self.s3_settings_doc.folder_name

	def get_bucket(self, is_private):
		"""Return the bucket for new uploads: *Public Bucket Name* for public files, if set."""
		if not is_private and self.PUBLIC_BUCKET:
			return self.PUBLIC_BUCKET
		return self.BUCKET

	def get_bucket_of_file(self, file_url, key):
		"""Return the bucket of an uploaded file. Public file URLs are ``{endpoint}/{bucket}/{key}``.

		The bucket comes from the URL, not from the settings, so it stays correct after
		*Public Bucket Name* changes. Private file URLs always use the main bucket.
		"""
		prefix = f"{self.S3_CLIENT.meta.endpoint_url}/"
		suffix = f"/{key}"
		if file_url.startswith(prefix) and file_url.endswith(suffix):
			return file_url.removeprefix(prefix).removesuffix(suffix)
		return self.BUCKET

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

	def upload_files_to_s3_with_key(
		self, file_path, file_name, is_private, parent_doctype, parent_name, callback=None
	):
		"""
		Uploads a new file to S3.
		Strips the file extension to set the content_type in metadata.
		"""
		key = self.key_generator(file_name, parent_doctype, parent_name)
		kind = filetype.guess(file_path)
		content_type = (kind.mime if kind else None) or mimetypes.guess_type(file_name)[0]
		content_type = content_type or "application/octet-stream"
		ascii_file_name = unicodedata.normalize("NFKD", file_name).encode("ascii", "ignore").decode("ascii")
		bucket = self.get_bucket(is_private)
		transfer_kwargs = {}
		if callback:
			# The callback runs Frappe code, which only works in the job's own thread.
			transfer_kwargs = {"Callback": callback, "Config": TransferConfig(use_threads=False)}
		try:
			if is_private:
				self.S3_CLIENT.upload_file(
					file_path,
					bucket,
					key,
					ExtraArgs={
						"ContentType": content_type,
						"Metadata": {
							"ContentType": content_type,
							"file_name": ascii_file_name,
						},
					},
					**transfer_kwargs,
				)
			else:
				extra_args = {
					"ContentType": content_type,
					"Metadata": {
						"ContentType": content_type,
					},
				}
				# A public bucket is readable by its own policy. Some providers (e.g. Backblaze B2)
				# reject object ACLs, so the ACL is only set when public files share the main bucket.
				if not self.PUBLIC_BUCKET:
					extra_args["ACL"] = "public-read"

				self.S3_CLIENT.upload_file(file_path, bucket, key, ExtraArgs=extra_args, **transfer_kwargs)

		except boto3.exceptions.S3UploadFailedError:
			frappe.throw(_("File Upload Failed. Please try again."))
		return key

	def delete_from_s3(self, key, bucket):
		"""Delete file from s3"""
		if self.s3_settings_doc.delete_file_from_cloud:
			try:
				self.S3_CLIENT.delete_object(Bucket=bucket, Key=key)
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
			# Same as Frappe's send_private_file: inline so previews work, except for XSS-prone types.
			extension = os.path.splitext(file_name)[1].lower()
			disposition = "attachment" if extension in FORCE_DOWNLOAD_EXTENSIONS else "inline"
			params["ResponseContentDisposition"] = f"{disposition}; filename*=UTF-8''{quoted}"

		url = self.S3_CLIENT.generate_presigned_url(
			"get_object",
			Params=params,
			ExpiresIn=self.signed_url_expiry_time,
		)

		return url


def file_upload_to_s3(doc, _method, callback=None):
	"""Upload a **File** row to S3 after insert.

	``doc_events`` handler: uploads the on-disk file, removes the local copy unless another **File** uses it, updates
	``file_url`` and ``s3_object_key``, clears ``content_hash``, and may update the
	parent document's ``image_field`` when the parent **DocType** defines one in Meta.
	Skips upload when *Attached To DocType* is listed as ignored on **S3 File Attachment**.

	Args:
		doc: The inserted **File** document.
		_method (str): Doc event name from Frappe's hook runner (e.g. ``"after_insert"``).
			``"move_to_s3"`` updates the parent's image only if it pointed to this file.
		callback: Optional boto3 progress callback, called with the bytes sent in each chunk.
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
			file_path, doc.file_name, doc.is_private, parent_doctype, parent_name, callback
		)

		if doc.is_private:
			generate_method = "frappe_s3_attachment.controller.generate_file"
			file_url = f"""/api/method/{generate_method}?key={key}&file_name={doc.file_name}"""
		else:
			file_url = f"{s3_upload.S3_CLIENT.meta.endpoint_url}/{s3_upload.get_bucket(doc.is_private)}/{key}"

		# Change file info without triggering any hooks
		frappe.db.set_value(
			"File",
			doc.name,
			{"file_url": file_url, "s3_object_key": key, "content_hash": None},
			update_modified=False,
		)

		doc.file_url = file_url
		doc.s3_object_key = key
		doc.content_hash = None

		image_field = frappe.get_meta(parent_doctype).get("image_field")
		# A move must not make this file the parent's image, unless it already was.
		is_parent_image = _method != "move_to_s3" or (
			image_field and frappe.db.get_value(parent_doctype, parent_name, image_field) == path
		)
		if image_field and is_parent_image:
			frappe.db.set_value(parent_doctype, parent_name, image_field, file_url)

		frappe.db.commit()
		# Frappe deduplicates uploads by content_hash, so another File can still use this disk file.
		if not frappe.db.exists("File", {"file_url": path}):
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
		# Same error as a denied read, like core Frappe, so callers cannot probe which keys exist.
		raise frappe.PermissionError

	file_doc = frappe.get_doc("File", file_name_in_db)
	# No check_permission: its messages name the File, so callers could see that the key exists.
	if not frappe.has_permission("File", "read", file_doc):
		raise frappe.PermissionError

	s3_upload = S3Operations()
	# Use the stored name, not the request's, so callers cannot turn an SVG into an inline "PDF".
	signed_url = s3_upload.get_url(key, file_doc.file_name)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = signed_url
	file_type = os.path.splitext(file_doc.file_name)[1][1:]
	make_access_log(doctype="File", document=file_doc.name, file_type=file_type)
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
		if not doc.exists_on_disk():
			continue

		try:
			file_upload_to_s3(doc, "migrate_existing_files")
		except JobTimeoutException:
			# The job's time limit must stop the whole migration, not only this file.
			raise
		except Exception:
			# Skip the file so one rejected upload does not stop the whole migration.
			# A failure before the database commit leaves the local file for a later retry.
			# A cleanup failure after the commit leaves the row migrated, so it is not retried.
			frappe.db.rollback()
			frappe.log_error(
				title=_("S3 migration failed for file {0}").format(doc.name),
				reference_doctype="File",
				reference_name=doc.name,
			)
			# Keep the log when a later file fails and rolls back.
			frappe.db.commit()


@frappe.whitelist()
def migrate_existing_files():
	"""Queue migration of local **File** records to S3 on the long worker queue."""
	frappe.only_for("System Manager")
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
	bucket = s3.get_bucket_of_file(doc.file_url, doc.s3_object_key)
	s3.delete_from_s3(doc.s3_object_key, bucket)


@frappe.whitelist(methods=["POST"])
def move_to_s3(file_name: str):
	"""Queue the upload of a local **File** to S3 (button on the **File** form)."""
	doc = frappe.get_doc("File", file_name)
	doc.check_permission("write")
	if doc.s3_object_key or not doc.exists_on_disk():
		frappe.throw(_("This file is not stored locally."))
	if S3Operations().is_ignored_doctype(doc.attached_to_doctype or "File"):
		frappe.throw(_("Files attached to {0} are kept locally.").format(_(doc.attached_to_doctype)))

	enqueue_move_file(file_name, to_s3=True)


@frappe.whitelist(methods=["POST"])
def move_to_local(file_name: str):
	"""Queue the download of a **File** from S3 to local storage (button on the **File** form)."""
	doc = frappe.get_doc("File", file_name)
	doc.check_permission("write")
	if not doc.s3_object_key:
		frappe.throw(_("This file is not stored in S3."))

	enqueue_move_file(file_name, to_s3=False)


def enqueue_move_file(file_name, to_s3):
	job = enqueue(
		"frappe_s3_attachment.controller.run_move_file",
		queue="long",
		# shortcut: fixed one-hour limit, enough for ~1.5 GB at 0.5 MB/s; make it a setting if that fails
		timeout=3600,
		job_id=f"frappe_s3_attachment.move_file::{file_name}",
		deduplicate=True,
		file_name=file_name,
		to_s3=to_s3,
	)
	if not job:
		frappe.throw(_("This file is already being moved."))


def run_move_file(file_name, to_s3):
	"""Move a **File** between S3 and local storage, and tell the user (background job)."""
	doc = frappe.get_doc("File", file_name)
	target = _("S3") if to_s3 else _("Local Storage")
	progress = TransferProgress(doc, _("Moving {0} to {1}").format(doc.file_name, target))
	try:
		if to_s3:
			file_upload_to_s3(doc, "move_to_s3", progress)
		else:
			move_file_to_local(doc, progress)
	except Exception:
		frappe.msgprint(
			_("Could not move {0} to {1}. See the Error Log for details.").format(doc.file_name, target),
			indicator="red",
			realtime=True,
		)
		raise

	# A new modified timestamp makes the open File form reload itself.
	doc.db_set("modified_by", frappe.session.user, notify=True)
	frappe.msgprint(
		_("Moved {0} to {1}.").format(doc.file_name, target), indicator="green", alert=True, realtime=True
	)


class TransferProgress:
	"""boto3 transfer callback that shows a progress bar on the open **File** form."""

	def __init__(self, doc, title):
		self.doc = doc
		self.title = title
		self.done = 0
		self.last_percent = 0

	def __call__(self, bytes_amount):
		if not self.doc.file_size:
			return
		self.done += bytes_amount
		percent = min(100, int(self.done * 100 / self.doc.file_size))
		# Send at most ~20 updates, not one for every chunk.
		if percent >= self.last_percent + 5 or percent == 100:
			self.last_percent = percent
			frappe.publish_progress(percent, title=self.title, doctype="File", docname=self.doc.name)


def move_file_to_local(doc, callback=None):
	"""Download a **File** from S3 to local storage, then delete the S3 object."""
	key = doc.s3_object_key
	s3 = S3Operations()
	bucket = s3.get_bucket_of_file(doc.file_url, key)

	safe_file_name = re.sub(r"[/\\%?#]", "_", doc.file_name)
	local_file_name = generate_file_name(safe_file_name, is_private=doc.is_private)
	file_path = get_files_path(local_file_name, is_private=doc.is_private)
	# Streams to a temporary file and renames it at the end, so memory use stays small.
	s3.S3_CLIENT.download_file(
		bucket, key, file_path, Callback=callback, Config=TransferConfig(use_threads=False)
	)

	md5 = hashlib.md5(usedforsecurity=False)
	with open(file_path, "rb") as f:
		for chunk in iter(lambda: f.read(1024 * 1024), b""):
			md5.update(chunk)

	old_file_url = doc.file_url
	file_url = f"/private/files/{local_file_name}" if doc.is_private else f"/files/{local_file_name}"
	# Change file info without triggering any hooks, like file_upload_to_s3
	frappe.db.set_value(
		"File",
		doc.name,
		{"file_url": file_url, "s3_object_key": None, "content_hash": md5.hexdigest()},
		update_modified=False,
	)

	if doc.attached_to_doctype and doc.attached_to_name:
		image_field = frappe.get_meta(doc.attached_to_doctype).get("image_field")
		parent_image = image_field and frappe.db.get_value(
			doc.attached_to_doctype, doc.attached_to_name, image_field
		)
		if parent_image == old_file_url:
			frappe.db.set_value(doc.attached_to_doctype, doc.attached_to_name, image_field, file_url)

	# Commit before deleting the S3 object, so a failed commit cannot lose the file.
	frappe.db.commit()
	# Ignore "Delete file from cloud", but keep an object that another File still uses:
	# a custom s3_key_generator can give several files the same key, in one or both buckets.
	other_file_urls = frappe.get_all("File", filters={"s3_object_key": key}, pluck="file_url")
	other_buckets = {s3.get_bucket_of_file(url, key) for url in other_file_urls}
	if bucket not in other_buckets:
		try:
			s3.S3_CLIENT.delete_object(Bucket=bucket, Key=key)
		except ClientError:
			# The move is complete. The object left behind only uses storage, so log it and go on.
			frappe.log_error(
				title=_("Could not delete S3 object after moving file {0}").format(doc.name),
				reference_doctype="File",
				reference_name=doc.name,
			)


@frappe.whitelist()
def ping():
	"""
	Test function to check if api function work.
	"""
	return "pong"
