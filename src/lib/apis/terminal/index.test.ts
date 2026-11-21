import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { config, user } from '$lib/stores';

import {
	downloadDeliveryFile,
	downloadFileBlob,
	downloadFilePreview,
	getFileDelivery,
	resolveTerminalConnection,
	terminalRequest,
	validateDeliveryUrl
} from './index';

beforeEach(() => {
	config.set({ features: { enable_tool_servers: true } } as any);
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('validateDeliveryUrl', () => {
	const baseUrl = '/api/v1/terminals/system%2Fmain';

	it('accepts only the signed file endpoint under the expected same-origin terminal', () => {
		expect(
			validateDeliveryUrl(
				'/api/v1/terminals/system%2Fmain/files/download?ref=signed.value',
				baseUrl,
				'download'
			)
		).toBe('http://localhost:3000/api/v1/terminals/system%2Fmain/files/download?ref=signed.value');
	});

	it.each([
		'https://attacker.example/api/v1/terminals/system%2Fmain/files/download?ref=signed',
		'http://attacker@localhost:3000/api/v1/terminals/system%2Fmain/files/download?ref=signed',
		'/api/v1/terminals/other/files/download?ref=signed',
		'/api/v1/terminals/system%2Fmain/files/download?ref=signed&next=https://attacker.example',
		'/api/v1/terminals/system%2Fmain/files/download',
		'/api/v1/terminals/system%2Fmain/files/image?ref=signed'
	])('rejects an untrusted download URL: %s', (url) => {
		expect(validateDeliveryUrl(url, baseUrl, 'download')).toBeNull();
	});
});

describe('getFileDelivery', () => {
	it('requests display metadata with bearer and session headers and validates returned URLs', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					path: '/tmp/chart.svg',
					exists: true,
					owner_id: 'owner-1',
					download_url: '/api/v1/terminals/system/files/download?ref=download-ref',
					image_url: '/api/v1/terminals/system/files/image?ref=image-ref'
				}),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		const result = await getFileDelivery(
			'/api/v1/terminals/system',
			' token ',
			'/tmp/chart.svg',
			'chat-1'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/terminals/system/files/display?path=%2Ftmp%2Fchart.svg',
			{
				headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-1' }
			}
		);
		expect(result).toMatchObject({
			path: '/tmp/chart.svg',
			owner_id: 'owner-1',
			download_url: 'http://localhost:3000/api/v1/terminals/system/files/download?ref=download-ref',
			image_url: 'http://localhost:3000/api/v1/terminals/system/files/image?ref=image-ref'
		});
	});

	it('fails closed when the server returns an external delivery URL', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(
					JSON.stringify({
						path: '/tmp/file.txt',
						exists: true,
						owner_id: 'owner-1',
						download_url: 'https://attacker.example/file'
					}),
					{ status: 200 }
				)
			)
		);

		await expect(
			getFileDelivery('/api/v1/terminals/system', 'token', '/tmp/file.txt')
		).resolves.toBeNull();
	});
});

describe('downloadDeliveryFile', () => {
	it('validates the URL before sending bearer credentials and session context', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(new Uint8Array([1, 2, 3]), {
				status: 200,
				headers: { 'content-disposition': "attachment; filename*=UTF-8''report.pdf" }
			})
		);
		vi.stubGlobal('fetch', fetchMock);

		const result = await downloadDeliveryFile(
			'/api/v1/terminals/system/files/download?ref=signed',
			'/api/v1/terminals/system',
			'token',
			'chat-1'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/terminals/system/files/download?ref=signed',
			{ headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-1' } }
		);
		expect(result?.filename).toBe('report.pdf');
	});

	it('does not fetch an invalid URL', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		await expect(
			downloadDeliveryFile('https://attacker.example/file', '/api/v1/terminals/system', 'token')
		).resolves.toBeNull();
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe('downloadFilePreview', () => {
	it('accepts a PDF response served as application/octet-stream', async () => {
		const pdf = new TextEncoder().encode('%PDF-1.7\n');
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(pdf, {
					status: 200,
					headers: { 'content-type': 'application/octet-stream' }
				})
			)
		);

		const result = await downloadFilePreview(
			'/api/v1/terminals/system',
			'token',
			'/tmp/report.docx',
			'chat-1'
		);

		expect(result?.filename).toBe('report.docx');
	});

	it('rejects non-PDF bytes regardless of the response MIME', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(new TextEncoder().encode('<html>no</html>'), {
					status: 200,
					headers: { 'content-type': 'application/pdf' }
				})
			)
		);

		await expect(
			downloadFilePreview('/api/v1/terminals/system', 'token', '/tmp/report.docx')
		).resolves.toBeNull();
	});
});

