import { get } from 'svelte/store';
import { config, settings, user } from '$lib/stores';
import { checkSourceSize, readSourceResponse } from './sourceText';
// The same attachment is used by @mentions, terminal uploads, and Files-pane requests.
export type FilesystemFile = {
	type: 'filesystem';
	id: string;
	path: string;
	url: string;
	name: string;
	status: string;
	size?: number;
	modified?: number;
	terminal_id?: string;
	session_id?: string | null;
};
export const filesystemFile = (
	entry: { path: string; name: string; size?: number; modified?: number },
	terminalId: string,
	sessionId: string | null
): FilesystemFile => ({
	type: 'filesystem',
	id: entry.path,
	path: entry.path,
	url: entry.path,
	name: entry.name,
	size: entry.size,
	modified: entry.modified,
	status: 'processed',
	terminal_id: terminalId,
	session_id: sessionId
});

export type FileEntry = {
	name: string;
	type: 'file' | 'directory';
	size?: number;
	modified?: number;
	created?: number | null;
	writable?: boolean;
};

export type TerminalFileList = {
	entries: FileEntry[];
	writable?: boolean;
};

export type TerminalFileSearchResult = FileEntry & {
	path: string;
};

export type TerminalFileSearchResponse = {
	results: TerminalFileSearchResult[];
};

export type TerminalContentMatch = {
	line: number;
	column: number;
	text: string;
};

export type TerminalFileMatch = {
	path: string;
	relative_path: string;
	name: string;
	type: 'file' | 'directory';
	name_match: boolean;
	content_matches: TerminalContentMatch[];
};

export type TerminalFileMatchesResponse = {
	results: TerminalFileMatch[];
	next_offset: number | null;
};

export type ListeningPort = {
	port: number;
	pid: number | null;
	process: string | null;
};

export type TerminalSystemInfo = {
	sampled_at: number;
	hostname: string;
	os: string;
	arch: string;
	environment: 'host' | 'container';
	uptime_seconds: number | null;
	service_uptime_seconds: number;
	cpu: { percent: number | null; cores: number | null; scope: 'host' | 'cgroup' };
	memory: { total: number; used: number; scope: 'host' | 'cgroup' } | null;
	disk: { path: string; total: number; used: number; free: number } | null;
	network: { name: string; up: boolean | null; addresses: string[] }[] | null;
	process_scope: 'user' | 'environment';
	processes:
		| {
				pid: number;
				name: string;
				cpu_percent: number | null;
				memory_bytes: number;
		  }[]
		| null;
	/** Present when the terminal has a setup script. */
	setup?: {
		status: 'running' | 'ok' | 'failed';
		exit_code?: number;
		log_tail?: string;
	};
};

export type TerminalFeatures = {
	agents?: boolean;
	terminal?: boolean;
	browser?: boolean;
	desktop?: boolean;
	git?: boolean;
	events?: boolean;
};

// Missing flags mean unsupported; never probe a new endpoint on an older server.
export async function getTerminalFeatures(
	target: Pick<TerminalTarget, 'baseUrl' | 'key' | 'context'>,
	signal?: AbortSignal
): Promise<TerminalFeatures> {
	try {
		const result = await terminalRequest<{ features?: TerminalFeatures }>(target, '/api/config', {
			signal
		});
		return result.features ?? {};
	} catch {
		return {};
	}
}

export type TerminalFileRoot = {
	path: string;
	label: string;
};

export type TerminalCwd = {
	cwd: string | null;
	home?: string;
	root?: TerminalFileRoot;
};

export type TerminalSkill = {
	id: string;
	name: string;
	description: string;
	is_active: boolean;
	source: 'terminal';
	terminal_path: string;
	terminal_selector: string;
	terminal_name?: string;
};

import { WEBUI_API_BASE_URL } from '$lib/constants';

// Captured once by the page/operation; never resolve a later account or scope at send time.
export type TerminalCredential =
	| string
	| {
			token: string;
			temporaryContext: { id: string; epoch: string };
			isCurrent: () => boolean;
	  };
export const terminalCredentialToken = (credential: TerminalCredential) =>
	typeof credential === 'string' ? credential : credential.token;
