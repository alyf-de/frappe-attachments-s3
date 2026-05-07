module.exports = {
	branches: [
		{
			name: "version-15",
		},
		{
			name: "develop",
			channel: "develop",
			prerelease: "beta",
		},
	],
	tagFormat: "v${version}",
	plugins: [
		[
			"@semantic-release/commit-analyzer",
			{
				preset: "conventionalcommits",
			},
		],
		[
			"@semantic-release/release-notes-generator",
			{
				preset: "conventionalcommits",
			},
		],
		[
			"@semantic-release/changelog",
			{
				changelogFile: "CHANGELOG.md",
			},
		],
		[
			"@semantic-release/exec",
			{
				prepareCmd:
					"python -c \"from pathlib import Path; Path('frappe_s3_attachment/__init__.py').write_text('__version__ = \\\"${nextRelease.version}\\\"\\\\n')\"",
			},
		],
		[
			"@semantic-release/git",
			{
				assets: ["CHANGELOG.md", "frappe_s3_attachment/__init__.py"],
				message: "chore(release): ${nextRelease.version} [skip ci]\n\n${nextRelease.notes}",
			},
		],
		"@semantic-release/github",
	],
};
