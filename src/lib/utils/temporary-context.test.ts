import { describe, expect, it, vi } from 'vitest';
import { TemporaryContextController } from './temporary-context';

const graph = () => ({
	messages: {
		u: { id: 'u', role: 'user', parentId: null, childrenIds: ['a', 'b'], content: 'draft' },
		a: { id: 'a', role: 'assistant', model: 'm', parentId: 'u', done: false },
		b: { id: 'b', role: 'assistant', model: 'm', parentId: 'u', done: false }
	},
	currentId: 'a'
});
const deferred = () => {
	let resolve: (value: any) => void;
	const promise = new Promise<any>((r) => {
		resolve = r;
	});
	return { promise, resolve: (v: any) => resolve(v) };
};
function setup() {
	let actor = { id: 'owner', token: 'one' };
	let count = 0;
	const identity = {
		id: 'scope',
		epoch: 'epoch',
		revision: 1,
		status: 'open',
		socket_generation: 1
	};
	const api: any = {
		create: vi.fn(async () => ({ ...identity, id: `scope${++count}` })),
		sync: vi.fn(async (_t, s) => ({ ...s, revision: s.revision + 1 })),
		lease: vi.fn(async (_t, s, _sid, rebind = false) => ({
			...s,
			socket_generation: s.socket_generation + (rebind ? 1 : 0)
		})),
		claimRpc: vi.fn(async (_t, _s, rpcId, identity) => ({
			claimed: true,
			rpc_id: rpcId,
			socket_generation: identity.socket_generation,
			remaining_ms: 60000
		})),
		state: vi.fn(async (_t, s) => ({
			...s,
			events: [],
			event_seq: 0,
			tasks: [],
			history: graph()
		})),
		close: vi.fn(async (_t, s) => ({ ...s, status: 'closed_confirmed' })),
		usage: vi.fn(),
		compact: vi.fn(),
		toolResult: vi.fn()
	};
	const apply = vi.fn();
	const snapshot = vi.fn();
	const cancel = vi.fn();
	const timers: (() => void)[] = [];
	const controller = new TemporaryContextController({
		api,
		actor: () => actor,
		session: () => 'same-socket',
		applyEvent: apply,
		applyState: snapshot,
		cancelInteractions: cancel,
		onError: vi.fn(),
		interval: (fn) => {
			timers.push(fn);
			return timers.length;
		},
		clearInterval: vi.fn(),
		delay: async () => {}
	});
	return {
		controller,
		api,
		apply,
		snapshot,
		cancel,
		timers,
		setActor: (a: { id: string; token: string }) => {
			actor = a;
		}
	};
}
const prepare = (s: ReturnType<typeof setup>) => s.controller.prepare(graph(), 'u');
const event = (s: ReturnType<typeof setup>, seq: number, reply = 'a', overrides = {}): any => ({
	chat_id: s.controller.chatId,
	temporary_context: s.controller.identity,
	seq,
	message_id: reply,
	model_id: 'm',
	task_id: `task-${reply}`,
	data: { type: 'chat:completion', data: {} },
	...overrides
});
const pendingRpc = (s: ReturnType<typeof setup>, rpc: any) => {
	s.api.state.mockImplementation(async (_token: string, scope: any) => ({
		...scope,
		history: graph(),
		tasks: [],
		events: [],
		event_seq: rpc.seq,
		pending_rpcs: [
			{
				rpc_id: rpc.rpc_id,
				socket_generation: rpc.socket_generation,
				task_id: rpc.task_id,
				reply_id: rpc.message_id,
				model_id: rpc.model_id,
				seq: rpc.seq,
				claimed: false
			}
		]
	}));
};
describe('page-local temporary scope', () => {
	it('syncs full graph before generation and gives a new chat a new identity on the same socket', async () => {
		const s = setup();
		const first = await prepare(s);
		expect(s.api.sync.mock.calls[0][2].history).toEqual(graph());
		expect(first.branch_id).toBe('u');
		await s.controller.close();
		const next = await prepare(s);
		expect(next.id).not.toBe(first.id);
		expect(s.api.create).toHaveBeenCalledTimes(2);
	});
	it('rejects an async result after token or account changes', async () => {
		const s = setup();
		const d = deferred();
		s.api.create.mockReturnValueOnce(d.promise);
		const pending = prepare(s);
		s.setActor({ id: 'other', token: 'two' });
		d.resolve({ id: 'old', epoch: 'e', revision: 1 });
		await expect(pending).rejects.toThrow();
		expect(s.controller.identity).toBeNull();
	});
	it('separates same-model replies, rejects duplicates, wrong models and epochs', async () => {
		const s = setup();
		await prepare(s);
		await s.controller.receive(event(s, 1));
		await s.controller.receive(event(s, 1));
		await s.controller.receive(event(s, 2, 'b'));
		await s.controller.receive(event(s, 3, 'a', { model_id: 'other' }));
		await s.controller.receive(
			event(s, 3, 'a', { temporary_context: { id: 'old', epoch: 'old' } })
		);
		expect(s.apply.mock.calls.map((c) => c[0].message_id)).toEqual(['a', 'b']);
	});
	it('reconciles a sequence gap without replaying a snapshot-covered delta', async () => {
		const s = setup();
		await prepare(s);
		s.api.state.mockImplementationOnce(async (_t: string, scope: any) => ({
			...scope,
			history: graph(),
			events: [],
			tasks: [],
			event_seq: 4
		}));
		await s.controller.receive(event(s, 4));
		expect(s.snapshot).toHaveBeenCalledOnce();
		expect(s.apply).not.toHaveBeenCalled();
	});
	it('does not apply an older snapshot over a newer event', async () => {
		const s = setup();
		await prepare(s);
		const d = deferred();
		s.api.state.mockReturnValueOnce(d.promise);
		const pending = s.controller.reconcile();
		await s.controller.receive(event(s, 1));
		d.resolve({ ...s.controller.identity, history: graph(), tasks: [], events: [], event_seq: 0 });
		await pending;
		expect(s.snapshot).not.toHaveBeenCalled();
	});
	it('409 reconciles once and never automatically repeats sync/generation', async () => {
		const s = setup();
		await prepare(s);
		s.api.sync.mockRejectedValueOnce({ status: 409 });
		await expect(prepare(s)).rejects.toMatchObject({ status: 409 });
		expect(s.api.sync).toHaveBeenCalledTimes(2);
		expect(s.api.state).toHaveBeenCalledOnce();
	});
	it('503 preserves the scope and does not create a fallback', async () => {
		const s = setup();
		const first = await prepare(s);
		s.api.sync.mockRejectedValueOnce({ status: 503 });
		await expect(prepare(s)).rejects.toMatchObject({ status: 503 });
		expect(s.controller.identity?.id).toBe(first.id);
		expect(s.api.create).toHaveBeenCalledOnce();
	});
	it('410 requires explicit rebuild permission and a separate user action', async () => {
		const s = setup();
		await prepare(s);
		s.api.sync.mockRejectedValueOnce({ status: 410, detail: { rebuild_allowed: false } });
		await expect(prepare(s)).rejects.toMatchObject({ status: 410 });
		await expect(s.controller.rebuild()).rejects.toThrow();
		expect(s.api.create).toHaveBeenCalledOnce();
		s.api.sync.mockRejectedValueOnce({ status: 410, detail: { rebuild_allowed: true } });
		await expect(prepare(s)).rejects.toMatchObject({ status: 410 });
		await s.controller.rebuild();
		expect(s.api.create.mock.calls[1][1]).toHaveProperty('replaces');
	});
	it('close timeout retains retryable scope', async () => {
		const s = setup();
		await prepare(s);
		s.api.close.mockImplementationOnce(async (_t: string, scope: any) => ({
			...scope,
			status: 'closing'
		}));
		expect(await s.controller.close()).toBe(false);
		expect(s.controller.identity).not.toBeNull();
		expect(await s.controller.close()).toBe(true);
		expect(s.controller.identity).toBeNull();
	});
	it('rebind cancels approvals, invalidates old callbacks and never sends a generation', async () => {
		const s = setup();
		await prepare(s);
		const current = s.controller.capture();
		s.controller.disconnect();
		expect(s.cancel).toHaveBeenCalled();
		expect(current()).toBe(false);
		await s.controller.rebind();
		expect(s.api.lease).toHaveBeenCalledTimes(2);
		expect(s.api.state).toHaveBeenCalledOnce();
		expect(s.api.sync).toHaveBeenCalledOnce();
	});
	it('lease is repeated without changing source revision', async () => {
		const s = setup();
		await prepare(s);
		const revision = s.controller.identity?.revision;
		await s.timers[0]();
		expect(s.api.lease).toHaveBeenCalledTimes(2);
		expect(s.controller.identity?.revision).toBe(revision);
	});
});

