import { setTimeout } from 'node:timers/promises';

function isTransientDownloadError(error) {
	for (let cause = error; cause; cause = cause.cause) {
		if (Number.isInteger(cause.status)) {
			return cause.status === 408 || cause.status === 429 || cause.status >= 500;
		}
		if (
			['AbortError', 'TimeoutError'].includes(cause.name) ||
			// Pyodide exposes the Python exception class as `type` on PythonError.
			['AbortError', 'TimeoutError'].includes(cause.type) ||
			/^(ECONNRESET|ECONNREFUSED|ETIMEDOUT|EAI_AGAIN|ENETUNREACH|EHOSTUNREACH|UND_ERR_(CONNECT_TIMEOUT|HEADERS_TIMEOUT|BODY_TIMEOUT|SOCKET))$/.test(
				cause.code ?? ''
			) ||
			/fetch failed|failed to fetch|network error|: request failed\./i.test(cause.message ?? '')
		) {
			return true;
		}
	}
	return false;
}

export async function withDownloadRetry(description, operation) {
	const attempts = 3;
	for (let attempt = 1; attempt <= attempts; attempt++) {
		try {
			return await operation();
		} catch (error) {
			if (!isTransientDownloadError(error) || attempt === attempts) {
				const exhausted = attempt === attempts ? ` after ${attempts} attempts` : '';
				throw new Error(`${description} failed${exhausted}`, { cause: error });
			}
			const delay = 1000 * 2 ** (attempt - 1);
			console.warn(`${description} failed; retrying in ${delay}ms (${attempt + 1}/${attempts})`);
			await setTimeout(delay);
		}
	}
}

export async function loadNativePackages(pyodide, names, packageIndex) {
	const required = new Set(names);
	for (const name of required) {
		for (const dependency of packageIndex[name]?.depends ?? []) required.add(dependency);
	}
	const missingPackages = () =>
		[...required].filter(
			(name) => pyodide.loadedPackages[packageIndex[name]?.name ?? name] === undefined
		);
	await withDownloadRetry(`Loading ${names.join(', ')}`, async () => {
		const missing = missingPackages();
		if (!missing.length) return;
		const errors = [];
		// loadPackage reports failed downloads via callbacks but resolves successfully.
		// Retry missing dependencies explicitly, even if their parent was installed.
		await pyodide.loadPackage(missing, { errorCallback: (message) => errors.push(message) });
		const remaining = missingPackages();
		if (remaining.length) {
			throw new Error(errors.join('\n') || `Pyodide did not load: ${remaining.join(', ')}`);
		}
	});
}

export async function fetchWithRetry(url, description, readBody) {
	return withDownloadRetry(description, async () => {
		const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
		if (!response.ok) {
			await response.body?.cancel();
			throw Object.assign(new Error(`HTTP ${response.status} ${response.statusText}`), {
				status: response.status
			});
		}
		// Reading the body can fail after the headers arrived. Retry the whole request.
		return await readBody(response);
	});
}
