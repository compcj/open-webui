export const CONNECTION_PROTOCOLS = [
	{ value: '', label: 'Chat Completions' },
	{ value: 'responses', label: 'Responses' },
	{ value: 'gemini', label: 'Gemini' },
	{ value: 'anthropic', label: 'Anthropic' }
];

export const normalizeConnectionProtocol = (value?: string) =>
	value === 'chat_completions' ? '' : (value ?? '');

export const isNativeConnectionProtocol = (value: string) =>
	value === 'gemini' || value === 'anthropic';

export const selectConnectionProtocol = (
	protocol: string,
	current: { url: string; auth_type: string },
	editing: boolean
) => {
	if (
		!isNativeConnectionProtocol(normalizeConnectionProtocol(protocol)) &&
		current.auth_type === 'api_key'
	) {
		return { ...current, auth_type: 'bearer' };
	}
	if (editing || current.url.trim() || !isNativeConnectionProtocol(protocol)) return current;
	return {
		url:
			protocol === 'gemini'
				? 'https://generativelanguage.googleapis.com/v1beta'
				: 'https://api.anthropic.com/v1',
		auth_type: 'api_key'
	};
};

export const connectionRequestRoute = (url: string, protocol?: string) => {
	const routes: Record<string, string> = {
		responses: 'responses',
		gemini: 'models/{model}:generateContent',
		anthropic: 'messages'
	};
	const route = routes[normalizeConnectionProtocol(protocol)] ?? 'chat/completions';
	return `${url.replace(/\/$/, '')}/${route}`;
};

// t('Native Gemini and Anthropic APIs cannot use Azure OpenAI.')
export const validateConnectionProtocol = (protocol: string, azure: boolean): string | null =>
	azure && isNativeConnectionProtocol(protocol)
		? 'Native Gemini and Anthropic APIs cannot use Azure OpenAI.'
		: null;

// Static extraction markers for the translated protocol options.
// t('Chat Completions')
// t('Responses')
// t('Gemini')
// t('Anthropic')
