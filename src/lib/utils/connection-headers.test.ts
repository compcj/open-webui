import { describe, expect, it } from 'vitest';
import {
	USER_AGENT_PRESETS,
	getUserAgentPreset,
	normalizeConnectionHeaderOptions,
	validateConnectionHeaderOptions,
	getTemporaryConnectionSessionId
} from './connection-headers';

describe('connection header options', () => {
	it('keeps existing connections on their original defaults', () => {
		expect(normalizeConnectionHeaderOptions({ headers: { 'User-Agent': 'Legacy/1.0' } })).toEqual({
			user_agent: '',
			enable_session_id: false,
			session_id_header: 'x-session-id'
		});
	});

	it('round trips literal overrides without changing them to preset versions', () => {
		const saved = {
			user_agent: 'my-client/99.0 (anything)',
			enable_session_id: true,
			session_id_header: 'X-My-Session'
		};
		expect(normalizeConnectionHeaderOptions(JSON.parse(JSON.stringify(saved)))).toEqual(saved);
		expect(getUserAgentPreset(saved.user_agent)).toBe('custom');
		expect(validateConnectionHeaderOptions(saved)).toBeNull();
	});

	it('provides all requested AI client templates and lets users customize them', () => {
		expect(USER_AGENT_PRESETS.map((preset) => preset.id)).toEqual([
			'claude-code',
			'codex',
			'gemini-cli',
			'opencode',
			'pi',
			'openclaw',
			'hermes'
		]);
		for (const preset of USER_AGENT_PRESETS) {
			expect(getUserAgentPreset(preset.value)).toBe(preset.id);
			expect(getUserAgentPreset(`${preset.value} custom`)).toBe('custom');
			expect(
				validateConnectionHeaderOptions({
					...normalizeConnectionHeaderOptions(),
					user_agent: preset.value
				})
			).toBeNull();
		}
		expect(getUserAgentPreset('')).toBe('default');
	});

	it.each([
		'Authorization',
		'x-goog-api-key',
		'X-Goog-Api-Key',
		'anthropic-version',
		'Anthropic-Version',
		'content-length',
		'User-Agent',
		'HTTP-Referer',
		'Bad Header',
		'X-Test\r\nOther'
	])('rejects unsafe session header %s', (name) => {
		expect(
			validateConnectionHeaderOptions({
				...normalizeConnectionHeaderOptions(),
				enable_session_id: true,
				session_id_header: name
			})
		).not.toBeNull();
	});

	it('allows custom header tokens and ignores inactive session settings', () => {
		const options = {
			...normalizeConnectionHeaderOptions(),
			enable_session_id: true,
			session_id_header: 'x-provider_session'
		};
		expect(validateConnectionHeaderOptions(options)).toBeNull();
		expect(validateConnectionHeaderOptions({ ...options, session_id_header: '' })).not.toBeNull();
		expect(
			validateConnectionHeaderOptions({
				...options,
				enable_session_id: false,
				session_id_header: ''
			})
		).toBeNull();
	});

	it.each(['bad\r\nvalue', 'bad\0value', 'a'.repeat(1025)])(
		'rejects invalid User-Agent values',
		(user_agent) => {
			expect(
				validateConnectionHeaderOptions({ ...normalizeConnectionHeaderOptions(), user_agent })
			).not.toBeNull();
		}
	);
});

describe('temporary connection session identity', () => {
	it('uses the first root user message across turns and branch changes', () => {
		const messages = {
			system: { id: 'system', role: 'system' },
			first: { id: 'root-a', role: 'user', parentId: null },
			answer: { id: 'answer', role: 'assistant', parentId: 'root-a' },
			next: { id: 'follow-up', role: 'user', parentId: 'answer' },
			branch: { id: 'root-branch', role: 'user', parentId: null }
		};
		expect(getTemporaryConnectionSessionId(messages)).toBe('root-a');
		expect(
			getTemporaryConnectionSessionId({ other: { id: 'root-b', role: 'user', parentId: null } })
		).toBe('root-b');
		expect(getTemporaryConnectionSessionId({})).toBeUndefined();
	});

	it('keeps the initial identity after the original root is deleted', () => {
		const remainingMessages = {
			followup: { id: 'followup-b', role: 'user', parentId: null }
		};
		expect(getTemporaryConnectionSessionId(remainingMessages, 'root-a')).toBe('root-a');
		expect(getTemporaryConnectionSessionId({}, 'root-a')).toBe('root-a');
		// A new conversation has no retained identity.
		expect(getTemporaryConnectionSessionId(remainingMessages)).toBe('followup-b');
	});
});
