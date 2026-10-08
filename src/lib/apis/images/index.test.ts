import { afterEach, describe, expect, it, vi } from 'vitest';
import { verifyConnection } from './index';
afterEach(() => {
	vi.unstubAllGlobals();
});
describe('image connection verification', () => {
	it('posts the unsaved connection including optional key', async () => {
		const fetcher = vi.fn(async () => new Response(JSON.stringify({ status: true })));
		vi.stubGlobal('fetch', fetcher);
		const connection = {
			engine: 'comfyui',
			url: 'https://images.example.test',
			key: 'synthetic-key'
		};
		expect(await verifyConnection('synthetic-token', connection)).toEqual({ status: true });
		expect(fetcher).toHaveBeenCalledWith(
			expect.stringContaining('/images/verify'),
			expect.objectContaining({ method: 'POST', body: JSON.stringify(connection) })
		);
	});
	it('surfaces server verification errors', async () => {
		vi.stubGlobal(
			'fetch',
			async () => new Response(JSON.stringify({ detail: 'Connection refused' }), { status: 400 })
		);
		await expect(
			verifyConnection('synthetic-token', {
				engine: 'automatic1111',
				url: 'https://images.example.test'
			})
		).rejects.toEqual('Connection refused');
	});
});
