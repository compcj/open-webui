import { afterEach, describe, expect, it, vi } from 'vitest';
import * as helpers from './context-compaction';
vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));
vi.mock('$lib/utils', () => ({ getTimeRange: vi.fn() }));
import * as chats from '$lib/apis/chats';

describe('tool budget admin validation', () => {
	it('accepts integers ordered from individual to batch and rejects invalid values', () => {
		const valid = (helpers as any).isValidToolBudgetPercent;
		expect(typeof valid).toBe('function');
		expect(valid(10, 30)).toBe(true);
		expect(valid(100, 100)).toBe(true);
		for (const [single, batch] of [
			[0, 30],
			[31, 30],
			[1, 101],
			[1.5, 30],
			[undefined, 30]
		]) {
			expect(valid(single, batch)).toBe(false);
		}
	});
});

describe('authenticated exact raw tool pages', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
	});
	it('binds raw read and paging to chat, result and current branch with escaped query values', async () => {
		const fetchMock = vi.fn(
			async () =>
				new Response(JSON.stringify({ text: '<script>原文</script>', next_cursor: 'next' }))
		);
		vi.stubGlobal('fetch', fetchMock);
		const read = (chats as any).getChatToolResult;
		expect(typeof read).toBe('function');
		const page = await read('synthetic-token', 'chat/id', 'raw/id', 'branch/id', {
			query: 'a & 中文',
			cursor: 'opaque&cursor'
		});
		const [url, options] = fetchMock.mock.calls[0] as any;
		expect(url).toContain('/chats/chat%2Fid/tool-results/raw%2Fid?');
		const params = new URL(url, 'https://example.test').searchParams;
		expect(params.get('branch_id')).toBe('branch/id');
		expect(params.get('query')).toBe('a & 中文');
		expect(params.get('cursor')).toBe('opaque&cursor');
		expect(options.headers.authorization).toBe('Bearer synthetic-token');
		expect(page.text).toBe('<script>原文</script>');
	});
	it('propagates authorization and invalidated source errors to the viewer', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn(async () => new Response(JSON.stringify({ detail: 'Source changed' }), { status: 409 }))
		);
		const read = (chats as any).getChatToolResult;
		expect(typeof read).toBe('function');
		await expect(read('synthetic-token', 'chat', 'raw', 'branch')).rejects.toBe('Source changed');
	});
});
