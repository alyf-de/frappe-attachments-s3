# Copyright (c) 2018, Frappe and contributors
# For license information, please see license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.background_jobs import enqueue

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class S3FileAttachment(Document):
	def validate(self):
		self.validate_credentials()
		self.validate_endpoint_url()

	def on_update(self):
		# Public files in proxy mode need a Frappe URL, because browsers cannot reach the bucket.
		if any(
			self.has_value_changed(fieldname)
			for fieldname in ("download_mode", "public_download_mode", "public_bucket_name")
		):
			enqueue(
				"frappe_s3_attachment.controller.rewrite_public_file_urls",
				queue="long",
				timeout=3600,
				enqueue_after_commit=True,
			)

	def validate_credentials(self):
		has_access_key = bool((self.access_key or "").strip())
		has_secret_key = bool((self.get_password("secret_key", raise_exception=False) or "").strip())
		if has_access_key != has_secret_key:
			frappe.throw(
				_("Set both {0} and {1}, or leave both empty.").format(
					frappe.bold(_("Access Key")), frappe.bold(_("Secret Key"))
				)
			)

	def validate_endpoint_url(self):
		endpoint_url = (self.endpoint_url or "").strip()
		if not endpoint_url:
			return

		parsed_endpoint = urlparse(endpoint_url)
		# Browsers load files from this endpoint, through presigned URLs and public file URLs. Over HTTP,
		# they travel in clear text and an HTTPS site blocks them as mixed content. On localhost,
		# e.g. MinIO for development, neither applies. In proxy mode, only the server uses the endpoint.
		uses_only_proxy_mode = self.download_mode == "Proxy" and (
			not self.public_bucket_name or self.public_download_mode == "Proxy"
		)
		is_allowed_http = parsed_endpoint.scheme == "http" and (
			parsed_endpoint.hostname in LOCAL_HOSTS or uses_only_proxy_mode
		)
		if (parsed_endpoint.scheme != "https" and not is_allowed_http) or not parsed_endpoint.netloc:
			frappe.throw(
				_("Enter a valid HTTPS URL in {0}, for example: {1}.").format(
					frappe.bold(_("S3 Endpoint URL")), frappe.bold("https://nbg1.your-objectstorage.com")
				)
			)

		if parsed_endpoint.params or parsed_endpoint.query or parsed_endpoint.fragment:
			frappe.throw(
				_("{0} must not include query strings or fragments.").format(
					frappe.bold(_("S3 Endpoint URL"))
				)
			)
