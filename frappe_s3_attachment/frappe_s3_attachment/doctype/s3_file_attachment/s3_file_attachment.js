// Copyright (c) 2018, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('S3 File Attachment', {
	refresh: function(frm) {

	},
	migrate_existing_files: function (frm) {
		frappe.call({
			method: "frappe_s3_attachment.controller.migrate_existing_files",
			freeze: true,
			freeze_message: __("Queuing S3 migration..."),
		});
	},
});
