import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { copyFile, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { promisify } from 'node:util';
import test from 'node:test';
import { withDownloadRetry } from './pyodide-downloads.js';

const execFileAsync = promisify(execFile);
const nativePackages = [
	'micropip',
	'packaging',
	'requests',
	'beautifulsoup4',
	'numpy',
	'pandas',
	'matplotlib',
	'scikit-learn',
	'scipy',
	'regex',
	'sympy',
	'tiktoken',
	'pytz',
	'openai',
	'lxml',
	'click',
	'platformdirs',
	'pillow',
	'typing-extensions'
];

// Keep the real orchestration and filesystem writes, replacing only the Pyodide
// runtime and external hosts so failures are deterministic and no assets are downloaded.
async function prepareFixture(
	t,
	{
		failures = 0,
		failureStage = 'install:black',
		permanent = false,
		reportLoadFailure = false,
		loadFailureMessage,
		alreadyLoaded = false,
		nativeDependency = false,
		nativeAlias = false,
		respond
	} = {}
) {
	const directory = await mkdtemp(join(tmpdir(), 'prepare-pyodide-test-'));
	t.after(async () => {
		assert.equal(dirname(directory), resolve(tmpdir()));
		await rm(directory, { recursive: true, force: true });
	});
	for (const path of ['scripts', 'node_modules/pyodide', 'node_modules/undici', 'static/pyodide']) {
		await mkdir(join(directory, path), { recursive: true });
	}
	for (const file of (await readdir(new URL('.', import.meta.url))).filter(
		(file) => file === 'prepare-pyodide.js' || file === 'pyodide-downloads.js'
	)) {
		await copyFile(new URL(file, import.meta.url), join(directory, 'scripts', file));
	}
	await writeFile(
		join(directory, 'package.json'),
		JSON.stringify({ type: 'module', dependencies: { pyodide: '1.0.0' } })
	);
	for (const name of ['pyodide', 'undici']) {
		await writeFile(
			join(directory, 'node_modules', name, 'package.json'),
			JSON.stringify({ type: 'module', version: '1.0.0', exports: './index.js' })
		);
	}
	const lock = {
		packages: Object.fromEntries(nativePackages.map((name) => [name, { file_name: `${name}.whl` }]))
	};
	lock.packages.matplotlib.depends = ['pillow'];
	lock.packages.openai.depends = ['typing-extensions'];
	if (nativeDependency) {
		lock.packages.numpy.depends = ['native-dependency'];
		lock.packages['native-dependency'] = { file_name: 'native-dependency.whl' };
	}
	if (nativeAlias) lock.packages.pillow.name = 'Pillow';
	await writeFile(join(directory, 'node_modules/pyodide/pyodide-lock.json'), JSON.stringify(lock));
	await writeFile(
		join(directory, 'static/pyodide/package.json'),
		JSON.stringify({ version: '1.0.0' })
	);
	await writeFile(
		join(directory, 'node_modules/pyodide/index.js'),
		`import { appendFile, readFile, writeFile } from 'node:fs/promises';
		let attempts = 0;
		const record = (value) => appendFile('operations.log', value + '\\n');
		async function attempt(operation) {
			await record(operation);
			if (operation === ${JSON.stringify(failureStage)} && attempts++ < ${failures}) {
				throw Object.assign(new Error(${JSON.stringify(permanent ? 'ValueError: incompatible wheel' : 'pyodide.http._exceptions.AbortError: fetch failed')}), {
					name: 'PythonError', type: '${permanent ? 'ValueError' : 'AbortError'}'
				});
			}
		}
		export async function loadPyodide() {
			await attempt('loadPyodide');
			const lock = JSON.parse(await readFile('node_modules/pyodide/pyodide-lock.json'));
			const loadedPackages = ${alreadyLoaded ? "{ micropip: 'default channel' }" : '{}'};
			if (loadedPackages.micropip) await writeFile('static/pyodide/micropip.whl', 'wheel');
			async function loadPackage(names, { errorCallback = console.error } = {}) {
				const pending = new Set();
				function include(pkg) {
					if (loadedPackages[pkg] || pending.has(pkg)) return;
					pending.add(pkg);
					for (const dependency of lock.packages[pkg].depends || []) include(dependency);
				}
				for (const name of [].concat(names)) include(name);
				const result = [];
				for (const pkg of pending) {
					try {
						await attempt('loadPackage:' + pkg);
					} catch (error) {
						if (!${reportLoadFailure}) throw error;
						errorCallback('The following error occurred while loading ' + pkg + ':');
						errorCallback(${JSON.stringify(loadFailureMessage)} ?? error.message);
						continue;
					}
					loadedPackages[lock.packages[pkg].name || pkg] = 'default channel';
					await writeFile('static/pyodide/' + lock.packages[pkg].file_name, 'wheel');
					result.push({ name: pkg });
				}
				return result;
			}
			return {
				loadPackage,
				loadedPackages,
				pyimport: () => {
					if (!loadedPackages.micropip) throw Object.assign(new Error("No module named 'micropip'"), {
						name: 'PythonError', type: 'ModuleNotFoundError'
					});
					return {
					install: async (pkg) => {
						await attempt('install:' + pkg);
						if (lock.packages[pkg]) await loadPackage(pkg);
						if (pkg === 'black') await loadPackage(['click', 'platformdirs']);
					},
					freeze: async () => { await record('freeze'); return JSON.stringify(lock); }
				}; }
			};
		}`
	);
	await writeFile(
		join(directory, 'node_modules/undici/index.js'),
		'export const setGlobalDispatcher = () => {}; export class ProxyAgent {}'
	);
	await writeFile(
		join(directory, 'redirect-fetch.js'),
		`const originalFetch = globalThis.fetch;
		globalThis.fetch = (url, options) => originalFetch(new URL(new URL(url).pathname, process.env.TEST_PYPI_URL), options);`
	);
	const requests = [];
	const server = createServer((request, response) => {
		requests.push(request.url);
		if (respond?.(request, response, requests)) return;
		if (request.url.endsWith('/json')) {
			const name = request.url.split('/')[2];
			const filename = `${name}-1.0.0-py3-none-any.whl`;
			response.setHeader('Content-Type', 'application/json');
			response.end(
				JSON.stringify({
					info: { version: '1.0.0' },
					urls: [{ filename, url: `https://files.pythonhosted.org/${filename}` }]
				})
			);
		} else {
			response.end('synthetic wheel bytes');
		}
	});
	server.listen(0, '127.0.0.1');
	await once(server, 'listening');
	t.after(() => new Promise((resolve) => server.close(resolve)));
	return {
		directory,
		requests,
		async run(slim = false) {
			let result;
			try {
				result = await execFileAsync(
					process.execPath,
					['--import', './redirect-fetch.js', 'scripts/prepare-pyodide.js'],
					{
						cwd: directory,
						env: {
							...process.env,
							all_proxy: '',
							ALL_PROXY: '',
							https_proxy: '',
							HTTPS_PROXY: '',
							http_proxy: '',
							HTTP_PROXY: '',
							USE_SLIM: String(slim),
							TEST_PYPI_URL: `http://127.0.0.1:${server.address().port}`
						},
						timeout: 15000
					}
				);
			} catch (error) {
				result = error;
			}
			return { code: 0, ...result };
		},
		async operations() {
			try {
				return (await readFile(join(directory, 'operations.log'), 'utf-8')).trim().split('\n');
			} catch (error) {
				if (error.code === 'ENOENT') return [];
				throw error;
			}
		}
	};
}

test('full preparation continues through later packages after a transient micropip failure', async (t) => {
	const fixture = await prepareFixture(t, { failures: 1 });
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	const operations = await fixture.operations();
	assert.deepEqual(
		operations.filter((operation) =>
			['install:black', 'loadPackage:openai', 'loadPackage:lxml', 'freeze'].includes(operation)
		),
		['install:black', 'install:black', 'loadPackage:openai', 'loadPackage:lxml', 'freeze']
	);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('exhausted micropip retries stop preparation with the original error', async (t) => {
	const fixture = await prepareFixture(t, { failures: 10 });
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Installing package black failed after 3 attempts/);
	assert.match(result.stderr, /\[cause\]:.*PythonError.*AbortError: fetch failed/s);
	const operations = await fixture.operations();
	assert.equal(operations.filter((operation) => operation === 'install:black').length, 3);
	assert.equal(operations.includes('loadPackage:openai'), false);
	assert.equal(operations.includes('freeze'), false);
	assert.deepEqual(fixture.requests, []);
});

test('permanent micropip errors fail immediately without preparing partial assets', async (t) => {
	const fixture = await prepareFixture(t, { failures: 10, permanent: true });
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Installing package black failed/);
	assert.match(result.stderr, /ValueError: incompatible wheel/);
	assert.equal((await fixture.operations()).filter((value) => value === 'install:black').length, 1);
	assert.deepEqual(fixture.requests, []);
});

for (const failureStage of ['loadPyodide', 'loadPackage:micropip']) {
	test(`transient ${failureStage} failures recover before installation`, async (t) => {
		const fixture = await prepareFixture(t, { failures: 1, failureStage });
		const result = await fixture.run();
		assert.equal(result.code, 0, result.stderr);
		assert.equal((await fixture.operations()).filter((value) => value === failureStage).length, 2);
		assert.match(result.stdout, /All listed packages are bundled/);
	});
}

test('nonthrowing micropip load failures recover using the callback error', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 1,
		failureStage: 'loadPackage:micropip',
		reportLoadFailure: true
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.equal(
		(await fixture.operations()).filter((value) => value === 'loadPackage:micropip').length,
		2
	);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('nonthrowing micropip load failures stop after retry exhaustion', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 10,
		failureStage: 'loadPackage:micropip',
		reportLoadFailure: true
	});
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Loading micropip failed after 3 attempts/);
	assert.match(result.stderr, /AbortError: fetch failed/);
	assert.deepEqual(await fixture.operations(), [
		'loadPyodide',
		...Array(3).fill('loadPackage:micropip')
	]);
	assert.deepEqual(fixture.requests, []);
});

