frappe.ui.form.on("File", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.is_folder || !frm.has_perm("write")) return;

		if (frm.doc.s3_object_key) {
			frm.add_custom_button(__("Move to Local Storage"), () =>
				move_file(
					frm,
					"move_to_local",
					__(
						"This downloads the file from S3 to this server and deletes it from S3. The file URL changes, so links to the old URL stop working."
					)
				)
			);
		} else if (/^\/(private\/)?files\//.test(frm.doc.file_url || "")) {
			frm.add_custom_button(__("Move to S3"), () =>
				move_file(
					frm,
					"move_to_s3",
					__(
						"This uploads the file to S3 and deletes it from this server. The file URL changes, so links to the old URL stop working."
					)
				)
			);
		}
	},
});

function move_file(frm, method, message) {
	frappe.confirm(message, () =>
		frappe.call({
			method: `frappe_s3_attachment.controller.${method}`,
			args: { file_name: frm.doc.name },
			freeze: true,
			// The job shows its progress here and reloads the form when it is done.
			callback: () => frappe.show_alert(__("Moving the file in the background...")),
		})
	);
}