export type TerminalConnection = {
	selector: string;
	baseUrl: string;
	key: TerminalCredential;
	system: boolean;
	context?: TerminalTarget['context'];
};

export type TerminalTarget = {
	selector: string;
	baseUrl: string;
	key: TerminalCredential;
	system: boolean;
	/** userId names another person's instance; only someone who manages the terminal may reach it. */
	context: { userId?: string | null; chatId: string | null; modelId: string | null };
};
export type TerminalProcess = {
	id: string;
	command: string;
	status: 'running' | 'done' | 'killed';
	exit_code: number | null;
};
export type TerminalProcessOutput = TerminalProcess & {
	output: { type: string; data: string }[];
	next_offset: number;
	truncated: boolean;
};

const hasPersonalTerminalPermission = () => {
	const actor = get(user);
	return (
		actor?.role === 'admin' ||
		(actor?.role === 'user' && actor.permissions?.features?.direct_tool_servers === true)
	);
};

export const resolveTerminalTarget = (
	selector: string | null,
	servers: any[],
	token: TerminalCredential,
	context: Partial<TerminalTarget['context']> = {},
	directServers = get(settings)?.terminalServers ?? []
): TerminalTarget | null => {
	const features = get(config)?.features;
	const actorId = get(user)?.id;
	if (
		!features?.enable_tool_servers ||
		!selector ||
		(selector.startsWith('tether:') && !features.enable_tether)
	)
		return null;
	const server = servers.find((server) => server.id === selector);
	if (server) {
		if (server.scope === 'model' && !context.modelId) return null;
		return {
			selector,
			baseUrl: `${WEBUI_API_BASE_URL}/terminals/${encodeURIComponent(selector)}`,
			context: {
				userId: context.userId ?? null,
				chatId: context.chatId ?? null,
				modelId: server.scope === 'model' ? (context.modelId ?? null) : null
			},
			// Panels outlive Tether's token rotations; read the current session at request time.
			get key() {
				if (get(user)?.id !== actorId) return '';
				return typeof token !== 'string' || typeof localStorage === 'undefined'
					? token
					: (localStorage.getItem('token') ?? '');
			},
			system: true
		};
	}
	if (
		!features.enable_direct_integrations ||
		!hasPersonalTerminalPermission() ||
		selector.startsWith('tether:') ||
		selector.startsWith('template:')
	)
		return null;
	const direct = directServers.find((item) => item.url === selector && item.enabled !== false);
	return direct
		? {
				selector,
				baseUrl: direct.url.replace(/\/$/, ''),
				key: direct.key ?? '',
				system: false,
				context: { chatId: context.chatId ?? null, modelId: null }
			}
		: null;
};

export const resolveTerminalConnection = (
	selector: string | null,
	servers: any[],
	directServers: any[],
	token: TerminalCredential
) => resolveTerminalTarget(selector, servers, token, {}, directServers);

type RequestTarget = Pick<TerminalTarget, 'key' | 'baseUrl'> &
	Partial<Pick<TerminalTarget, 'context' | 'selector' | 'system'>>;

function legacyTarget(
	baseUrl: string,
	key: TerminalCredential,
	chatId?: string | null
): TerminalTarget {
	const api = new URL(WEBUI_API_BASE_URL, browserOrigin());
	const url = new URL(baseUrl, api.origin);
	const prefix = api.pathname.replace(/\/$/, '') + '/terminals/';
	const canonical = url.origin === api.origin && url.pathname.startsWith(prefix);
	const proxy = url.origin === api.origin && (canonical || baseUrl.startsWith('/'));
	const selector = canonical
		? decodeURIComponent(url.pathname.slice(prefix.length).split('/')[0])
		: baseUrl;
	return {
		baseUrl,
		key,
		selector,
		system: proxy,
		context: { chatId: chatId ?? null, modelId: null }
	};
}

