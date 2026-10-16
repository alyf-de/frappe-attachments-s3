frappe.ui.form.on("File", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.is_folder || !frm.has_perm("write")) return;

		if (frm.doc.s3_object_key) {
			frm.add_custom_button(__("Move to Local"), () => move_file(frm, "move_to_local"));
		} else if (/^\/(private\/)?files\//.test(frm.doc.file_url || "")) {
			frm.add_custom_button(__("Move to S3"), () => move_file(frm, "move_to_s3"));
		}
	},
});

function move_file(frm, method) {
	frappe.call({
		method: `frappe_s3_attachment.controller.${method}`,
		args: { file_name: frm.doc.name },
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}
