import { describe, expect, it } from 'vitest';
import {
	CONNECTION_PROTOCOLS,
	normalizeConnectionProtocol,
	selectConnectionProtocol,
	connectionRequestRoute,
	validateConnectionProtocol
} from './connection-protocol';

describe('administrator connection protocols', () => {
	it('offers four explicit protocols with legacy chat completions normalized', () => {
		expect(CONNECTION_PROTOCOLS.map(({ value }) => value)).toEqual([
			'',
			'responses',
			'gemini',
			'anthropic'
		]);
		expect(normalizeConnectionProtocol('chat_completions')).toBe('');
	});
	it.each([
		['gemini', 'https://generativelanguage.googleapis.com/v1beta'],
		['anthropic', 'https://api.anthropic.com/v1']
	])('defaults a new blank %s connection to its native URL and API key auth', (protocol, url) => {
		expect(selectConnectionProtocol(protocol, { url: '', auth_type: 'bearer' }, false)).toEqual({
			url,
			auth_type: 'api_key'
		});
	});
	it('preserves custom URLs, gateway authentication and loaded blank connections', () => {
		for (const auth_type of ['none', 'bearer', 'session', 'system_oauth', 'api_key']) {
			const current = { url: 'https://gateway.example/custom', auth_type };
			expect(selectConnectionProtocol('gemini', current, false)).toEqual(current);
			expect(selectConnectionProtocol('anthropic', { ...current, url: '' }, true)).toEqual({
				...current,
				url: ''
			});
		}
	});
	it.each(['', 'responses', 'chat_completions'])(
		'uses bearer when leaving native API key auth for %s',
		(protocol) => {
			const current = { url: 'https://custom.example/native', auth_type: 'api_key' };
			expect(selectConnectionProtocol(protocol, current, true)).toEqual({
				...current,
				auth_type: 'bearer'
			});
			for (const auth_type of ['none', 'bearer', 'session', 'system_oauth']) {
				expect(selectConnectionProtocol(protocol, { ...current, auth_type }, true)).toEqual({
					...current,
					auth_type
				});
			}
		}
	);
	it.each([
		['', 'chat/completions'],
		['chat_completions', 'chat/completions'],
		['responses', 'responses'],
		['gemini', 'models/{model}:generateContent'],
		['anthropic', 'messages']
	])('routes %s by selected protocol', (protocol, route) => {
		expect(connectionRequestRoute('https://custom.example/v1/', protocol)).toBe(
			`https://custom.example/v1/${route}`
		);
	});
	it('rejects native Azure combinations explicitly', () => {
		expect(validateConnectionProtocol('gemini', true)).toBe(
			'Native Gemini and Anthropic APIs cannot use Azure OpenAI.'
		);
		expect(validateConnectionProtocol('anthropic', true)).toBeTruthy();
		expect(validateConnectionProtocol('responses', true)).toBeNull();
	});
});
