import { afterEach, expect, it, vi } from 'vitest';
import { temporaryContextsApi, TemporaryContextError } from './index';
afterEach(() => {
	vi.unstubAllGlobals();
});
const scope = { id: 'scope/id', epoch: 'epoch', revision: 7, branch_id: 'leaf' };
it('preserves status and structured retry/rebuild details', async () => {
	vi.stubGlobal(
		'fetch',
		vi.fn(async () =>
			Response.json(
				{ detail: { code: 'expired', retryable: true, rebuild_allowed: false } },
				{ status: 410 }
			)
		)
	);
	await expect(temporaryContextsApi.state('token', scope)).rejects.toMatchObject({
		status: 410,
		detail: { rebuild_allowed: false }
	});
});
it('sends canonical scope fields, model and direct descriptor without writing a saved chat', async () => {
	const fetcher: any = vi.fn(async () => Response.json({ ok: true }));
	vi.stubGlobal('fetch', fetcher);
	await temporaryContextsApi.sync('token', scope, { history: { messages: {}, currentId: null } });
	await temporaryContextsApi.usage('token', scope, 'm', { id: 'm', direct: true }, 'socket');
	await temporaryContextsApi.toolResult('token', scope, 'result', 'leaf', { cursor: 'cursor' });
	await temporaryContextsApi.close('token', scope, true);
	expect(fetcher.mock.calls[0][0]).toContain('/temporary-contexts/scope%2Fid/sync');
	expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
		epoch: 'epoch',
		expected_revision: 7
	});
	expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({
		model: 'm',
		branch_id: 'leaf',
		model_item: { direct: true },
		session_id: 'socket'
	});
	expect(fetcher.mock.calls[2][0]).toContain('epoch=epoch&branch_id=leaf&cursor=cursor');
	expect(fetcher.mock.calls[3][1]).toMatchObject({ method: 'DELETE', keepalive: true });
});

it('sends explicit lease rebind and only minimal nonce claim identity', async () => {
	const fetcher: any = vi.fn(async () => Response.json({ ok: true }));
	vi.stubGlobal('fetch', fetcher);
	await temporaryContextsApi.lease('token', scope, 'socket', true);
	await temporaryContextsApi.claimRpc('token', scope, 'rpc/nonce', {
		socket_generation: 2,
		task_id: 'task',
		reply_id: 'reply',
		model_id: 'model',
		seq: 4
	});
	expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
		epoch: 'epoch',
		session_id: 'socket',
		rebind: true
	});
	expect(fetcher.mock.calls[1][0]).toContain('/scope%2Fid/rpc/rpc%2Fnonce/claim');
	expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
		epoch: 'epoch',
		socket_generation: 2,
		task_id: 'task',
		reply_id: 'reply',
		model_id: 'model',
		seq: 4
	});
});
