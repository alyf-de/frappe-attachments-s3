"""Add **Bank Statement Import** to *Ignored DocTypes* on existing sites.

It writes back to the file on disk, which fails for S3 files. Only this row is added,
so a **Data Import** row that an admin removed stays removed.
"""

from frappe_s3_attachment.install import ensure_default_ignored_doctype


def execute():
	ensure_default_ignored_doctype(("Bank Statement Import",))