// Older browser integrations pass the session separately. Both forms use the same checked target.
function withLegacyTerminalArgs<A extends unknown[], R>(
	operation: (target: TerminalTarget, ...args: A) => R,
	chatIndex: number
) {
	function call(target: TerminalTarget, ...args: A): R;
	function call(baseUrl: string, key: TerminalCredential, ...args: unknown[]): R;
	function call(target: TerminalTarget | string, ...args: unknown[]): R {
		if (typeof target === 'string') {
			const key = args.shift() as TerminalCredential;
			const [chatId] = args.splice(chatIndex, 1) as [string | undefined];
			target = legacyTarget(target, key, chatId);
		}
		return operation(target, ...(args as A));
	}
	return call;
}

export class TerminalRequestError extends Error {
	constructor(
		message: string,
		public status: number
	) {
		super(message);
		this.name = 'TerminalRequestError';
	}
}

export const terminalRequest = async <T>(
	target: RequestTarget,
	pathOrChat: string | null,
	optionsOrPath: RequestInit | string = {},
	legacyOptions: RequestInit = {}
): Promise<T> => {
	const legacy = typeof optionsOrPath === 'string';
	const path = legacy ? optionsOrPath : pathOrChat;
	const options = legacy ? legacyOptions : optionsOrPath;
	const cleanup = options.method === 'DELETE' && /^\/api\/terminals\/[^/]+$/.test(path ?? '');
	if (legacy)
		target = {
			...target,
			context: { ...target.context, chatId: pathOrChat, modelId: target.context?.modelId ?? null }
		};
	const response = await fetch(`${target.baseUrl}${path}`, {
		...options,
		headers: {
			...terminalHeaders(target, cleanup),
			...options.headers
		}
	});
	if (!response.ok) {
		const body = await response.json().catch(() => null);
		throw new TerminalRequestError(
			typeof body?.detail === 'string'
				? body.detail
				: typeof body?.error === 'string'
					? body.error
					: `Terminal request failed (${response.status})`,
			response.status
		);
	}
	return response.json();
};

export const terminalHeaders = (
	target: Pick<RequestTarget, 'key' | 'context' | 'system' | 'selector'>,
	cleanup = false
): Record<string, string> => {
	const features = get(config)?.features;
	if (!cleanup && !features?.enable_tool_servers)
		throw new TerminalRequestError('Tool servers are disabled', 403);
	if (
		!cleanup &&
		target.system === false &&
		(!features?.enable_direct_integrations || !hasPersonalTerminalPermission())
	)
		throw new TerminalRequestError('Direct integrations are disabled', 403);
	if (!cleanup && target.selector?.startsWith('tether:') && !features?.enable_tether)
		throw new TerminalRequestError('Tether is disabled', 403);
	return {
		...bearerHeaders(target.key, cleanup),
		...(target.context?.userId ? { 'X-Terminal-User-Id': target.context.userId } : {}),
		...(target.context?.chatId ? { 'X-Session-Id': target.context.chatId } : {}),
		...(target.context?.modelId ? { 'X-Terminal-Model-Id': target.context.modelId } : {})
	};
};

export const terminalConnectionHeaders = terminalHeaders;

const bearerHeaders = (apiKey: TerminalCredential, cleanup = false): Record<string, string> => {
	if (!cleanup && !get(config)?.features?.enable_tool_servers)
		throw new Error('Tool servers are disabled');
	if (!cleanup && typeof apiKey !== 'string' && !apiKey.isCurrent())
		throw new Error('Terminal context changed. Retry from the current chat.');
	return {
		Authorization: `Bearer ${terminalCredentialToken(apiKey).trim()}`,
		...(typeof apiKey === 'string'
			? {}
			: {
					'X-Temporary-Context-Id': apiKey.temporaryContext.id,
					'X-Temporary-Context-Epoch': apiKey.temporaryContext.epoch
				})
	};
};

export type TerminalFileDelivery = {
	path: string;
	exists: boolean;
	owner_id: string;
	download_url: string;
	image_url?: string;
};

const browserOrigin = () =>
	typeof window === 'undefined' ? 'http://localhost:3000' : window.location.origin;

