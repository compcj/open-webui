import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { getOutputText } from './Messages/structuredOutput';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set(['chatCompletionEventHandler', 'handleOpenAIError']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createChat = () => {
	const toast = { error: vi.fn() };
	const completed = vi.fn();
	const body = `
		const { getOutputText, toast, completed } = dependencies;
		let history = { messages: {} };
		const $settings = {};
		const $i18n = { t: (text) => text };
		const getErrorMessage = (error) => error.message ?? error;
		const navigator = {};
		const bridge = null;
		const dispatchCallOverlayAudio = () => {};
		const dismissContextCompactionToast = () => {};
		const processNextInQueue = async () => {};
		const removeAllDetails = (text) => text;
		const eventTarget = { dispatchEvent: () => {} };
		const CustomEvent = class {};
		const tick = async () => {};
		const shouldAutoScrollResponse = () => false;
		const chatCompletedHandler = completed;
		const createMessagesList = () => [];
		${declarations}
		return chatCompletionEventHandler;
	`;
	return {
		toast,
		completed,
		handle: new Function(
			'dependencies',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)({ getOutputText, toast, completed })
	};
};

describe('provider stream error recovery', () => {
	it.each([false, true])('clears an explicitly recovered error for shared=%s', async (shared) => {
		const chat = createChat();
		const message = { id: 'reply', content: '', done: false, error: null };
		await chat.handle({ error: { message: 'Upstream request failed' } }, message, 'chat', shared);
		expect(message.error).toBeTruthy();
		await chat.handle({ error: null, done: false }, message, 'chat', shared);
		expect(message.error).toBeNull();
		expect(message.done).toBe(false);
		await chat.handle({ content: 'Recovered answer', done: true }, message, 'chat', shared);
		expect(message.error).toBeNull();
		expect(message.content).toBe('Recovered answer');
		expect(message.done).toBe(true);
		if (!shared) expect(chat.completed).toHaveBeenCalledOnce();
	});

	it('keeps a terminal error when final output does not explicitly clear it', async () => {
		const chat = createChat();
		const message = { id: 'reply', content: 'Partial answer', done: false, error: null };
		await chat.handle({ error: { message: 'Upstream request failed' } }, message, 'chat');
		await chat.handle({ content: 'Partial answer', done: true }, message, 'chat');
		expect(message.error).toEqual({ content: 'Upstream request failed' });
		expect(chat.completed).not.toHaveBeenCalled();
	});
});
