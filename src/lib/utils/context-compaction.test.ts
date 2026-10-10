import * as compaction from './context-compaction';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	getCompactionMode,
	resolveCompactionWindow,
	selectContextUsage
} from './context-compaction';

describe('compaction UI contracts', () => {
	it('keeps legacy enable compatibility and gives the explicit mode priority', () => {
		expect(getCompactionMode({ enable_context_compaction: true })).toBe('legacy');
		expect(getCompactionMode({ enable_context_compaction: false })).toBe('off');
		expect(
			getCompactionMode({ context_compaction_mode: 'v2', enable_context_compaction: false })
		).toBe('v2');
		expect(
			getCompactionMode({ context_compaction_mode: 'off', enable_context_compaction: true })
		).toBe('off');
	});
	it('resolves explicit metadata, base inheritance and global defaults without inference parameters', () => {
		const models = [
			{ id: 'preset', info: { base_model_id: 'base', meta: { context_window: null } } },
			{ id: 'base', info: { meta: { context_window: 128000 } } }
		];
		expect(resolveCompactionWindow('preset', models, 262144)).toEqual({
			value: 128000,
			source: 'base'
		});
		expect(resolveCompactionWindow('missing', models, 262144)).toEqual({
			value: 262144,
			source: null
		});
		expect(
			resolveCompactionWindow('cycle', [{ id: 'cycle', info: { base_model_id: 'cycle' } }])
		).toEqual({ value: 262144, source: null });
	});
	it('never presents stale model/mode counts as current V2 occupancy', () => {
		const usage = { mode: 'v2' as const, model: 'a', tokens: 200, threshold: 1000 };
		expect(selectContextUsage('v2', 'a', usage, { tokens: 999 })).toBe(usage);
		expect(selectContextUsage('v2', 'b', usage, { tokens: 999 })).toBeNull();
		expect(selectContextUsage('legacy', 'a', usage, { tokens: 999 })).toEqual({ tokens: 999 });
		expect(selectContextUsage('off', 'a', usage, { tokens: 999 })).toBeNull();
	});
});

describe('compaction branch scope', () => {
	it('discards sibling branch usage even with the same mode and model', () => {
		const usage = { mode: 'v2', model: 'a', branch_id: 'left', tokens: 200 };
		expect(selectContextUsage('v2', 'a', usage, null, 'left')).toBe(usage);
		expect(selectContextUsage('v2', 'a', usage, null, 'right')).toBeNull();
		expect(
			selectContextUsage('v2', 'a', { ...usage, branch_id: undefined }, null, 'right')
		).toBeNull();
	});
});

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));
vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));

import { compactChatById, getChatContextUsage } from '$lib/apis/chats';

describe('compaction browser connection requests', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});

	it('uses an explicit branch query and sends direct metadata only for browser models', async () => {
		const fetchMock = vi.fn(
			async (_url: string, _options: RequestInit) =>
				new Response(JSON.stringify({ tokens: 123 }), { status: 200 })
		);
		vi.stubGlobal('fetch', fetchMock);
		await getChatContextUsage('synthetic-token', 'saved', 'server-model', 'branch');
		expect(fetchMock.mock.calls[0][0]).toContain('model=server-model&message_id=branch');
		expect(fetchMock.mock.calls[0][1].method).toBe('GET');
		const direct = { id: 'browser-model', direct: true };
		await getChatContextUsage('synthetic-token', 'saved', direct.id, 'sibling', direct);
		expect(fetchMock.mock.calls[1][1].method).toBe('POST');
		expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
			model: direct.id,
			message_id: 'sibling',
			model_item: direct
		});
	});

	it('provides the active socket only when manually compacting a direct model', async () => {
		const fetchMock = vi.fn(
			async (_url: string, _options: RequestInit) =>
				new Response(JSON.stringify({ compacted: true }), { status: 200 })
		);
		vi.stubGlobal('fetch', fetchMock);
		await compactChatById('synthetic-token', 'saved', 'server-model');
		expect(JSON.parse(String(fetchMock.mock.calls[0][1].body))).toEqual({ model: 'server-model' });
		const direct = { id: 'browser-model', direct: true };
		await compactChatById('synthetic-token', 'saved', direct.id, direct, 'owned-session');
		expect(JSON.parse(String(fetchMock.mock.calls[1][1].body))).toEqual({
			model: direct.id,
			model_item: direct,
			session_id: 'owned-session'
		});
	});
});

