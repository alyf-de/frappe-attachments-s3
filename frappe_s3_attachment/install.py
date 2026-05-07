import frappe


def after_install():
	ensure_default_ignored_doctype()


def ensure_default_ignored_doctype():
	settings = frappe.get_single("S3 File Attachment")
	configured_ignored = {row.doctype_name for row in (settings.ignored_doctypes or []) if row.doctype_name}
	if "Data Import" in configured_ignored:
		return

	settings.append("ignored_doctypes", {"doctype_name": "Data Import"})
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)
