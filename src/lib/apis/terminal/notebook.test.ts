import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import { createNotebookSession, executeNotebookCell, stopNotebookSession } from './index';

beforeEach(() => {
	config.set({ features: { enable_tool_servers: true } } as any);
});
afterEach(() => {
	vi.unstubAllGlobals();
});

describe('notebook chat context', () => {
	it('sends the chat context when creating, executing and stopping a kernel', async () => {
		const fetchMock = vi
			.fn()
			.mockResolvedValueOnce(Response.json({ id: 'kernel-1', kernel: 'python', status: 'idle' }))
			.mockResolvedValueOnce(Response.json({ status: 'ok', outputs: [] }))
			.mockResolvedValueOnce(new Response(null, { status: 204 }));
		vi.stubGlobal('fetch', fetchMock);
		await createNotebookSession(
			'/api/v1/terminals/server/',
			' token ',
			'/work/test.ipynb',
			'chat-a'
		);
		await executeNotebookCell(
			'/api/v1/terminals/server',
			' token ',
			'kernel-1',
			0,
			'print(1)',
			'chat-a'
		);
		await stopNotebookSession('/api/v1/terminals/server', ' token ', 'kernel-1', 'chat-a');
		expect(fetchMock.mock.calls).toEqual([
			[
				'/api/v1/terminals/server/notebooks',
				{
					method: 'POST',
					headers: {
						Authorization: 'Bearer token',
						'Content-Type': 'application/json',
						'X-Session-Id': 'chat-a'
					},
					body: JSON.stringify({ path: '/work/test.ipynb' })
				}
			],
			[
				'/api/v1/terminals/server/notebooks/kernel-1/execute',
				{
					method: 'POST',
					headers: {
						Authorization: 'Bearer token',
						'Content-Type': 'application/json',
						'X-Session-Id': 'chat-a'
					},
					body: JSON.stringify({ cell_index: 0, source: 'print(1)' })
				}
			],
			[
				'/api/v1/terminals/server/notebooks/kernel-1',
				{ method: 'DELETE', headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-a' } }
			]
		]);
	});
});
