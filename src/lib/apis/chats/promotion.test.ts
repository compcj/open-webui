import { afterEach, expect, it, vi } from 'vitest';
import { createNewChat } from './index';

afterEach(() => {
	vi.unstubAllGlobals();
});
it('retries promotion with a stable creation key and authoritative source revision', async () => {
	const fetcher = vi.fn(async () => Response.json({ id: 'saved-chat' }));
	vi.stubGlobal('fetch', fetcher);
	const creation = {
		key: 'f5f90d52-e765-4cdf-a73d-4c1cba46f299',
		temporaryContext: { id: 'scope', epoch: 'epoch', revision: 7 }
	};
	for (let i = 0; i < 2; i++)
		await createNewChat(
			'token',
			{ history: { messages: {}, currentId: null } },
			null,
			{},
			creation
		);
	for (const call of fetcher.mock.calls as any[])
		expect(call[1].headers).toMatchObject({
			'Idempotency-Key': creation.key,
			'X-Temporary-Context-Id': 'scope',
			'X-Temporary-Context-Epoch': 'epoch',
			'X-Temporary-Context-Revision': '7',
			authorization: 'Bearer token'
		});
});
it('ordinary chat creation does not send temporary or idempotency headers', async () => {
	const fetcher = vi.fn(async () => Response.json({ id: 'saved-chat' }));
	vi.stubGlobal('fetch', fetcher);
	await createNewChat('token', { title: 'saved' }, null);
	const headers = (fetcher.mock.calls[0] as any[])[1].headers;
	expect(headers['X-Temporary-Context-Id']).toBeUndefined();
	expect(headers['Idempotency-Key']).toBeUndefined();
});
