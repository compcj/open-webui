import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import { readComparisonSource } from './index';
import { MAX_COMPARISON_BYTES, readSourceResponse } from './sourceText';

beforeEach(() => config.set({ features: { enable_tool_servers: true } } as any));
afterEach(() => {
	vi.unstubAllGlobals();
});

describe('complete bounded source reads', () => {
	it('reads UTF-8 across chunks with a captured terminal, chat and abort signal', async () => {
		const bytes = new TextEncoder().encode('<svg>你好</svg>');
		const response = new Response(
			new ReadableStream({
				start(controller) {
					controller.enqueue(bytes.slice(0, 6));
					controller.enqueue(bytes.slice(6));
					controller.close();
				}
			}),
			{ headers: { 'content-type': 'image/svg+xml' } }
		);
		const fetchMock = vi.fn().mockResolvedValue(response);
		vi.stubGlobal('fetch', fetchMock);
		const controller = new AbortController();
		await expect(
			readComparisonSource(
				'/api/v1/terminals/server/',
				' token ',
				'/work/a b.svg',
				controller.signal,
				'chat-a'
			)
		).resolves.toBe('<svg>你好</svg>');
		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/terminals/server/files/view?path=%2Fwork%2Fa%20b.svg',
			{
				headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-a' },
				signal: controller.signal
			}
		);
	});
	it('refuses an oversized listed source without fetching', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);
		await expect(
			readComparisonSource(
				'/terminal',
				'token',
				'/large',
				new AbortController().signal,
				'chat-a',
				MAX_COMPARISON_BYTES + 1
			)
		).rejects.toThrow('2 MiB');
		expect(fetchMock).not.toHaveBeenCalled();
	});
	it.each<[number, string]>([
		[404, 'File not found.'],
		[403, 'Permission denied.'],
		[500, 'Could not read file.']
	])('cancels response body after HTTP %i', async (status, message) => {
		const cancel = vi.fn();
		const response = new Response(new ReadableStream({ cancel }), { status });
		await expect(readSourceResponse(response, new AbortController().signal)).rejects.toThrow(
			message
		);
		expect(cancel).toHaveBeenCalledOnce();
	});
	it.each<[Uint8Array, string]>([
		[new Uint8Array([0xff]), 'Only UTF-8'],
		[new Uint8Array(MAX_COMPARISON_BYTES + 1).fill(65), '2 MiB']
	])('rejects invalid or oversized bytes and cancels the stream', async (bytes, message) => {
		const cancel = vi.fn();
		const response = new Response(
			new ReadableStream({
				start(controller) {
					controller.enqueue(bytes);
				},
				cancel
			})
		);
		await expect(readSourceResponse(response, new AbortController().signal)).rejects.toThrow(
			message as string
		);
		expect(cancel).toHaveBeenCalledOnce();
	});
	it('rejects control characters in a complete text response', async () => {
		await expect(
			readSourceResponse(new Response('a\u0000b'), new AbortController().signal)
		).rejects.toThrow('not supported');
	});
	it('cancels a pending body read when the comparison is aborted', async () => {
		const cancel = vi.fn();
		const response = new Response(new ReadableStream({ cancel }));
		const controller = new AbortController();
		const pending = readSourceResponse(response, controller.signal);
		controller.abort();
		await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
		expect(cancel).toHaveBeenCalledOnce();
	});
	it('rejects binary MIME before consuming its bytes', async () => {
		const cancel = vi.fn();
		const response = new Response(new ReadableStream({ cancel }), {
			headers: { 'content-type': 'image/png' }
		});
		await expect(readSourceResponse(response, new AbortController().signal)).rejects.toThrow(
			'not supported'
		);
		expect(cancel).toHaveBeenCalledOnce();
	});
	it('cancels a source whose declared content length exceeds the limit', async () => {
		const cancel = vi.fn();
		const response = new Response(new ReadableStream({ cancel }), {
			headers: { 'content-length': String(MAX_COMPARISON_BYTES + 1) }
		});
		await expect(readSourceResponse(response, new AbortController().signal)).rejects.toThrow(
			'2 MiB'
		);
		expect(cancel).toHaveBeenCalledOnce();
	});
});
