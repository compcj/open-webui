import { afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { config } from '$lib/stores';
import { createViewSession, serializeChatView } from '$lib/stores/chatViewStates';
import { emptyTabs, openTab } from './ChatTabs/tabs';

afterEach(() => {
	vi.unstubAllGlobals();
});

// Run the component's socket listener with the same page-local stores as the chat.
const chatSource = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const handlers = parse(chatSource)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => ['openBrowserTabRequest', 'chatEventHandler'].includes(node.id?.name))
	.map((node: any) => 'const ' + chatSource.slice(node.start, node.end) + ';')
	.join('\n');
function chat(temporary = false) {
	const open = vi.fn().mockResolvedValue({ session_id: 'new-tab', mode: 'chrome' });
	const receive = vi.fn();
	const state = { id: '' };
	const run = new Function(
		'state',
		'open',
		'receive',
		'temporary',
		transpileModule(
			`
		let $chatId = state.id;
		const chatId = { set: id => { state.id = $chatId = id; } };
		const $socket = { id: 'socket' }, $user = { id: 'alice' }, embedded = false;
		const history = { messages: { reply: {role: 'assistant', user_id: 'alice'} } };
		const terminalTabs = { openBrowserTabForTool: open };
		const temporaryController = { identity: temporary, receive, capture: () => () => true };
		const tick = async () => {}, reportTemporaryError = () => {};
		${handlers}
		return chatEventHandler;`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(state, open, receive.mockResolvedValue(false), temporary);
	const event = {
		chat_id: 'new-chat',
		message_id: 'reply',
		data: {
			type: 'request:open_browser_tab',
			data: { session_id: 'socket', terminal_id: 'selected' }
		}
	};
	return { open, receive, run, state, event };
}

it('lets the first tool call open a tab in its newly assigned chat', async () => {
	const { run, event, state, open } = chat();
	const reply = vi.fn();
	await run(event, reply);
	expect(state.id).toBe('new-chat');
	expect(open).toHaveBeenCalledWith('new-chat', 'selected', expect.any(Function));
	expect(reply).toHaveBeenCalledWith({ session_id: 'new-tab', mode: 'chrome' });
	await run({ ...event, chat_id: 'other-chat' }, reply);
	await run({ ...event, shared: true }, reply);
	await run(
		{ ...event, data: { ...event.data, data: { ...event.data.data, session_id: 'other-socket' } } },
		reply
	);
	expect(open).toHaveBeenCalledTimes(1);
});

it('opens a scoped temporary tab only through its claimed RPC', async () => {
	const { run, event, open, receive } = chat(true);
	const scoped = { ...event, temporary_context: { id: 'scope', epoch: 'epoch' } };
	const reply = vi.fn();
	await run(scoped, reply);
	expect(receive).not.toHaveBeenCalled();
	await run({ ...scoped, rpc_id: 'rpc' }, reply);
	const [envelope, callback, handle] = receive.mock.calls[0];
	expect(open).not.toHaveBeenCalled();
	await handle(envelope, callback, () => false);
	expect(open).not.toHaveBeenCalled();
	await handle(envelope, callback, () => true);
	expect(open).toHaveBeenCalledOnce();
});

it('rechecks the view, actor and selected terminal after browser features load', async () => {
	const source = readFileSync(new URL('./ChatTabs.svelte', import.meta.url), 'utf8');
	const method = parse(source)
		.instance!.content.body.map((node: any) => node.declaration)
		.find((node: any) => node?.id?.name === 'openBrowserTabForTool');
	const open = vi.fn().mockResolvedValue({ id: 'fresh-id', mode: 'chrome' });
	let ready!: () => void;
	const features = new Promise<void>((resolve) => (ready = resolve));
	const viewEntry = { chatId: 'chat', userId: 'alice' };
	const run = new Function(
		'openBrowser',
		'viewEntry',
		'terminalFeaturesReady',
		transpileModule(
			`
		const chatId='chat', $selectedTerminalId='selected', $user={id:'alice'}, promotion=null, viewReady=true;
		${source.slice(method.start, method.end)}
		return openBrowserTabForTool;`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(open, viewEntry, features);
	let current = true;
	const pending = run('chat', 'selected', () => current);
	current = false;
	ready();
	await pending;
	await run('chat', 'other', () => true);
	await run('other-chat', 'selected', () => true);
	viewEntry.userId = 'bob';
	await run('chat', 'selected', () => true);
	expect(open).not.toHaveBeenCalled();
	viewEntry.userId = 'alice';
	await expect(run('chat', 'selected', () => true)).resolves.toEqual({
		session_id: 'fresh-id',
		mode: 'chrome'
	});
	expect(open).toHaveBeenCalledOnce();
	expect(open).toHaveBeenCalledWith(undefined, 'selected');
});

it('opens one blank session, waits for Chrome, and gives the caller the same tab ID as the view', async () => {
	config.set({ features: { enable_tool_servers: true } } as any);
	const connection = { selector: 'terminal', baseUrl: '/terminal', key: 'login', system: true };
	const tab = { id: 'panel-1', kind: 'browser' as const, terminalId: 'terminal', label: 'Browser' };
	const entry = {
		chatId: 'chat-1',
		state: { schemaVersion: 1, panels: openTab(emptyTabs(), tab) },
		pending: new Map(),
		sessionErrors: {},
		revision: 0
	} as any;
	const response = (mode: string) =>
		Response.json({ id: 'session-1', token: 'private-ticket', mode, chrome_available: true });
	let finishChrome!: (value: Response) => void;
	const fetch = vi
		.fn()
		.mockResolvedValueOnce(response('proxy'))
		.mockImplementationOnce(() => new Promise<Response>((resolve) => (finishChrome = resolve)));
	vi.stubGlobal('fetch', fetch);

	const opened = createViewSession(entry, tab.id, connection);
	expect(createViewSession(entry, tab.id, connection)).toBe(opened);
	await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
	expect(entry.state.panels.tabs[0].sessionId).toBeUndefined();
	expect(
		fetch.mock.calls.map(([url, args]) => [url, args.method, args.headers['X-Session-Id']])
	).toEqual([
		['/terminal/browser/sessions', 'POST', 'chat-1'],
		['/terminal/browser/sessions/session-1', 'PATCH', 'chat-1']
	]);
	finishChrome(response('chrome'));
	await expect(opened).resolves.toEqual({ id: 'session-1', mode: 'chrome' });
	expect(entry.state.panels.tabs[0].sessionId).toBe('session-1');
	expect(JSON.stringify(serializeChatView(entry.state))).not.toContain('private-ticket');
});
