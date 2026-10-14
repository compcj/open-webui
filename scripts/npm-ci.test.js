import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import {
	copyFileSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	writeFileSync
} from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const bash = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : 'bash';

function runDockerInstall(t, failures, errorCode = 'EEXIST', errorPath = '_cacache/tmp/collision') {
	const directory = mkdtempSync(path.join(tmpdir(), 'oui-npm-ci-test-'));
	assert.equal(path.dirname(directory), path.resolve(tmpdir()));
	t.after(() => rmSync(directory, { recursive: true, force: true }));
	mkdirSync(path.join(directory, 'fakebin'));
	mkdirSync(path.join(directory, 'scripts'));
	const script = path.join(root, 'scripts/npm-ci.sh');
	if (existsSync(script)) copyFileSync(script, path.join(directory, 'scripts/npm-ci.sh'));
	writeFileSync(
		path.join(directory, 'fakebin/npm'),
		`#!/bin/sh
cache_dir=default-cache
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--cache" ]; then shift; cache_dir=$1; fi
  shift
done
printf '%s\\n' "$cache_dir" >> attempts
count=$(wc -l < attempts)
if [ "$count" -le "$TEST_FAILURES" ]; then
  printf 'npm error code %s\\nnpm error path %s/%s\\n' "$TEST_ERROR_CODE" "$cache_dir" "$TEST_ERROR_PATH" >&2
  exit 239
fi
printf 'installed from frozen lockfile\\n'
`,
		{ mode: 0o755 }
	);
	const dockerfile = readFileSync(path.join(root, 'Dockerfile'), 'utf8');
	const command = dockerfile.match(/^RUN ((?:npm ci\b|sh scripts\/npm-ci\.sh)[^\r\n]*)/m)?.[1];
	assert.ok(command, 'Docker frontend dependency installation must be present');
	const result = spawnSync(
		bash,
		['--noprofile', '--norc', '-c', `export PATH="$PWD/fakebin:$PATH"; ${command}`],
		{
			cwd: directory,
			env: {
				...process.env,
				TEST_FAILURES: String(failures),
				TEST_ERROR_CODE: errorCode,
				TEST_ERROR_PATH: errorPath
			},
			encoding: 'utf8'
		}
	);
	assert.ifError(result.error);
	const attempts = readFileSync(path.join(directory, 'attempts'), 'utf8').trim().split(/\r?\n/);
	// Bash reports POSIX paths on Windows; ask the same shell whether cleanup completed.
	const remaining = spawnSync(
		bash,
		[
			'--noprofile',
			'--norc',
			'-c',
			'while IFS= read -r cache; do if [ -e "$cache" ]; then printf "%s\\n" "$cache"; fi; done < attempts'
		],
		{ cwd: directory, encoding: 'utf8' }
	);
	assert.equal(remaining.status, 0);
	assert.equal(
		remaining.stdout,
		'',
		'installer must clean only its temporary caches on every exit'
	);
	return { ...result, attempts };
}

test('Docker npm install recovers from a cache collision with a fresh cache', (t) => {
	const result = runDockerInstall(t, 1);
	assert.equal(result.status, 0, result.stderr + result.stdout);
	assert.equal(result.attempts.length, 2);
	assert.equal(new Set(result.attempts).size, 2);
	assert.match(result.stdout, /installed from frozen lockfile/);
});

test('Docker npm install stops after three cache collisions and preserves failure', (t) => {
	const result = runDockerInstall(t, 10);
	assert.equal(result.status, 239, result.stderr + result.stdout);
	assert.equal(result.attempts.length, 3);
	assert.equal(new Set(result.attempts).size, 3);
	assert.match(result.stderr + result.stdout, /EEXIST/);
});

test('Docker npm install does not retry a dependency or lifecycle failure', (t) => {
	const result = runDockerInstall(t, 10, 'EUSAGE');
	assert.equal(result.status, 239);
	assert.equal(result.attempts.length, 1);
});

test('Docker npm install does not retry file collisions outside its cache', (t) => {
	const result = runDockerInstall(t, 10, 'EEXIST', 'node_modules/collision');
	assert.equal(result.status, 239);
	assert.equal(result.attempts.length, 1);
});

test('Docker npm install succeeds without retries when its first attempt succeeds', (t) => {
	const result = runDockerInstall(t, 0);
	assert.equal(result.status, 0, result.stderr + result.stdout);
	assert.equal(result.attempts.length, 1);
	assert.notEqual(result.attempts[0], 'default-cache');
});
