import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set([
	'chatEventHandler',
	'applyChatEvent',
	'cancelBrowserInteractions',
	'resolveBrowserInteraction',
	'handleInteractionDisconnect'
]);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const createChat = (temporary = false, author = 'actor') => {
	const body = `
  const $chatId = temporary ? 'temporary-chat' : 'saved-chat';
  const $user = { id: 'actor' };
  const history = { messages: { reply: { user_id: author } } };
  let browserInteractions = [], browserApprovalMode = null, eventInteractionId = null;
  let showEventConfirmation = false, eventCallback = () => {};
  const isTemporaryChatId = () => temporary;
  const isTemporaryContextRpc = () => false;
  const temporaryController = { identity: null, capture: () => () => true, disconnect: () => {} };
  const console = { log: () => {} };
  ${declarations}
  return { event: chatEventHandler, pending: () => browserInteractions, resolve: resolveBrowserInteraction,
   disconnect: () => typeof handleInteractionDisconnect === 'function' ? handleInteractionDisconnect() : cancelBrowserInteractions() };
 `;
	return new Function(
		'temporary',
		'author',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(temporary, author);
};
const event = (temporary = false) => ({
	chat_id: temporary ? 'temporary-chat' : 'saved-chat',
	message_id: 'reply',
	data: { type: 'request:user_input', data: { interaction_id: 'question', questions: [] } }
});

describe('saved interaction reconnect lifecycle', () => {
	it('replaces an existing interaction callback by identity without creating another card', async () => {
		const chat = createChat();
		const previous = vi.fn(),
			current = vi.fn();
		await chat.event(event(), previous);
		await chat.event(event(), current);
		expect(chat.pending()).toHaveLength(1);
		chat.resolve(chat.pending()[0], { answers: { question: 'yes' } });
		expect(previous).not.toHaveBeenCalled();
		expect(current).toHaveBeenCalledOnce();
	});
	it("does not show another reply author's question to a participant", async () => {
		const chat = createChat(false, 'other');
		await chat.event(event(), vi.fn());
		expect(chat.pending()).toHaveLength(0);
	});
	it('drops stale saved-chat callbacks on disconnect without cancelling server state', async () => {
		const chat = createChat();
		const callback = vi.fn();
		await chat.event(event(), callback);
		chat.disconnect();
		expect(callback).not.toHaveBeenCalled();
		expect(chat.pending()).toHaveLength(0);
	});
	it('cancels ephemeral temporary-chat interactions on disconnect', async () => {
		const chat = createChat(true);
		const callback = vi.fn();
		await chat.event(event(true), callback);
		chat.disconnect();
		expect(callback).toHaveBeenCalledWith({ status: 'cancelled', answers: {} });
		expect(chat.pending()).toHaveLength(0);
	});
});
