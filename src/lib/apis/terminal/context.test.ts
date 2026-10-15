import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import {
	listFiles,
	readFile,
	uploadToTerminal,
	createDirectory,
	deleteEntry,
	archiveFromTerminal,
	compareFiles
} from './index';

beforeEach(() => config.set({ features: { enable_tool_servers: true } } as any));
afterEach(() => {
	vi.unstubAllGlobals();
});

describe('terminal file chat context at the HTTP boundary', () => {
	it('sends context and cancellation for directory and HTML source reads', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(Response.json({ entries: [] }))
			.mockResolvedValueOnce(Response.json({ content: '<html>source</html>' }));
		vi.stubGlobal('fetch', fetchMock);
		const controller = new AbortController();
		await listFiles('/terminal/', ' token ', '/work/', 'chat-a', controller.signal);
		await expect(
			readFile('/terminal', ' token ', '/work/index.html', 'chat-a', controller.signal)
		).resolves.toBe('<html>source</html>');
		expect(fetchMock.mock.calls).toEqual([
			[
				'/terminal/files/list?directory=%2Fwork%2F',
				{
					headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-a' },
					signal: controller.signal
				}
			],
			[
				'/terminal/files/read?path=%2Fwork%2Findex.html',
				{
					headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-a' },
					signal: controller.signal
				}
			]
		]);
	});
	it('sends context for saves, new folders, deletions and ZIP downloads', async () => {
		const fetchMock = vi
			.fn()
			.mockImplementation(async () => Response.json({ path: '/work/file.txt' }));
		vi.stubGlobal('fetch', fetchMock);
		const file = new File(['new content'], 'file.txt');
		await uploadToTerminal('/terminal', ' token ', '/work/', file, 'chat-a');
		await createDirectory('/terminal', ' token ', '/work/new', 'chat-a');
		await deleteEntry('/terminal', ' token ', '/work/old', 'chat-a');
		await archiveFromTerminal('/terminal', ' token ', ['/work/a', '/work/b'], 'chat-a');
		for (const [, options] of fetchMock.mock.calls)
			expect(options.headers).toMatchObject({
				Authorization: 'Bearer token',
				'X-Session-Id': 'chat-a'
			});
		expect(fetchMock.mock.calls[0][0]).toBe('/terminal/files/upload?directory=%2Fwork%2F');
		expect(fetchMock.mock.calls[0][1].body.get('file').name).toBe('file.txt');
		expect(await fetchMock.mock.calls[0][1].body.get('file').text()).toBe('new content');
		expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify({ path: '/work/new' }));
		expect(fetchMock.mock.calls[2][0]).toBe('/terminal/files/delete?path=%2Fwork%2Fold');
		expect(fetchMock.mock.calls[2][1].method).toBe('DELETE');
		expect(fetchMock.mock.calls[3][1].body).toBe(JSON.stringify({ paths: ['/work/a', '/work/b'] }));
	});
	it('sends captured chat and cancellation through document comparison', async () => {
		const fetchMock = vi.fn().mockResolvedValue(Response.json({ hunks: [] }));
		vi.stubGlobal('fetch', fetchMock);
		const request = { original: '/a.pdf', revised: '/b.pdf', ignore_whitespace: false };
		const controller = new AbortController();
		await compareFiles('/terminal', 'token', request, 'chat-a', controller.signal);
		expect(fetchMock).toHaveBeenCalledWith('/terminal/files/compare', {
			method: 'POST',
			headers: {
				Authorization: 'Bearer token',
				'Content-Type': 'application/json',
				'X-Session-Id': 'chat-a'
			},
			body: JSON.stringify(request),
			signal: controller.signal
		});
	});
});
