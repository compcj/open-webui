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
