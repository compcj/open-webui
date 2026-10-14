import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

import { extractReleaseNotes, formatReleaseNotes, prepareRelease } from './prepare-release.js';

const before = 'a'.repeat(40);
const head = 'b'.repeat(40);
const context = { before, head, repository: 'example/oui', serverUrl: 'https://github.com' };
const changelog =
	'# Changelog\n\n## [1.2.0] - 2026-10-14\n\n### Fixed\n\n- Release fix.\n\n## [1.1.0]\n\n- Old release.\n';

function readFixture(previous, current, notes = changelog) {
	const files = new Map([
		[`${before}:package.json`, JSON.stringify(previous)],
		[`${head}:package.json`, JSON.stringify(current)],
		[`${head}:CHANGELOG.md`, notes]
	]);
	return (sha, path) => {
		assert.ok(files.has(`${sha}:${path}`), `Unexpected read: ${sha}:${path}`);
		return files.get(`${sha}:${path}`);
	};
}

test('does not release when other package fields change without a version change', () => {
	const result = prepareRelease(
		context,
		readFixture(
			{ version: '1.2.0', scripts: { test: 'old' } },
			{ version: '1.2.0', scripts: { test: 'new' } },
			'No matching changelog section is needed when skipping.'
		)
	);
	assert.equal(result.changed, false);
	assert.equal(result.version, '1.2.0');
	assert.equal(result.notes, undefined);
});

test('prepares a release when the before and head package versions differ', () => {
	const result = prepareRelease(context, readFixture({ version: '1.1.0' }, { version: '1.2.0' }));
	assert.equal(result.changed, true);
	assert.equal(result.previousVersion, '1.1.0');
	assert.equal(result.version, '1.2.0');
	assert.equal(result.notes, '### Fixed\n\n- Release fix.');
});

test('skips safely without a prior commit, including initial branch pushes', () => {
	for (const prior of [undefined, null, '', '0'.repeat(40)]) {
		const result = prepareRelease({ ...context, before: prior }, () => {
			assert.fail('Must not read history or prepare notes without a prior commit');
		});
		assert.equal(result.changed, false);
		assert.match(result.reason, /prior commit/i);
	}
});

test('fails closed with a clear error when the prior commit is unavailable', () => {
	assert.throws(
		() =>
			prepareRelease(context, () => {
				throw new Error('unknown revision');
			}),
		/Unable to read package\.json at a{40}/
	);
});

test('rejects invalid commit input before invoking Git', () => {
	for (const invalid of ['--help', 'main', '123\nchanged=true']) {
		assert.throws(
			() => prepareRelease({ ...context, before: invalid }, () => assert.fail('Invalid Git read')),
			/Invalid before commit/
		);
	}
	assert.throws(
		() => prepareRelease({ ...context, head: undefined }, () => assert.fail('Invalid Git read')),
		/Invalid head commit/
	);
});

test('rejects an invalid package version before writing workflow outputs', () => {
	assert.throws(
		() =>
			prepareRelease(
				context,
				readFixture({ version: '1.1.0' }, { version: '1.2.0\nchanged=true' })
			),
		/Invalid package version/
	);
});

test('extracts only the exact version section with normalized newlines', () => {
	const text = '## [1x2x0]\r\n\r\nWrong section.\r\n\r\n' + changelog.replaceAll('\n', '\r\n');
	assert.equal(extractReleaseNotes(text, '1.2.0'), '### Fixed\n\n- Release fix.');
});

test('fails clearly when the requested release section is missing or empty', () => {
	assert.throws(() => extractReleaseNotes(changelog, '9.0.0'), /No CHANGELOG section for 9\.0\.0/);
	assert.throws(
		() => extractReleaseNotes('## [1.2.0]\n\n## [1.1.0]\nOld', '1.2.0'),
		/Empty CHANGELOG section for 1\.2\.0/
	);
});

test('keeps ordinary release notes intact', () => {
	const notes = '### Fixed\n\n- Preserve details, Unicode 中文 and emoji 🛠️.';
	assert.equal(formatReleaseNotes(notes, context), notes);
});

test('bounds the actual v0.12.0 changelog and links to the immutable full source', () => {
	const text = readFileSync(new URL('../CHANGELOG.md', import.meta.url), 'utf8');
	const original = extractReleaseNotes(text, '0.12.0');
	assert.ok(original.length > 125000, 'Regression fixture must exceed the GitHub release limit');
	const notes = formatReleaseNotes(original, context);
	assert.ok(notes.length <= 120000);
	assert.ok(notes.startsWith(original.slice(0, 1000)), 'Must preserve meaningful release notes');
	assert.match(notes, /truncated/i);
	assert.ok(notes.includes(`https://github.com/example/oui/blob/${head}/CHANGELOG.md`));
	assert.equal(notes.isWellFormed(), true);
});

test('never splits a Unicode surrogate pair when truncating a long line', () => {
	for (const prefix of ['', 'x']) {
		const notes = formatReleaseNotes(prefix + '😀'.repeat(70000), context);
		assert.ok(notes.length <= 120000);
		assert.equal(notes.isWellFormed(), true);
		assert.ok(notes.startsWith(prefix + '😀'.repeat(100)));
	}
});

test('escapes repository names in the full changelog Markdown link', () => {
	const notes = formatReleaseNotes('x'.repeat(130000), {
		...context,
		repository: 'example/repo(name)',
		serverUrl: 'https://github.example.com/'
	});
	assert.ok(
		notes.includes(`https://github.example.com/example/repo%28name%29/blob/${head}/CHANGELOG.md`)
	);
	assert.ok(notes.length <= 120000);
});

test('CLI writes a false workflow output when the checked-out version has not changed', (t) => {
	const root = fileURLToPath(new URL('..', import.meta.url));
	const sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim();
	const directory = mkdtempSync(join(tmpdir(), 'oui-release-test-'));
	assert.equal(dirname(directory), resolve(tmpdir()));
	t.after(() => rmSync(directory, { recursive: true, force: true }));
	const outputPath = join(directory, 'github-output');
	const result = spawnSync(process.execPath, ['scripts/prepare-release.js'], {
		cwd: root,
		encoding: 'utf8',
		env: {
			...process.env,
			RELEASE_BEFORE: sha,
			RELEASE_HEAD: sha,
			GITHUB_REPOSITORY: 'example/oui',
			GITHUB_OUTPUT: outputPath,
			RELEASE_NOTES_PATH: join(directory, 'release-notes.md')
		}
	});
	assert.equal(result.status, 0, result.stderr);
	assert.match(readFileSync(outputPath, 'utf8'), /^changed=false$/m);
	assert.match(result.stdout, /unchanged/i);
});
