"""Ensure **Data Import** is present on **S3 File Attachment** *Ignored DocTypes*.

Older databases may lack the default child row (for example upgrades from releases
that only seeded it on ``after_install``). Without it, **Data Import** attachments
would be sent to S3 once controller-side forcing of that name was removed.

This patch delegates to ``ensure_default_ignored_doctype`` (idempotent).
"""

from frappe_s3_attachment.install import ensure_default_ignored_doctype


def execute():
	ensure_default_ignored_doctype()