export const validateDeliveryUrl = (
	value: unknown,
	baseUrl: string,
	kind: 'download' | 'image'
): string | null => {
	if (typeof value !== 'string' || !value) return null;
	try {
		const origin = browserOrigin();
		const base = new URL(baseUrl, origin);
		const candidate = new URL(value, origin);
		// Display requests may carry chat context in the route; signed delivery
		// always uses the exact same server's root endpoint.
		const basePath = base.pathname.replace(/\/$/, '');
		const systemChatRoot = basePath.match(/^(.*\/api\/v1\/terminals\/[^/]+)\/chats\/[^/]+$/)?.[1];
		const expectedPath = `${systemChatRoot ?? basePath}/files/${kind}`;
		if (
			candidate.origin !== base.origin ||
			candidate.origin !== origin ||
			candidate.username ||
			candidate.password ||
			candidate.pathname !== expectedPath ||
			candidate.hash ||
			[...candidate.searchParams.keys()].some((key) => key !== 'ref') ||
			candidate.searchParams.getAll('ref').length !== 1 ||
			!candidate.searchParams.get('ref')
		) {
			return null;
		}
		return candidate.toString();
	} catch {
		return null;
	}
};

export const getFileDelivery = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string,
		signal?: AbortSignal
	): Promise<TerminalFileDelivery | null> => {
		const baseUrl = target.baseUrl;
		const url = baseUrl.replace(/\/$/, '') + '/files/display?path=' + encodeURIComponent(path);
		const headers = terminalHeaders(target);
		const res = await fetch(url, { headers, ...(signal ? { signal } : {}) }).catch(() => null);
		if (!res?.ok) return null;
		const result = await res.json().catch(() => null);
		if (
			!result ||
			result.exists !== true ||
			typeof result.path !== 'string' ||
			typeof result.owner_id !== 'string'
		) {
			return null;
		}
		const downloadUrl = validateDeliveryUrl(result.download_url, baseUrl, 'download');
		if (!downloadUrl) return null;
		const imageUrl = result.image_url
			? validateDeliveryUrl(result.image_url, baseUrl, 'image')
			: undefined;
		if (result.image_url && !imageUrl) return null;
		return { ...result, download_url: downloadUrl, ...(imageUrl ? { image_url: imageUrl } : {}) };
	},
	1
);

const deliveryFilename = (response: Response) => {
	const disposition = response.headers.get('content-disposition') ?? '';
	const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
	if (encoded) {
		try {
			return decodeURIComponent(encoded.replace(/^"|"$/g, ''));
		} catch {
			// Fall through to the plain filename form.
		}
	}
	return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? 'file';
};

export function downloadDeliveryFile(
	url: string,
	target: TerminalTarget,
	signal?: AbortSignal
): Promise<{ blob: Blob; filename: string } | null>;
export function downloadDeliveryFile(
	url: string,
	baseUrl: string,
	key: TerminalCredential,
	chatId?: string,
	signal?: AbortSignal
): Promise<{ blob: Blob; filename: string } | null>;
export async function downloadDeliveryFile(
	url: string,
	connection: TerminalTarget | string,
	credentialOrSignal?: TerminalCredential | AbortSignal,
	chatId?: string,
	legacySignal?: AbortSignal
) {
	const target =
		typeof connection === 'string'
			? legacyTarget(connection, credentialOrSignal as TerminalCredential, chatId)
			: connection;
	const signal =
		typeof connection === 'string' ? legacySignal : (credentialOrSignal as AbortSignal | undefined);
	const validated = validateDeliveryUrl(url, target.baseUrl, 'download');
	if (!validated) return null;
	const headers = terminalHeaders(target);
	const res = await fetch(validated, { headers, ...(signal ? { signal } : {}) }).catch(() => null);
	if (!res?.ok) return null;
	const blob = await res.blob().catch(() => null);
	return blob ? { blob, filename: deliveryFilename(res) } : null;
}

export const joinTerminalPath = (base: string, child: string) => {
	if (!child) return base;
	if (child.startsWith('/') || /^[A-Za-z]:[\\/]/.test(child)) return child;
	return `${base.replace(/[\\/]+$/, '')}/${child.replace(/^[\\/]+/, '')}`;
};

const basename = (path: string) =>
	path.replace(/\\/g, '/').split('/').filter(Boolean).at(-1) ?? path;

const hasHiddenPathPart = (path: string) =>
	path
		.replace(/\\/g, '/')
		.split('/')
		.some((part) => part.startsWith('.'));