describe('terminal feature policy', () => {
	it('does not send credentials when tool servers are disabled', async () => {
		config.set({ features: { enable_tool_servers: false } } as any);
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);
		await expect(
			getFileDelivery('/api/v1/terminals/system', 'token', '/tmp/file.txt')
		).rejects.toThrow('Tool servers are disabled');
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe('shared system-terminal preview download path', () => {
	it('uses signed original delivery for comment, hover and Markdown resource downloads', async () => {
		const baseUrl = '/api/v1/terminals/system';
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						path: '/work/document.docx',
						exists: true,
						owner_id: 'actor',
						download_url: baseUrl + '/files/download?ref=signed'
					})
				)
			)
			.mockResolvedValueOnce(new Response('original bytes'));
		vi.stubGlobal('fetch', fetchMock);
		const result = await downloadFileBlob(baseUrl, 'actor-token', '/work/document.docx', 'chat-a');
		expect(await result?.blob.text()).toBe('original bytes');
		expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
			baseUrl + '/files/display?path=%2Fwork%2Fdocument.docx',
			'http://localhost:3000' + baseUrl + '/files/download?ref=signed'
		]);
		for (const [, init] of fetchMock.mock.calls)
			expect(init.headers['X-Session-Id']).toBe('chat-a');
	});
	it('does not fall back to unsigned system bytes when delivery validation fails', async () => {
		const fetchMock = vi.fn().mockResolvedValue(new Response('{}'));
		vi.stubGlobal('fetch', fetchMock);
		expect(
			await downloadFileBlob('/api/v1/terminals/system', 'token', '/work/image.svg', 'chat')
		).toBeNull();
		expect(fetchMock).toHaveBeenCalledOnce();
	});
});

describe('direct terminal connection runtime authorization', () => {
	const direct = [{ url: 'https://terminal.example', key: 'synthetic' }];
	it('rejects saved direct connections while retaining system terminals when the flag is off', () => {
		expect(resolveTerminalConnection(direct[0].url, [], direct, 'token')).toBeNull();
		expect(resolveTerminalConnection('system', [{ id: 'system' }], direct, 'token')?.system).toBe(
			true
		);
		expect(direct).toEqual([{ url: 'https://terminal.example', key: 'synthetic' }]);
	});
	it.each([
		{ role: 'user', permissions: {} },
		{ role: 'pending', permissions: { features: { direct_tool_servers: true } } }
	])('rejects saved direct credentials without a current personal integration grant', (actor) => {
		config.set({
			features: { enable_tool_servers: true, enable_direct_integrations: true }
		} as any);
		user.set({ id: 'actor', ...actor } as any);
		expect(resolveTerminalConnection(direct[0].url, [], direct, 'token')).toBeNull();
		user.set(undefined);
	});
	it('permits direct selection when the global switch is on', () => {
		user.set({
			id: 'actor',
			role: 'user',
			permissions: { features: { direct_tool_servers: true } }
		} as any);
		config.set({
			features: { enable_tool_servers: true, enable_direct_integrations: true }
		} as any);
		expect(resolveTerminalConnection(direct[0].url, [], direct, 'token')?.system).toBe(false);
		user.set(undefined);
	});
	it('rechecks the switch before using an already captured direct connection', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);
		await expect(
			terminalRequest(
				{ selector: direct[0].url, baseUrl: direct[0].url, key: 'synthetic', system: false },
				'chat',
				'/processes'
			)
		).rejects.toThrow('Direct integrations are disabled');
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe('per-chat signed terminal delivery', () => {
	it('accepts root-scoped signed downloads issued through a per-chat display route', async () => {
		const base = '/api/v1/terminals/system%2Fmain';
		const preview = base + '/chats/chat%2Fone';
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(
				new Response(
					JSON.stringify({
						path: '/work/chart.svg',
						exists: true,
						owner_id: 'actor',
						download_url: base + '/files/download?ref=signed',
						image_url: base + '/files/image?ref=signed-image'
					})
				)
			)
			.mockResolvedValueOnce(new Response('original'));
		vi.stubGlobal('fetch', fetchMock);
		expect(
			await (
				await downloadFileBlob(preview, 'actor-token', '/work/chart.svg', 'chat/one')
			)?.blob.text()
		).toBe('original');
		expect(fetchMock.mock.calls[0][0]).toBe(preview + '/files/display?path=%2Fwork%2Fchart.svg');
		expect(fetchMock.mock.calls[1][0]).toBe(
			'http://localhost:3000' + base + '/files/download?ref=signed'
		);
	});
	it.each([
		['/api/v1/terminals/other/files/download?ref=signed', '/api/v1/terminals/system/chats/chat'],
		[
			'/api/v1/terminals/system/files/download?ref=signed',
			'/api/v1/terminals/system/chats/chat/extra'
		],
		['/api/v1/terminals/system/files/download?ref=signed', '/api/v1/terminals/system/chats/'],
		['/custom/files/download?ref=signed', '/custom/chats/chat'],
		[
			'https://foreign.example/api/v1/terminals/system/files/download?ref=signed',
			'/api/v1/terminals/system/chats/chat'
		]
	])('rejects mismatched or malformed terminal scope', (url, base) => {
		expect(validateDeliveryUrl(url, base, 'download')).toBeNull();
	});
});
