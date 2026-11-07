/* global frappe_s3_attachment */
frappe.provide("frappe_s3_attachment");

// Upload a file from the browser directly to S3, then create its File. Resolves with the File.
// Needs "Allow Direct Upload" in S3 File Attachment and a CORS rule on the bucket.
frappe_s3_attachment.upload = async function (
	file,
	{ doctype, docname, fieldname, folder, is_private = 1 } = {}
) {
	const start = await frappe.call({
		method: "frappe_s3_attachment.controller.upload_start",
		args: { file_name: file.name, file_size: file.size, doctype, docname, fieldname, folder, is_private },
	});
	const { url, fields, token } = start.message;

	const form = new FormData();
	for (const [name, value] of Object.entries(fields)) {
		form.append(name, value);
	}
	// S3 ignores form fields after the file.
	form.append("file", file);
	const response = await fetch(url, { method: "POST", body: form });
	if (!response.ok) {
		frappe.throw(__("Upload to S3 failed with status {0}.", [response.status]));
	}

	const complete = await frappe.call({
		method: "frappe_s3_attachment.controller.upload_complete",
		args: { token },
	});
	return complete.message;
};