export type TerminalServer = {
	contexts?: Record<string, false | { context_id?: string }>;
	template_id?: string;
	scope?: 'user' | 'chat' | 'model' | null;
	id: string;
	url: string;
	name: string;
	config?: {
		chat_uploads?: 'default' | 'filesystem';
		[key: string]: unknown;
	};
};

export const getTerminalServers = async (token: string): Promise<TerminalServer[]> => {
	if (!get(config)?.features?.enable_tool_servers) return [];
	const res = await fetch(`${WEBUI_API_BASE_URL}/terminals/`, {
		headers: {
			Authorization: `Bearer ${token}`
		}
	}).catch(() => null);
	if (!res || !res.ok) return [];
	const servers: TerminalServer[] = await res.json().catch(() => []);
	return servers.filter(
		(server) => !server.id?.startsWith('tether:') || get(config)?.features?.enable_tether
	);
};

export const getTerminalConfig = async (
	baseUrl: string,
	apiKey: TerminalCredential
): Promise<{ features: TerminalFeatures } | null> => {
	const url = `${baseUrl.replace(/\/$/, '')}/api/config`;
	const res = await fetch(url, {
		headers: bearerHeaders(apiKey)
	}).catch(() => null);
	if (!res || !res.ok) return null;
	return res.json().catch(() => null);
};

export const getCwd = withLegacyTerminalArgs(
	async (target: TerminalTarget): Promise<TerminalCwd | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/cwd`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, { headers }).catch(() => null);
		if (!res || !res.ok) return null;
		const json = await res.json().catch(() => null);
		if (!json) return null;
		return {
			cwd: json?.cwd ?? null,
			home: json?.home,
			root: json?.root
		};
	},
	0
);

export const listFiles = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string = '/',
		signal?: AbortSignal
	): Promise<TerminalFileList | null> => {
		// The endpoint uses `directory` as the query param name
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/list?directory=${encodeURIComponent(path)}`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, { headers, ...(signal ? { signal } : {}) })
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				if (err?.name !== 'AbortError') console.error('open-terminal listFiles error:', err);
				return null;
			});
		return res?.entries ? { entries: res.entries, writable: res.writable } : null;
	},
	1
);

export const searchFiles = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		query: string,
		path: string = '.',
		limit: number = 20,
		type: 'file' | 'directory' | 'any' = 'any',
		showHidden: boolean = false,
		signal?: AbortSignal
	): Promise<TerminalFileSearchResponse | null> => {
		const headers: Record<string, string> = terminalHeaders(target);

		const searchParams = new URLSearchParams({
			query,
			path,
			limit: String(limit),
			type,
			show_hidden: String(showHidden)
		});
		const base = target.baseUrl.replace(/\/$/, '');
		const searchRes = await fetch(`${base}/files/search?${searchParams.toString()}`, {
			headers,
			signal
		}).catch(() => null);

		if (searchRes?.ok) {
			const json = await searchRes.json().catch(() => null);
			if (Array.isArray(json?.results)) return { results: json.results };
		}

		if (signal?.aborted) return null;

		const globParams = new URLSearchParams({
			pattern: query.trim() ? `*${query.trim()}*` : '*',
			path,
			type,
			max_results: String(limit)
		});
		const globRes = await fetch(`${base}/files/glob?${globParams.toString()}`, {
			headers,
			signal
		}).catch((err) => {
			if (!signal?.aborted) console.error('open-terminal searchFiles error:', err);
			return null;
		});
		if (!globRes?.ok) return null;

		const json = await globRes.json().catch(() => null);
		const root = json?.path ?? path;
		return {
			results: (json?.matches ?? [])
				.filter((item: FileEntry & { path: string }) => showHidden || !hasHiddenPathPart(item.path))
				.map((item: FileEntry & { path: string }) => ({
					path: joinTerminalPath(root, item.path),
					name: basename(item.path),
					type: item.type,
					size: item.size,
					modified: item.modified
				}))
		};
	},
	4
);

