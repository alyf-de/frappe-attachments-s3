# Copyright (c) 2018, Frappe and contributors
# For license information, please see license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document


class S3FileAttachment(Document):
	def validate(self):
		self.validate_credentials()
		self.validate_endpoint_url()

	def validate_credentials(self):
		has_access_key = bool((self.access_key or "").strip())
		has_secret_key = bool((self.get_password("secret_key") or "").strip())
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
		if parsed_endpoint.scheme != "https" or not parsed_endpoint.netloc:
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