it('rejects events while disconnected until authenticated lease rebind', async () => {
	const s = setup();
	await prepare(s);
	s.controller.disconnect();
	await s.controller.receive(event(s, 1));
	expect(s.apply).not.toHaveBeenCalled();
	await s.controller.rebind();
	await s.controller.receive(event(s, 1));
	expect(s.apply).toHaveBeenCalledOnce();
});
it('does not query the previous owner after an account switch', async () => {
	const s = setup();
	await prepare(s);
	s.setActor({ id: 'other', token: 'two' });
	await expect(s.controller.usage('a', 'm')).rejects.toThrow();
	expect(s.api.usage).not.toHaveBeenCalled();
});
it('allows a new account to create after the previous pending create is rejected', async () => {
	const s = setup();
	const pendingCreate = deferred();
	s.api.create.mockReturnValueOnce(pendingCreate.promise);
	const pending = prepare(s);
	s.setActor({ id: 'other', token: 'two' });
	pendingCreate.resolve({ id: 'old', epoch: 'old', revision: 1 });
	await expect(pending).rejects.toThrow();
	await prepare(s);
	expect(s.api.create).toHaveBeenCalledTimes(2);
});

it('rejects a snapshot older than the current raw revision even when event sequence is unchanged', async () => {
	const s = setup();
	await prepare(s);
	const pendingState = deferred();
	s.api.state.mockReturnValueOnce(pendingState.promise);
	const reading = s.controller.reconcile();
	await prepare(s);
	pendingState.resolve({
		...s.controller.identity,
		revision: 1,
		history: graph(),
		tasks: [],
		events: [],
		event_seq: 0
	});
	await reading;
	expect(s.snapshot).not.toHaveBeenCalled();
});