export const getFileMatches = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		query: string,
		path: string = '.',
		showHidden: boolean = false,
		offset: number = 0,
		signal?: AbortSignal
	): Promise<TerminalFileMatchesResponse | null> => {
		const headers: Record<string, string> = terminalHeaders(target);

		const params = new URLSearchParams({
			query,
			path,
			show_hidden: String(showHidden),
			offset: String(offset)
		});
		const res = await fetch(
			`${target.baseUrl.replace(/\/$/, '')}/files/matches?${params.toString()}`,
			{
				headers,
				signal
			}
		).catch((err) => {
			if (err?.name !== 'AbortError') console.error('open-terminal getFileMatches error:', err);
			return null;
		});
		if (!res?.ok) return null;
		const json = await res.json().catch(() => null);
		return Array.isArray(json?.results) ? json : null;
	},
	4
);

export const readComparisonSource = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string,
		signal: AbortSignal,
		size?: number
	): Promise<string> => {
		checkSourceSize(size);
		const headers = terminalHeaders(target);

		const response = await fetch(
			`${target.baseUrl.replace(/\/$/, '')}/files/view?path=${encodeURIComponent(path)}`,
			{ headers, signal }
		).catch(() => {
			signal.throwIfAborted();
			throw new Error('Could not read file. Check your terminal connection.');
		});
		return readSourceResponse(response, signal);
	},
	2
);

export const readFile = withLegacyTerminalArgs(
	async (target: TerminalTarget, path: string, signal?: AbortSignal): Promise<string | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/read?path=${encodeURIComponent(path)}`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, { headers, ...(signal ? { signal } : {}) }).catch((err) => {
			if (err?.name !== 'AbortError') console.error('open-terminal readFile error:', err);
			return null;
		});

		if (!res || !res.ok) return null;

		const contentType = res.headers.get('content-type') ?? '';
		if (contentType.startsWith('image/') || contentType.startsWith('application/octet')) {
			// Binary — return a placeholder
			return `[Binary file: ${contentType}]`;
		}

		// Text files: endpoint returns JSON { path, total_lines, content }
		// Binary image files: endpoint returns raw bytes (handled above)
		const json = await res.json().catch(() => null);
		return json?.content ?? null;
	},
	1
);

export const listTerminalSkills = async (
	target: TerminalTarget | null
): Promise<TerminalSkill[]> => {
	if (!target) return [];

	const skills = await terminalRequest<TerminalSkill[]>(target, '/skills').catch(() => []);

	return (Array.isArray(skills) ? skills : []).map((skill) => ({
		...skill,
		is_active: true,
		source: 'terminal',
		terminal_path: skill.terminal_path ?? (skill as any).location ?? '',
		terminal_selector: target.selector,
		terminal_name: target.selector
	}));
};

export const downloadFileBlob = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string,
		signal?: AbortSignal
	): Promise<{ blob: Blob; filename: string } | null> => {
		if (target.system && target.baseUrl.includes('/api/v1/terminals/')) {
			const delivery = await getFileDelivery(target, path, signal);
			if (!delivery) return null;
			return downloadDeliveryFile(delivery.download_url, target, signal);
		}
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/view?path=${encodeURIComponent(path)}`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, { headers, signal }).catch(() => null);

		if (!res || !res.ok) return null;

		const filename = path.split('/').pop() ?? 'file';
		const blob = await res.blob().catch(() => null);
		if (!blob) return null;
		return { blob, filename };
	},
	1
);

export const downloadFilePreview = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string,
		signal?: AbortSignal
	): Promise<{ blob: Blob; filename: string } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/view?path=${encodeURIComponent(path)}&preview=true`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, { headers, signal }).catch(() => null);

		if (!res) return null;
		if (!res.ok) return null;

		const filename = path.split('/').pop() ?? 'file';
		const blob = await res.blob().catch(() => null);
		if (!blob) return null;
		const { hasPdfSignature } = await import('$lib/utils/terminal-files');
		if (!hasPdfSignature(await blob.arrayBuffer())) return null;
		return { blob, filename };
	},
	1
);

export const archiveFromTerminal = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		paths: string[]
	): Promise<{ blob: Blob; filename: string } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/archive`;
		const headers: Record<string, string> = {
			...terminalHeaders(target),
			'Content-Type': 'application/json'
		};

		const res = await fetch(url, {
			method: 'POST',
			headers,
			body: JSON.stringify({ paths })
		}).catch(() => null);

		if (!res || !res.ok) return null;

		const disposition = res.headers.get('content-disposition') ?? '';
		const match = disposition.match(/filename="?([^"]+)"?/);
		const filename = match?.[1] ?? 'download.zip';
		const blob = await res.blob().catch(() => null);
		if (!blob) return null;
		return { blob, filename };
	},
	1
);

