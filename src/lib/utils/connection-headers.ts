export type ConnectionHeaderOptions = {
	user_agent: string;
	enable_session_id: boolean;
	session_id_header: string;
};

// Editable examples, deliberately persisted as literal strings. Client versions and
// provider-specific identities vary; selecting a template must never enable other headers.
export const USER_AGENT_PRESETS = [
	{ id: 'claude-code', label: 'Claude Code', value: 'claude-cli/2.1.280 (external, cli)' },
	{ id: 'codex', label: 'Codex', value: 'codex_cli_rs/0.153.4 (Linux 6.8.0; x86_64)' },
	{
		id: 'gemini-cli',
		label: 'Gemini CLI',
		value: 'GeminiCLI/0.34.0/gemini-pro (linux; x64; terminal)'
	},
	{ id: 'opencode', label: 'OpenCode', value: 'opencode/1.0.0' },
	{ id: 'pi', label: 'Pi', value: 'pi (linux 6.8.0; x64)' },
	{
		id: 'openclaw',
		label: 'OpenClaw',
		value: 'openclaw/2026.8.2 (linux; node/v22.23.2; x64; gateway)'
	},
	{ id: 'hermes', label: 'Hermes', value: 'HermesAgent/0.11.0' }
];

export const SESSION_ID_HEADER_PRESETS = [
	'x-session-id',
	'session-id',
	'session_id',
	'x-session-affinity',
	'x-litellm-session-id',
	'x-opencode-session'
];

export const normalizeConnectionHeaderOptions = (
	config: Record<string, unknown> = {}
): ConnectionHeaderOptions => ({
	user_agent: typeof config.user_agent === 'string' ? config.user_agent : '',
	enable_session_id: config.enable_session_id === true,
	session_id_header:
		typeof config.session_id_header === 'string' ? config.session_id_header : 'x-session-id'
});

export const getUserAgentPreset = (value: string): string =>
	value ? (USER_AGENT_PRESETS.find((preset) => preset.value === value)?.id ?? 'custom') : 'default';

const RESERVED_SESSION_HEADERS = new Set([
	'authorization',
	'proxy-authorization',
	'cookie',
	'set-cookie',
	'host',
	'content-type',
	'content-length',
	'content-encoding',
	'transfer-encoding',
	'connection',
	'keep-alive',
	'upgrade',
	'te',
	'trailer',
	'expect',
	'user-agent',
	'api-key',
	'x-api-key',
	'x-goog-api-key',
	'anthropic-version',
	'http-referer',
	'x-title'
]);

export const validateConnectionHeaderOptions = (
	options: ConnectionHeaderOptions
): string | null => {
	// t('User-Agent must contain only printable ASCII characters (maximum 1024).')
	if (options.user_agent.length > 1024 || /[^\x20-\x7e]/.test(options.user_agent)) {
		return 'User-Agent must contain only printable ASCII characters (maximum 1024).';
	}
	if (options.enable_session_id) {
		const name = options.session_id_header;
		// t('Session ID header must be a valid HTTP header name (maximum 128 characters).')
		if (name.length > 128 || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name)) {
			return 'Session ID header must be a valid HTTP header name (maximum 128 characters).';
		}
		// t('Session ID header cannot replace authentication or standard request headers.')
		if (RESERVED_SESSION_HEADERS.has(name.toLowerCase())) {
			return 'Session ID header cannot replace authentication or standard request headers.';
		}
	}
	return null;
};

export const getTemporaryConnectionSessionId = (
	messages: Record<string, { id?: string; role?: string; parentId?: string | null }>,
	initialId?: string
): string | undefined =>
	initialId ??
	Object.values(messages).find((message) => message.role === 'user' && !message.parentId)?.id;