it('waits for worker-confirmed state and keeps uncertain execution blocked', async () => {
	const s = setup();
	await prepare(s);
	s.api.state
		.mockImplementationOnce(async (_t: string, scope: any) => ({
			...scope,
			history: graph(),
			event_seq: 0,
			tasks: [{ status: 'registered' }],
			uncertain: false
		}))
		.mockImplementationOnce(async (_t: string, scope: any) => ({
			...scope,
			history: graph(),
			event_seq: 0,
			tasks: [],
			uncertain: false
		}));
	expect(await s.controller.waitForSettled()).toBe(true);
	s.api.state.mockImplementationOnce(async (_t: string, scope: any) => ({
		...scope,
		history: graph(),
		event_seq: 0,
		tasks: [],
		uncertain: true
	}));
	expect(await s.controller.waitForSettled()).toBe(false);
});
it('times out without converting a registered worker to completion', async () => {
	const s = setup();
	await prepare(s);
	s.api.state.mockImplementation(async (_t: string, scope: any) => ({
		...scope,
		history: graph(),
		event_seq: 0,
		tasks: [{ status: 'registered' }],
		uncertain: false
	}));
	expect(await s.controller.waitForSettled(2)).toBe(false);
	expect(s.api.state).toHaveBeenCalledTimes(2);
});

it('claims each scoped RPC once and rejects its callback guard after rebinding', async () => {
	const s = setup();
	await prepare(s);
	let current = () => true;
	const run = vi.fn(async (_event: any, _callback: any, guard: () => boolean) => {
		current = guard;
	});
	const rpc = event(s, 1, 'a', {
		rpc_id: 'first-rpc',
		socket_generation: 1,
		data: { type: 'request:chat:completion' }
	});
	pendingRpc(s, rpc);
	await s.controller.receive(rpc, undefined, run);
	expect(run).toHaveBeenCalledOnce();
	await s.controller.receive(event(s, 1), undefined, run);
	expect(run).toHaveBeenCalledOnce();
	s.controller.disconnect();
	expect(current()).toBe(false);
});