export const uploadToTerminal = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		directory: string,
		file: File
	): Promise<{ path: string; size: number } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/upload?directory=${encodeURIComponent(directory)}`;
		const body = new FormData();
		body.append('file', file);
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, {
			method: 'POST',
			headers,
			body
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal uploadToTerminal error:', err);
				return null;
			});
		return res;
	},
	2
);

// ponytail: serialize new uploads in this page; cross-client races need server-side protection.
let newFileUploadQueue: Promise<void> = Promise.resolve();

export const uploadNewFileToTerminal = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		directory: string,
		file: File
	): Promise<{ path: string; size: number } | null> => {
		const upload = newFileUploadQueue.then(async () => {
			const listing = await listFiles(target, directory);
			if (!listing) return null;

			const names = new Set(listing.entries.map((entry) => entry.name));
			const extensionIndex = file.name.startsWith('.') ? -1 : file.name.lastIndexOf('.');
			const stem = extensionIndex > 0 ? file.name.slice(0, extensionIndex) : file.name;
			const extension = extensionIndex > 0 ? file.name.slice(extensionIndex) : '';
			let name = file.name;
			for (let suffix = 1; names.has(name); suffix++) {
				name = `${stem} (${suffix})${extension}`;
			}

			const uploadFile =
				name === file.name
					? file
					: new File([file], name, { type: file.type, lastModified: file.lastModified });
			return uploadToTerminal(target, directory, uploadFile);
		});
		newFileUploadQueue = upload.then(
			() => undefined,
			() => undefined
		);
		return upload;
	},
	2
);

export const createDirectory = withLegacyTerminalArgs(
	async (target: TerminalTarget, path: string): Promise<{ path: string } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/mkdir`;
		const headers: Record<string, string> = {
			...terminalHeaders(target),
			'Content-Type': 'application/json'
		};

		const res = await fetch(url, {
			method: 'POST',
			headers,
			body: JSON.stringify({ path })
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal createDirectory error:', err);
				return null;
			});
		return res;
	},
	1
);

export const deleteEntry = withLegacyTerminalArgs(
	async (target: TerminalTarget, path: string): Promise<{ path: string; type: string } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/delete?path=${encodeURIComponent(path)}`;
		const headers: Record<string, string> = terminalHeaders(target);

		const res = await fetch(url, {
			method: 'DELETE',
			headers
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal deleteEntry error:', err);
				return null;
			});
		return res;
	},
	1
);

export const setCwd = withLegacyTerminalArgs(
	async (target: TerminalTarget, path: string): Promise<{ cwd: string } | null> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/cwd`;
		const headers: Record<string, string> = {
			...terminalHeaders(target),
			'Content-Type': 'application/json'
		};

		const res = await fetch(url, {
			method: 'POST',
			headers,
			body: JSON.stringify({ path })
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal setCwd error:', err);
				return null;
			});
		return res;
	},
	1
);

export const moveEntry = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		source: string,
		destination: string
	): Promise<{ source: string; destination: string } | { error: string }> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/files/move`;
		const headers: Record<string, string> = {
			...terminalHeaders(target),
			'Content-Type': 'application/json'
		};

		const res = await fetch(url, {
			method: 'POST',
			headers,
			body: JSON.stringify({ source, destination })
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal moveEntry error:', err);
				return { error: err?.detail ?? 'Move failed' };
			});
		return res;
	},
	2
);

export const getListeningPorts = withLegacyTerminalArgs(
	async (target: TerminalTarget): Promise<ListeningPort[]> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/ports`;
		const res = await fetch(url, {
			headers: {
				...terminalHeaders(target)
			}
		}).catch(() => null);
		if (!res || !res.ok) return [];
		const json = await res.json().catch(() => null);
		return json?.ports ?? [];
	},
	0
);

