import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set([
	'stopResponse',
	'sendQueuedMessageNow',
	'submitHandler',
	'processNextInQueue'
]);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createChat = (stop: () => Promise<unknown>, chatId: string | null = 'chat') => {
	const dependencies = {
		stopTasksByChatId: vi.fn(stop),
		stopTask: vi.fn(stop),
		toast: { error: vi.fn() },
		submitPrompt: vi.fn(),
		abort: vi.fn()
	};
	const body = `
		const { stopTasksByChatId, stopTask, toast, submitPrompt, abort } = dependencies;
		const localStorage = { token: 'synthetic-token' };
		const $user = { id: 'author' }, chat = { user_id: 'author' };
		let $chatId = chatId;
		const stoppingChats = new Map(), processingQueueChats = new Set();
		let $chatRequestQueues = {}, files = [], prompt = 'draft';
		const chatRequestQueues = { update: (fn) => { $chatRequestQueues = fn($chatRequestQueues); } };
		const tick = async () => {}, emitTyping = () => {};
		let selectedModels = ['model'];
		const selectedModelIds = ['model'], $models = [{ id: 'model' }], pendingOAuthTools = [];
		const $settings = { enableMessageQueue: false }, $config = {}, chatFiles = [], chatVariables = {};
		const getChatVariablesForm = () => ({ conflicts: [] }), equal = () => true;
		const messageInput = { setText: () => {} };
		const eventTarget = new EventTarget();
		const cancelBrowserInteractions = () => {};
		const bridge = { connected: true };
		const shouldAutoScrollResponse = () => false, scrollToBottom = () => {};
		let taskIds = ['task'], generating = true, generationController = { abort };
		const history = { currentId: 'reply', messages: {
			parent: { childrenIds: ['reply'] },
			reply: { role: 'assistant', user_id: 'author', parentId: 'parent', done: false }
		}};
		${declarations}
		return { stop: stopResponse, sendNow: sendQueuedMessageNow, submit: submitHandler,
			processQueue: processNextInQueue, history, state: () => ({ taskIds, generating }),
			queue: () => $chatRequestQueues[chatId],
			enqueue: () => { $chatRequestQueues[chatId] = [{ id: 'queued', prompt: 'next', files: [] }]; },
			cancelEvent: () => { history.messages.reply.done = true; taskIds = null; },
			navigate: (id) => { $chatId = id; history.currentId = 'new-reply'; taskIds = ['new-task']; }
		};
	`;
	return {
		...dependencies,
		...new Function(
			'dependencies',
			'chatId',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(dependencies, chatId)
	};
};

describe('stopping a tool response', () => {
	it.each(['chat', null])('keeps a failed stop retryable for chat %s', async (chatId) => {
		const chat = createChat(async () => ({ status: false, message: 'Still stopping' }), chatId);
		await chat.stop();
		expect(chat.history.messages.reply.done).toBe(false);
		expect(chat.history.messages.reply.bridgeStopping).toBe(false);
		expect(chat.state()).toEqual({ taskIds: ['task'], generating: true });
		expect(chat.toast.error).toHaveBeenCalledWith('Still stopping');
		expect(chat.submitPrompt).not.toHaveBeenCalled();
		expect(chat.abort).not.toHaveBeenCalled();
	});

	it('keeps the stop available after an HTTP failure', async () => {
		const chat = createChat(async () => {
			throw 'Connection failed';
		});
		await chat.stop();
		expect(chat.history.messages.reply.done).toBe(false);
		expect(chat.state().taskIds).toEqual(['task']);
		expect(chat.toast.error).toHaveBeenCalledWith('Connection failed');
		expect(chat.submitPrompt).not.toHaveBeenCalled();
	});

	it('marks the reply finished only after cancellation is acknowledged', async () => {
		let resolve!: (value: unknown) => void;
		const chat = createChat(() => new Promise((done) => (resolve = done)));
		chat.enqueue();
		const stopping = chat.stop();
		expect(chat.history.messages.reply.done).toBe(false);
		resolve({ status: true });
		await stopping;
		expect(chat.history.messages.reply.done).toBe(true);
		expect(chat.state()).toEqual({ taskIds: null, generating: false });
		expect(chat.abort).toHaveBeenCalledOnce();
		expect(chat.submitPrompt).toHaveBeenCalledWith('next', []);
	});

	it('retains queued input when send-now cannot stop the current reply', async () => {
		const chat = createChat(async () => ({ status: false, message: 'Still stopping' }));
		chat.enqueue();
		await chat.sendNow('queued');
		expect(chat.submitPrompt).not.toHaveBeenCalled();
		expect(chat.queue()).toEqual([{ id: 'queued', prompt: 'next', files: [] }]);
	});

	it('does not submit a replacement prompt after a failed interruption', async () => {
		const chat = createChat(async () => ({ status: false, message: 'Still stopping' }));
		expect(await chat.submit('replacement')).toEqual({ status: 'rejected' });
		expect(chat.submitPrompt).not.toHaveBeenCalled();
	});

	it('can stop the previous reply after a voice transcript has already advanced the selection', async () => {
		const chat = createChat(async () => ({ status: true }));
		chat.history.currentId = 'voice-transcript';
		expect(await chat.stop(false, 'reply')).toBe(true);
		expect(chat.history.messages.reply.done).toBe(true);
		expect(chat.state()).toEqual({ taskIds: null, generating: false });
	});

	it.each(['chat', 'another-chat'])('does not clear newer reply state in %s', async (id) => {
		let resolve!: (value: unknown) => void;
		const chat = createChat(() => new Promise((done) => (resolve = done)));
		const stopping = chat.stop();
		chat.navigate(id);
		resolve({ status: true });
		await stopping;
		expect(chat.state()).toEqual({ taskIds: ['new-task'], generating: true });
		expect(chat.abort).not.toHaveBeenCalled();
	});

	it('holds queued input after the cancel event until the stop request is acknowledged', async () => {
		let resolve!: (value: unknown) => void;
		const chat = createChat(() => new Promise((done) => (resolve = done)));
		chat.enqueue();
		const stopping = chat.stop();
		chat.cancelEvent();
		await chat.processQueue('chat');
		expect(chat.submitPrompt).not.toHaveBeenCalled();
		resolve({ status: true });
		await stopping;
		expect(chat.submitPrompt).toHaveBeenCalledOnce();
	});
});
