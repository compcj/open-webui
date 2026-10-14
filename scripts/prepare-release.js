import { execFileSync } from 'node:child_process';
import { appendFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Count UTF-16 units conservatively and leave room below GitHub's 125000-character limit.
const MAX_NOTES_LENGTH = 120000;
const COMMIT_SHA = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i;
const VERSION = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/;

function validateCommit(sha, name) {
	if (typeof sha !== 'string' || !COMMIT_SHA.test(sha) || /^0+$/.test(sha)) {
		throw new Error(`Invalid ${name} commit`);
	}
}

function readGitFile(sha, path) {
	return execFileSync('git', ['show', `${sha}:${path}`], {
		encoding: 'utf8',
		maxBuffer: 16 * 1024 * 1024,
		stdio: ['ignore', 'pipe', 'pipe']
	});
}

export function prepareRelease(context, readFileAtCommit = readGitFile) {
	const { before, head } = context;
	if (before == null || before === '' || /^0+$/.test(before)) {
		return { changed: false, reason: 'No prior commit in push context; skipping release.' };
	}
	validateCommit(before, 'before');
	validateCommit(head, 'head');

	const read = (sha, path) => {
		try {
			return readFileAtCommit(sha, path);
		} catch (cause) {
			throw new Error(`Unable to read ${path} at ${sha}; ensure push history was fetched.`, {
				cause
			});
		}
	};
	const readVersion = (sha) => {
		const { version } = JSON.parse(read(sha, 'package.json'));
		if (typeof version !== 'string' || !VERSION.test(version)) {
			throw new Error(`Invalid package version at ${sha}`);
		}
		return version;
	};
	const previousVersion = readVersion(before);
	const version = readVersion(head);
	if (previousVersion === version) {
		return {
			changed: false,
			version,
			reason: `Package version unchanged (${version}); skipping release.`
		};
	}

	const notes = extractReleaseNotes(read(head, 'CHANGELOG.md'), version);
	return { changed: true, version, previousVersion, notes: formatReleaseNotes(notes, context) };
}

export function extractReleaseNotes(changelog, version) {
	const lines = [];
	let found = false;
	for (const line of changelog.replaceAll('\r\n', '\n').split('\n')) {
		const heading = line.match(/^## \[([^\]]+)\]/);
		if (heading) {
			if (found) break;
			found = heading[1] === version;
			continue;
		}
		if (found) lines.push(line);
	}
	if (!found) throw new Error(`No CHANGELOG section for ${version}`);
	const notes = lines.join('\n').trim();
	if (!notes) throw new Error(`Empty CHANGELOG section for ${version}`);
	return notes;
}

export function formatReleaseNotes(notes, { repository, head, serverUrl = 'https://github.com' }) {
	if (notes.length <= MAX_NOTES_LENGTH) return notes;
	validateCommit(head, 'head');
	const parts = repository?.split('/');
	if (parts?.length !== 2 || parts.some((part) => !part)) {
		throw new Error('Invalid repository for full changelog link');
	}
	const url = new URL(serverUrl);
	if (!['http:', 'https:'].includes(url.protocol)) {
		throw new Error('Invalid GitHub server URL');
	}
	const encodedRepository = parts
		.map((part) =>
			encodeURIComponent(part).replace(
				/[!'()*]/g,
				(char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`
			)
		)
		.join('/');
	url.pathname = `${url.pathname.replace(/\/+$/, '')}/${encodedRepository}/blob/${head}/CHANGELOG.md`;
	url.search = '';
	url.hash = '';
	const footer = `\n\n---\n\nRelease notes were truncated to fit GitHub's size limit. [Read the complete changelog](<${url.href}>).\n`;
	const limit = MAX_NOTES_LENGTH - footer.length;
	if (limit <= 0) throw new Error('Full changelog link exceeds the release notes limit');
	let excerpt = notes.slice(0, limit);
	// Do not leave half of a surrogate pair when a very long line reaches the limit.
	if (/[\uD800-\uDBFF]$/.test(excerpt)) excerpt = excerpt.slice(0, -1);
	const lineEnd = excerpt.lastIndexOf('\n');
	if (lineEnd >= excerpt.length / 2) excerpt = excerpt.slice(0, lineEnd);
	return excerpt.trimEnd() + footer;
}

function main() {
	const { GITHUB_OUTPUT: outputPath, RELEASE_NOTES_PATH: notesPath } = process.env;
	if (!outputPath) throw new Error('GITHUB_OUTPUT is required');
	const release = prepareRelease({
		before: process.env.RELEASE_BEFORE,
		head: process.env.RELEASE_HEAD,
		repository: process.env.GITHUB_REPOSITORY,
		serverUrl: process.env.GITHUB_SERVER_URL
	});
	if (release.changed) {
		if (!notesPath) throw new Error('RELEASE_NOTES_PATH is required');
		writeFileSync(notesPath, release.notes);
		console.log(
			`Preparing release ${release.previousVersion} -> ${release.version} (${release.notes.length} notes characters).`
		);
	} else {
		console.log(release.reason);
	}
	appendFileSync(outputPath, `changed=${release.changed}\nversion=${release.version ?? ''}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	try {
		main();
	} catch (error) {
		console.error(error.message);
		process.exitCode = 1;
	}
}