// Notebook execution

export const createNotebookSession = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		path: string
	): Promise<{ id: string; kernel: string; status: string } | { error: string }> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/notebooks`;
		const res = await fetch(url, {
			method: 'POST',
			headers: {
				...terminalHeaders(target),
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({ path })
		})
			.then(async (res) => {
				if (!res.ok) {
					const body = await res.json().catch(() => ({}));
					return { error: body?.detail ?? `HTTP ${res.status}` };
				}
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal createNotebookSession error:', err);
				return { error: 'Connection failed' };
			});
		return res;
	},
	1
);

export const executeNotebookCell = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		sessionId: string,
		cellIndex: number,
		source?: string,
		chatId?: string
	): Promise<{ status: string; execution_count?: number; outputs: any[] } | { error: string }> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/notebooks/${sessionId}/execute`;
		const body: Record<string, any> = { cell_index: cellIndex };
		if (source !== undefined) body.source = source;

		const res = await fetch(url, {
			method: 'POST',
			headers: {
				...terminalHeaders(target),
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(body)
		})
			.then(async (res) => {
				if (!res.ok) {
					const body = await res.json().catch(() => ({}));
					return { error: body?.detail ?? `HTTP ${res.status}` };
				}
				return res.json();
			})
			.catch((err) => {
				console.error('open-terminal executeNotebookCell error:', err);
				return { error: 'Connection failed' };
			});
		return res;
	},
	3
);

export const stopNotebookSession = withLegacyTerminalArgs(
	async (target: TerminalTarget, sessionId: string): Promise<boolean> => {
		const url = `${target.baseUrl.replace(/\/$/, '')}/notebooks/${sessionId}`;
		const res = await fetch(url, {
			method: 'DELETE',
			headers: { ...terminalHeaders(target, true) }
		}).catch(() => null);
		return res?.ok ?? false;
	},
	1
);

export type TerminalDiffLine = {
	type: 'added' | 'removed' | 'context';
	oldNumber: number | null;
	newNumber: number | null;
	content: string;
	revisedContent?: string;
	segments: { text: string; changed: boolean }[];
};
export type TerminalComparisonRequest = {
	original: string;
	revised: string;
	ignore_whitespace: boolean;
};
export type TerminalComparison = {
	original: { name: string; path: string; notices: string[] };
	revised: { name: string; path: string; notices: string[] };
	additions: number;
	deletions: number;
	hunks: { header: string; lines: TerminalDiffLine[] }[];
};

export const compareFiles = withLegacyTerminalArgs(
	async (
		target: TerminalTarget,
		request: TerminalComparisonRequest,
		signal?: AbortSignal
	): Promise<TerminalComparison> => {
		const response = await fetch(`${target.baseUrl.replace(/\/$/, '')}/files/compare`, {
			method: 'POST',
			signal,
			headers: {
				...terminalHeaders(target),
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(request)
		});
		const body = await response.json().catch(() => null);
		if (
			response.status === 405 ||
			(response.status === 404 && ((!body?.detail && !body?.error) || body.detail === 'Not Found'))
		) {
			throw new Error(
				'File comparison is not available on this terminal. Update Open Terminal to use Compare.'
			);
		}
		if (!response.ok)
			throw new Error(
				typeof body?.detail === 'string'
					? body.detail
					: (body?.error ?? `Comparison failed (${response.status})`)
			);
		return body;
	},
	1
);

export async function templatesApi(path = '', method = 'GET', body?: unknown) {
	const response = await fetch(`${WEBUI_API_BASE_URL}/terminals/templates${path}`, {
		method,
		headers: {
			Authorization: `Bearer ${localStorage.token}`,
			...(body !== undefined ? { 'Content-Type': 'application/json' } : {})
		},
		...(body !== undefined ? { body: JSON.stringify(body) } : {})
	});
	const data = await response.json().catch(() => null);
	if (!response.ok)
		throw new Error(typeof data?.detail === 'string' ? data.detail : 'Terminal request failed');
	return data;
}
