import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

// Exercise the real socket handler without starting services or the editor.
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set(['chatEventHandler', 'handleContextCompactionStatus']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createChat = () => {
	const toast = { loading: vi.fn(() => 'toast'), success: vi.fn(), error: vi.fn() };
	const body = `
		const { toast } = dependencies;
		const console = { log: () => {} };
		const tick = async () => {};
		const $i18n = { t: text => text };
		const $chatId = 'chat';
		let contextCompactionToastId = null;
		let contextUsageModelId = 'model', contextCompactionMode = 'v2';
		let contextUsageRequestVersion = 0, serverContextUsage = null;
		let history = { currentId: 'reply', messages: {
			reply: { model: 'model', content: '', statusHistory: [] },
			sibling: { model: 'other', content: '', statusHistory: [] }
		} };
		${declarations}
		return { event: chatEventHandler, history, usage: () => serverContextUsage };
	`;
	return {
		toast,
		...new Function(
			'dependencies',
			transpileModule(body, {
				compilerOptions: { target: ScriptTarget.ES2022 }
			}).outputText
		)({ toast })
	};
};
const status = (done = false, extra = {}) => ({
	mode: 'v2',
	model: 'model',
	action: 'context_compaction',
	done,
	description: done ? 'Context compacted' : 'Compacting context',
	tokens: done ? 20000 : 90000,
	threshold: 90000,
	context_window: 100000,
	...extra
});
const event = (data: any, message_id = 'reply', chat_id = 'chat') => ({
	chat_id,
	message_id,
	data: { type: 'context_compaction', data }
});

describe('compaction lifecycle in chat', () => {
	it('adds start and completion to the response status history without changing its content', async () => {
		const chat = createChat();
		await chat.event(event(status()));
		await chat.event(event(status(true)));
		expect(chat.history.messages.reply.statusHistory).toEqual([status(), status(true)]);
		expect(chat.history.messages.reply.content).toBe('');
		expect(chat.usage()).toMatchObject({ context_window: 100000, threshold: 90000 });
	});
	it('records failures even when the start event was missed', async () => {
		const chat = createChat();
		const failure = status(true, { error: true, description: 'Summary timed out' });
		await chat.event(event(failure));
		expect(chat.history.messages.reply.statusHistory).toEqual([failure]);
	});
	it('keeps another model branch status on that branch and rejects its usage', async () => {
		const chat = createChat();
		const sibling = status(true, { model: 'other' });
		await chat.event(event(sibling, 'sibling'));
		expect(chat.history.messages.sibling.statusHistory).toEqual([sibling]);
		expect(chat.history.messages.reply.statusHistory).toEqual([]);
		expect(chat.usage()).toBeNull();
	});
	it('ignores events from another chat', async () => {
		const chat = createChat();
		await chat.event(event(status(), 'reply', 'another-chat'));
		expect(chat.history.messages.reply.statusHistory).toEqual([]);
		expect(chat.toast.loading).not.toHaveBeenCalled();
	});
});

describe('tool result status lifecycle', () => {
	it('stores backend tool projection metadata without altering response content or usage', async () => {
		const chat = createChat();
		const data = {
			action: 'tool_result_compaction',
			mode: 'v2',
			model: 'model',
			done: true,
			method: 'excerpt',
			result_id: 'raw',
			branch_id: 'reply',
			original_tokens: 40000,
			processed_tokens: 10000
		};
		await chat.event({
			chat_id: 'chat',
			message_id: 'reply',
			data: { type: 'tool_result_compaction', data }
		});
		expect(chat.history.messages.reply.statusHistory).toEqual([data]);
		expect(chat.history.messages.reply.content).toBe('');
		expect(chat.usage()).toBeNull();
	});
});