test('nonthrowing permanent micropip load errors fail without retrying', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 10,
		failureStage: 'loadPackage:micropip',
		permanent: true,
		reportLoadFailure: true
	});
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Loading micropip failed/);
	assert.match(result.stderr, /ValueError: incompatible wheel/);
	assert.deepEqual(await fixture.operations(), ['loadPyodide', 'loadPackage:micropip']);
	assert.deepEqual(fixture.requests, []);
});

test('an already-loaded micropip is accepted without another download', async (t) => {
	const fixture = await prepareFixture(t, { alreadyLoaded: true });
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.equal(
		(await fixture.operations()).filter((value) => value === 'loadPackage:micropip').length,
		0
	);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('nonthrowing native package failures retry the missing dependency of an installed parent', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 1,
		failureStage: 'loadPackage:native-dependency',
		reportLoadFailure: true,
		nativeDependency: true
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	const operations = await fixture.operations();
	assert.equal(operations.filter((value) => value === 'loadPackage:numpy').length, 1);
	assert.equal(operations.filter((value) => value === 'loadPackage:native-dependency').length, 2);
	assert.equal(
		await readFile(join(fixture.directory, 'static/pyodide/native-dependency.whl'), 'utf-8'),
		'wheel'
	);
});

test('nonthrowing native dependencies of PyPI packages recover before micropip installation', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 1,
		failureStage: 'loadPackage:click',
		reportLoadFailure: true
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	const operations = await fixture.operations();
	assert.equal(operations.filter((value) => value === 'loadPackage:click').length, 2);
	assert.ok(operations.lastIndexOf('loadPackage:click') < operations.indexOf('install:black'));
});

