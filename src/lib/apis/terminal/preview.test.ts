import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { browserFrameUrl } from './browser';
import type { TerminalConnection } from './index';
import { previewTicketToken, requestPreviewTicket, validatePreviewTicket } from './preview';

type PreviewLifecycle = {
	manage: (
		active: boolean,
		connection: TerminalConnection,
		session: string,
		chat: string,
		base: string,
		actor: string,
		port: number
	) => void;
	getFrame: () => string;
	stop: () => void;
};

function previewLifecycle(request: typeof requestPreviewTicket): PreviewLifecycle {
	const source = readFileSync(
		new URL('../../components/chat/ChatTabs/Browser.svelte', import.meta.url),
		'utf8'
	);
	const declaration = parse(source)
		.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
		.find((node: any) => node.id?.name === 'managePreview');
	return new Function(
		'requestPreviewTicket',
		'browserFrameUrl',
		'previewTicketToken',
		transpileModule(
			`let stopPreview, previewTicket, frameUrl, frameReady, previewError, loading;
			let previewIdentity = null, previewConnection = null;
			const currentUrl = 'http://localhost:5173/';
			const ${source.slice(declaration.start, declaration.end)};
			return {
				manage: managePreview,
				getFrame: () => frameUrl,
				stop: () => stopPreview?.()
			};`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(request, browserFrameUrl, previewTicketToken);
}

describe('terminal preview ticket', () => {
	const app = 'https://webui.example.test';
	const preview = 'https://preview.example.test';
	const now = 1000;
	const connection = {
		selector: 'terminal',
		baseUrl: '/api/v1/terminals/terminal',
		key: 'test-token',
		system: true
	};

	it('accepts a short-lived capability on the configured preview host', () => {
		const ticket = { url: preview + '/p/opaque', expires_at: now + 300 };
		expect(validatePreviewTicket(ticket, preview, app, now)).toEqual(ticket);
	});

	it.each([
		app + '/p/opaque',
		'https://other.example.test/p/opaque',
		'https://webui.example.test:8443/p/opaque',
		'https://user:secret@preview.example.test/p/opaque'
	])('rejects a capability for an untrusted origin', (url) => {
		expect(() =>
			validatePreviewTicket({ url, expires_at: now + 300 }, preview, app, now)
		).toThrow();
	});

	it('rejects expired capabilities', () => {
		expect(() =>
			validatePreviewTicket({ url: preview + '/p/opaque', expires_at: now }, preview, app, now)
		).toThrow();
	});

	it('requests a port ticket for the Browser tab session', () => {
		const request = vi.fn().mockRejectedValue(new Error('Preview response is outside this test'));
		previewLifecycle(request).manage(
			true,
			connection,
			'browser-session',
			'chat',
			preview,
			'actor',
			5173
		);
		expect(request).toHaveBeenCalledWith(
			connection,
			'chat',
			{ kind: 'port', port: 5173, session_id: 'browser-session' },
			preview,
			expect.any(AbortSignal),
			undefined
		);
	});

	it('preserves the frame across equivalent connections and renews with the current credential', async () => {
		vi.useFakeTimers();
		const url = preview + '/p/opaque';
		const request = vi.fn().mockResolvedValue({ url, expires_at: Date.now() / 1000 + 300 });
		const lifecycle = previewLifecycle(request);
		try {
			lifecycle.manage(true, connection, 'session', 'chat', preview, 'actor', 5173);
			await vi.waitFor(() =>
				expect(lifecycle.getFrame()).toBe(browserFrameUrl(url, 'http://localhost:5173/'))
			);
			const frame = lifecycle.getFrame();
			const refreshedConnection = { ...connection, key: 'refreshed-token' };
			lifecycle.manage(true, refreshedConnection, 'session', 'chat', preview, 'actor', 5173);
			expect(lifecycle.getFrame()).toBe(frame);
			expect(request).toHaveBeenCalledTimes(1);
			expect(request.mock.calls[0][4].aborted).toBe(false);

			await vi.advanceTimersByTimeAsync(60_000);
			expect(request).toHaveBeenCalledTimes(2);
			expect(request.mock.calls[1][0]).toBe(refreshedConnection);
			expect(request.mock.calls[1][5]).toBe(url);
			expect(lifecycle.getFrame()).toBe(frame);
		} finally {
			lifecycle.stop();
			vi.useRealTimers();
		}
	});

	it.each([
		['terminal URL', { baseUrl: '/api/v1/terminals/other' }, 'session', 'actor', 5173],
		['session', {}, 'other-session', 'actor', 5173],
		['actor', {}, 'session', 'other-actor', 5173],
		['port', {}, 'session', 'actor', 5174],
		[
			'temporary epoch',
			{
				key: {
					token: 'test-token',
					temporaryContext: { id: 'scope', epoch: 'second' },
					isCurrent: () => true
				}
			},
			'session',
			'actor',
			5173
		]
	] as const)(
		'replaces authorization when the %s changes',
		async (_, changes, session, actor, port) => {
			const request = vi.fn().mockResolvedValue({
				url: preview + '/p/opaque',
				expires_at: Date.now() / 1000 + 300
			});
			const lifecycle = previewLifecycle(request);
			const original = {
				...connection,
				key: {
					token: 'test-token',
					temporaryContext: { id: 'scope', epoch: 'first' },
					isCurrent: () => true
				}
			};
			try {
				lifecycle.manage(true, original, 'session', 'temporary:scope', preview, 'actor', 5173);
				await vi.waitFor(() => expect(lifecycle.getFrame()).not.toBe(''));
				lifecycle.manage(
					true,
					{ ...original, ...changes },
					session,
					'temporary:scope',
					preview,
					actor,
					port
				);
				expect(lifecycle.getFrame()).toBe('');
				expect(request).toHaveBeenCalledTimes(2);
				expect(request.mock.calls[0][4].aborted).toBe(true);
				expect(request.mock.calls[1][5]).toBeUndefined();
			} finally {
				lifecycle.stop();
			}
		}
	);
});
