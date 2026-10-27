import { afterEach, expect, it, vi } from 'vitest';
import { stopTask, stopTasksByChatId } from './index';
import { generateOpenAIChatCompletion } from './openai';
afterEach(() => {
	vi.unstubAllGlobals();
});
it('sends epoch verification on both temporary stop endpoints', async () => {
	const fetcher: any = vi.fn(async () => Response.json({ status: true }));
	vi.stubGlobal('fetch', fetcher);
	await stopTask('token', 'task', { id: 'scope', epoch: 'epoch' });
	await stopTasksByChatId('token', 'temporary:scope', { id: 'scope', epoch: 'epoch' });
	for (const [, options] of fetcher.mock.calls)
		expect(options.headers).toMatchObject({
			'X-Temporary-Context-Id': 'scope',
			'X-Temporary-Context-Epoch': 'epoch',
			authorization: 'Bearer token'
		});
});
it('preserves structured generation conflict data for reconciliation', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () =>
			Response.json(
				{ detail: { code: 'conflict', retryable: true, rebuild_allowed: false } },
				{ status: 409 }
			)
		)
	);
	await expect(
		generateOpenAIChatCompletion('token', { temporary_context: { id: 'scope' } })
	).rejects.toMatchObject({ status: 409, detail: { code: 'conflict' } });
});