test('native HTTP failures retry when Pyodide reports only a generic request failure', async (t) => {
	const fixture = await prepareFixture(t, {
		failures: 1,
		failureStage: 'loadPackage:numpy',
		reportLoadFailure: true,
		loadFailureMessage: "Failed to load 'https://example.invalid/numpy.whl': request failed."
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.equal(
		(await fixture.operations()).filter((value) => value === 'loadPackage:numpy').length,
		2
	);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('native package availability uses the distribution name from its lock entry', async (t) => {
	const fixture = await prepareFixture(t, { nativeAlias: true });
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('retry errors retain the original permanent error object as their cause', async () => {
	const original = new Error('invalid package metadata');
	let attempts = 0;
	await assert.rejects(
		withDownloadRetry('Installing package example', () => {
			attempts++;
			throw original;
		}),
		(error) => error.cause === original && error.message === 'Installing package example failed'
	);
	assert.equal(attempts, 1);
});

test('PyPI metadata and wheel HTTP failures recover before completing the full bundle', async (t) => {
	const fixture = await prepareFixture(t, {
		respond(request, response, requests) {
			if (
				['/pypi/black/json', '/black-1.0.0-py3-none-any.whl'].includes(request.url) &&
				requests.filter((url) => url === request.url).length === 1
			) {
				response.writeHead(request.url.endsWith('/json') ? 503 : 429);
				response.end('temporarily unavailable');
				return true;
			}
		}
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.equal(fixture.requests.filter((url) => url === '/pypi/black/json').length, 2);
	assert.equal(fixture.requests.filter((url) => url === '/black-1.0.0-py3-none-any.whl').length, 2);
	assert.match(result.stdout, /All listed packages are bundled/);
});

test('an interrupted wheel response is downloaded again before it is saved', async (t) => {
	const fixture = await prepareFixture(t, {
		respond(request, response, requests) {
			if (
				request.url === '/black-1.0.0-py3-none-any.whl' &&
				requests.filter((url) => url === request.url).length === 1
			) {
				response.writeHead(200, { 'Content-Length': 100 });
				response.write('incomplete');
				setTimeout(() => response.destroy(), 25);
				return true;
			}
		}
	});
	const result = await fixture.run();
	assert.equal(result.code, 0, result.stderr);
	assert.equal(fixture.requests.filter((url) => url === '/black-1.0.0-py3-none-any.whl').length, 2);
	assert.equal(
		await readFile(join(fixture.directory, 'static/pyodide/black-1.0.0-py3-none-any.whl'), 'utf-8'),
		'synthetic wheel bytes'
	);
});

test('exhausted PyPI metadata retries stop without writing a partial PyPI lock', async (t) => {
	const fixture = await prepareFixture(t, {
		respond(request, response) {
			response.writeHead(503);
			response.end('temporarily unavailable');
			return true;
		}
	});
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Fetching PyPI metadata for black failed after 3 attempts/);
	assert.match(result.stderr, /\[cause\]:.*HTTP 503/s);
	assert.deepEqual(fixture.requests, Array(3).fill('/pypi/black/json'));
	const lock = JSON.parse(
		await readFile(join(fixture.directory, 'static/pyodide/pyodide-lock.json'))
	);
	assert.equal(lock.packages.black, undefined);
	assert.equal(
		(await readdir(join(fixture.directory, 'static/pyodide'))).some((name) =>
			name.endsWith('.tmp')
		),
		false
	);
});

test('permanent PyPI wheel HTTP errors fail without retrying or fetching later packages', async (t) => {
	const fixture = await prepareFixture(t, {
		respond(request, response) {
			if (request.url === '/black-1.0.0-py3-none-any.whl') {
				response.writeHead(404);
				response.end('not found');
				return true;
			}
		}
	});
	const result = await fixture.run();
	assert.notEqual(result.code, 0);
	assert.match(result.stderr, /Downloading wheel black-1.0.0-py3-none-any.whl failed/);
	assert.match(result.stderr, /HTTP 404/);
	assert.deepEqual(fixture.requests, ['/pypi/black/json', '/black-1.0.0-py3-none-any.whl']);
});

test('slim preparation still uses CDN entries and removes previously bundled wheels', async (t) => {
	const fixture = await prepareFixture(t);
	await writeFile(join(fixture.directory, 'static/pyodide/stale.whl'), 'old wheel');
	const result = await fixture.run(true);
	assert.equal(result.code, 0, result.stderr);
	assert.deepEqual(await fixture.operations(), []);
	assert.deepEqual(fixture.requests, []);
	const files = await readdir(join(fixture.directory, 'static/pyodide'));
	assert.equal(files.includes('stale.whl'), false);
	assert.equal(
		files.some((name) => name.endsWith('.tmp')),
		false
	);
	const lock = JSON.parse(
		await readFile(join(fixture.directory, 'static/pyodide/pyodide-lock.json'))
	);
	assert.equal(
		lock.packages.micropip.file_name,
		'https://cdn.jsdelivr.net/pyodide/v1.0.0/full/micropip.whl'
	);
});