describe('legacy tool notification ownership', () => {
	const old = { id: 'old', output: [{ id: 'out', call_id: 'call', type: 'function_call_output' }] };
	const current = {
		id: 'new',
		meta: { tool_results: [{ id: 'sidecar', call_id: 'side-call', text: 'original' }] }
	};
	const messages = { old, new: current };
	const notice = { action: 'tool_result_compaction', method: 'summary', done: true };
	it('keeps old records with the original reply and hides copied records on a later reply', () => {
		const status = { ...notice, output_id: 'out', call_id: 'call' };
		expect(compaction.isVisibleToolResultStatus(status, 'old', messages)).toBe(true);
		expect(compaction.isVisibleToolResultStatus(status, 'new', messages)).toBe(false);
	});
	it('resolves injected sidecars and preserves records without sufficient identity', () => {
		const status = { ...notice, output_id: 'sidecar', call_id: 'side-call' };
		expect(compaction.isVisibleToolResultStatus(status, 'old', messages)).toBe(false);
		expect(compaction.isVisibleToolResultStatus(status, 'new', messages)).toBe(true);
		expect(compaction.isVisibleToolResultStatus(notice, 'old', messages)).toBe(true);
	});
});

// A trigger fraction is independent of available input space.
describe('context status budget meanings', () => {
	it('keeps the automatic trigger and input capacity separate', () => {
		expect(
			compaction.getContextBudgetStatus({
				tokens: 80000,
				threshold: 90000,
				input_budget: 120000,
				percent: 89,
				remaining_tokens: 10000,
				input_remaining_tokens: 40000
			})
		).toEqual({
			tokens: 80000,
			threshold: 90000,
			percent: 89,
			triggerRemaining: 10000,
			inputRemaining: 40000
		});
	});
	it('derives missing compatible fields without inventing zero capacity', () => {
		expect(
			compaction.getContextBudgetStatus({ tokens: 95000, threshold: 90000, input_budget: 120000 })
		).toEqual({
			tokens: 95000,
			threshold: 90000,
			percent: (95000 / 90000) * 100,
			triggerRemaining: 0,
			inputRemaining: 25000
		});
		expect(
			compaction.getContextBudgetStatus({ tokens: 0, estimated_tokens: 3000 }).inputRemaining
		).toBeNull();
		expect(compaction.getContextBudgetStatus(null).tokens).toBeNull();
	});
});

describe('last tool decision identity', () => {
	it('identifies a previous reply even when its batch is returned with current usage', () => {
		expect(compaction.getToolBatchOrigin({ source_message_id: 'earlier' }, 'reply')).toBe(
			'previous'
		);
		expect(compaction.getToolBatchOrigin({ source_message_id: 'reply' }, 'reply')).toBe('current');
	});
	it('keeps legacy historical and unknown records distinct from current admission', () => {
		expect(
			compaction.getToolBatchOrigin({ historical: true, source_message_id: 'reply' }, 'reply')
		).toBe('historical');
		expect(compaction.getToolBatchOrigin({}, 'reply')).toBe('unknown');
		expect(compaction.getToolBatchOrigin({ source_message_id: 'reply' }, undefined)).toBe(
			'unknown'
		);
	});
	it('converts a saved nanosecond clock to a date without assigning a new clock to legacy records', () => {
		expect(compaction.getToolBatchRecordedDate(1728000000000000000)?.toISOString()).toBe(
			'2024-10-04T00:00:00.000Z'
		);
		expect(compaction.getToolBatchRecordedDate(undefined)).toBeNull();
		expect(compaction.getToolBatchRecordedDate(-1)).toBeNull();
	});
});

describe('authoritative input occupancy', () => {
	it('uses backend tokens when a separate estimate is also present', () => {
		expect(
			compaction.getContextBudgetStatus({
				tokens: 80000,
				estimated_tokens: 50000,
				threshold: 100000,
				input_budget: 120000
			})
		).toMatchObject({ tokens: 80000, percent: 80, inputRemaining: 40000 });
	});
});