it('a state watermark never consumes an unclaimed live RPC, but a new lease rejects old delivery', async () => {
	const s = setup();
	s.api.lease.mockImplementation(
		async (_token: string, scope: any, _session: string, rebind: boolean) => ({
			...scope,
			socket_generation: rebind ? 2 : 1
		})
	);
	await prepare(s);
	s.api.state.mockImplementation(async (_token: string, scope: any) => ({
		...scope,
		history: graph(),
		tasks: [],
		events: [],
		event_seq: 4
	}));
	await s.controller.reconcile();
	const rpc = event(s, 4, 'a', {
		rpc_id: 'rpc-one',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	const run = vi.fn();
	pendingRpc(s, rpc);
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(true);
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(false);
	expect(run).toHaveBeenCalledOnce();
	await s.controller.rebind();
	expect(
		await s.controller.receive({ ...rpc, seq: 5, rpc_id: 'old-undelivered' }, vi.fn(), run)
	).toBe(false);
	expect(run).toHaveBeenCalledOnce();
});

it('manual direct compaction accepts only its own selected branch and model', async () => {
	const s = setup();
	s.api.lease.mockImplementation(async (_token: string, scope: any) => ({
		...scope,
		socket_generation: 1
	}));
	await prepare(s);
	const pending = deferred();
	s.api.compact.mockReturnValueOnce(pending.promise);
	const compact = s.controller.compact('u', 'summary-model');
	const rpc = event(s, 1, 'u', {
		model_id: 'summary-model',
		task_id: 'manual-task',
		rpc_id: 'manual',
		socket_generation: 1,
		data: { type: 'request:chat:completion' }
	});
	const run = vi.fn();
	pendingRpc(s, rpc);
	expect(await s.controller.receive({ ...rpc, model_id: 'other' }, vi.fn(), run)).toBe(false);
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(true);
	pending.resolve({ ok: true });
	await compact;
	expect(await s.controller.receive({ ...rpc, seq: 2, rpc_id: 'late-manual' }, vi.fn(), run)).toBe(
		false
	);
	expect(run).toHaveBeenCalledOnce();
});

it('rejects an expired pending RPC even if its event sequence is new', async () => {
	const s = setup();
	await prepare(s);
	const rpc = event(s, 1, 'a', {
		rpc_id: 'gone',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	const run = vi.fn();
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(false);
	expect(s.api.claimRpc).not.toHaveBeenCalled();
	expect(run).not.toHaveBeenCalled();
});
it('server atomic claim rejects a timeout that happened while fetching state', async () => {
	const s = setup();
	await prepare(s);
	const rpc = event(s, 1, 'a', {
		rpc_id: 'expired-between',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	pendingRpc(s, rpc);
	s.api.claimRpc.mockRejectedValueOnce({ status: 409 });
	const run = vi.fn();
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(false);
	expect(run).not.toHaveBeenCalled();
});
it('reserves a nonce before awaits and rejects duplicate delivery during state fetch', async () => {
	const s = setup();
	await prepare(s);
	const rpc = event(s, 1, 'a', {
		rpc_id: 'concurrent',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	pendingRpc(s, rpc);
	const state = await s.api.state('', s.controller.identity);
	s.api.state.mockClear();
	const pending = deferred();
	s.api.state.mockReturnValueOnce(pending.promise);
	const run = vi.fn();
	const first = s.controller.receive(rpc, vi.fn(), run);
	expect(await s.controller.receive(rpc, vi.fn(), run)).toBe(false);
	pending.resolve(state);
	expect(await first).toBe(true);
	expect(run).toHaveBeenCalledOnce();
	expect(s.api.claimRpc).toHaveBeenCalledOnce();
});
it('does not execute a claim response after same-page rebind', async () => {
	const s = setup();
	await prepare(s);
	const rpc = event(s, 1, 'a', {
		rpc_id: 'rebound',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	pendingRpc(s, rpc);
	const accepted = deferred();
	s.api.claimRpc.mockReturnValueOnce(accepted.promise);
	const run = vi.fn();
	const receiving = s.controller.receive(rpc, vi.fn(), run);
	await vi.waitFor(() => expect(s.api.claimRpc).toHaveBeenCalledOnce());
	await s.controller.rebind();
	accepted.resolve({
		claimed: true,
		rpc_id: rpc.rpc_id,
		socket_generation: 1,
		remaining_ms: 60000
	});
	expect(await receiving).toBe(false);
	expect(run).not.toHaveBeenCalled();
	expect(s.api.lease.mock.lastCall[3]).toBe(true);
});
it('revokes a running callback guard when authoritative state removes its nonce', async () => {
	const s = setup();
	await prepare(s);
	const rpc = event(s, 1, 'a', {
		rpc_id: 'stopped',
		socket_generation: 1,
		data: { type: 'execute:tool' }
	});
	pendingRpc(s, rpc);
	let current = () => false;
	await s.controller.receive(rpc, vi.fn(), (_event, _callback, guard) => {
		current = guard;
	});
	expect(current()).toBe(true);
	s.api.state.mockImplementation(async (_t: string, scope: any) => ({
		...scope,
		event_seq: 1,
		tasks: [],
		pending_rpcs: []
	}));
	await s.controller.reconcile();
	expect(current()).toBe(false);
});

it.each(['normal', 'rpc'])(
	'authoritatively binds a continued reply to its new task for %s delivery',
	async (kind) => {
		const s = setup();
		await prepare(s);
		await s.controller.receive(event(s, 1, 'a', { task_id: 'old-task' }));
		await prepare(s);
		const next = event(s, 2, 'a', {
			task_id: 'new-task',
			...(kind === 'rpc'
				? { rpc_id: 'continued', socket_generation: 1, data: { type: 'request:chat:completion' } }
				: {})
		});
		s.api.state.mockImplementation(async (_t: string, scope: any) => ({
			...scope,
			history: graph(),
			event_seq: kind === 'rpc' ? 2 : 1,
			tasks: [
				{ task_id: 'new-task', reply_id: 'a', model_id: 'm', status: 'registered' },
				{ task_id: 'old-task', reply_id: 'a', model_id: 'm', status: 'done' }
			],
			pending_rpcs: [
				{
					rpc_id: 'continued',
					socket_generation: 1,
					task_id: 'new-task',
					reply_id: 'a',
					model_id: 'm',
					seq: 2,
					claimed: false
				}
			]
		}));
		const run = vi.fn();
		expect(await s.controller.receive(next, vi.fn(), run)).toBe(true);
		expect(run).toHaveBeenCalledOnce();
		expect(
			await s.controller.receive(
				{ ...next, seq: 3, task_id: 'old-task', ...(kind === 'rpc' ? { rpc_id: 'late-old' } : {}) },
				vi.fn(),
				run
			)
		).toBe(false);
		expect(run).toHaveBeenCalledOnce();
	}
);

it('rejects the prior task before the first continued event arrives and handles a reconciled watermark', async () => {
	const s = setup();
	await prepare(s);
	await s.controller.receive(event(s, 1, 'a', { task_id: 'old-task' }));
	await prepare(s);
	s.api.state.mockImplementation(async (_t: string, scope: any) => ({
		...scope,
		history: graph(),
		event_seq: 2,
		tasks: [
			{ task_id: 'old-task', reply_id: 'a', model_id: 'm', status: 'done' },
			{ task_id: 'new-task', reply_id: 'a', model_id: 'm', status: 'registered' }
		]
	}));
	const run = vi.fn();
	expect(
		await s.controller.receive(event(s, 2, 'a', { task_id: 'old-task' }), undefined, run)
	).toBe(false);
	expect(run).not.toHaveBeenCalled();
	expect(s.snapshot).toHaveBeenCalled();
	expect(
		await s.controller.receive(event(s, 3, 'a', { task_id: 'new-task' }), undefined, run)
	).toBe(true);
	expect(run).toHaveBeenCalledOnce();
});

it.each(['done', 'unknown'])('does not bind an unverified %s continuation task', async (status) => {
	const s = setup();
	await prepare(s);
	await s.controller.receive(event(s, 1, 'a', { task_id: 'old-task' }));
	await prepare(s);
	s.api.state.mockImplementation(async (_t: string, scope: any) => ({
		...scope,
		history: graph(),
		event_seq: 1,
		tasks:
			status === 'done'
				? [{ task_id: 'new-task', reply_id: 'a', model_id: 'm', status: 'done' }]
				: []
	}));
	const run = vi.fn();
	expect(
		await s.controller.receive(event(s, 2, 'a', { task_id: 'new-task' }), undefined, run)
	).toBe(false);
	expect(run).not.toHaveBeenCalled();
});
