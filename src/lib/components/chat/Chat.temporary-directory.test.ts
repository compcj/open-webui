import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { config } from '$lib/stores';
import { resolveTerminalConnection, setCwd } from '$lib/apis/terminal';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const statements: any[] = parse(source).instance!.content.body;
const declarations = statements.flatMap((node) => node.declarations ?? []);
const declaration = (name: string) =>
	declarations
		.filter((node) => node.id?.name === name)
		.map((node) => `const ${source.slice(node.start, node.end)};`)
		.join('\n');
const registration = statements
	.filter(
		(node) =>
			node.expression?.callee?.name === 'setContext' &&
			node.expression.arguments[0]?.value === 'temporary-context'
	)
	.map((node) => source.slice(node.start, node.end))
	.join('\n');

let storage: { token: string; getItem: () => string };
const createChat = (sendCwd = setCwd) =>
	new Function(
		'deps',
		transpileModule(
			`
			const {resolveTerminalConnection, setCwd, localStorage} = deps;
			const contexts = new Map(), setContext = (key, value) => contexts.set(key, value);
			const $config = {features:{enable_tool_servers:true,enable_direct_integrations:true}};
			const $settings = {terminalServers:[{url:'https://terminal.example.test',key:'direct-token'}]};
			const $terminalServers = [{id:'server'}], $i18n = {t: value => value};
			let $user = {id:'actor'}, temporaryTerminalCredential = null, initializedTerminalChatId = null;
			const temporaryController = {
				identity:{id:'side-independent-scope',epoch:'epoch-1',revision:1},
				get chatId(){return this.identity ? 'temporary:' + this.identity.id : null;}
			};
			const prepareTemporaryCopy = () => {};
			${declaration('temporaryContext')}
			${registration}
			${declaration('initializeTerminalDirectory')}
			return {
				initialize:initializeTerminalDirectory,
				context:contexts.get('temporary-context'),
				invalidate:(change) => {
					if(change === 'actor') $user = {id:'other-actor'};
					if(change === 'token') localStorage.token = 'other-token';
					if(change === 'epoch') temporaryController.identity = {...temporaryController.identity,epoch:'epoch-2'};
				}
			};`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)({ resolveTerminalConnection, setCwd: sendCwd, localStorage: storage });

beforeEach(() => {
	storage = { token: 'actor-token', getItem: () => storage.token };
	vi.stubGlobal('localStorage', storage);
	config.set({ features: { enable_tool_servers: true, enable_direct_integrations: true } } as any);
	vi.stubGlobal(
		'fetch',
		vi.fn(async () => new Response(JSON.stringify({ cwd: '/workspace' }), { status: 200 }))
	);
});
afterEach(() => {
	vi.unstubAllGlobals();
});

describe('new chat terminal working directory', () => {
	it('sends the page-owned temporary identity through the real terminal request headers', async () => {
		const chat = createChat();
		await chat.initialize('temporary:side-independent-scope', {
			terminalId: 'server',
			currentDirectory: '/workspace'
		});
		expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/terminals/server/files/cwd'), {
			method: 'POST',
			headers: {
				Authorization: 'Bearer actor-token',
				'Content-Type': 'application/json',
				'X-Session-Id': 'temporary:side-independent-scope',
				'X-Temporary-Context-Id': 'side-independent-scope',
				'X-Temporary-Context-Epoch': 'epoch-1'
			},
			body: JSON.stringify({ path: '/workspace' })
		});
	});

	it.each(['actor', 'token', 'epoch'])(
		'rejects a captured credential after its %s changes',
		async (change) => {
			const chat = createChat((...args) => {
				chat.invalidate(change);
				return setCwd(...args);
			});
			await expect(
				chat.initialize('temporary:side-independent-scope', {
					terminalId: 'server',
					currentDirectory: '/workspace'
				})
			).rejects.toThrow('Terminal context changed');
			expect(fetch).not.toHaveBeenCalled();
		}
	);

	it.each([
		['saved-chat', 'server', 'actor-token'],
		['temporary:side-independent-scope', 'https://terminal.example.test', 'direct-token']
	])('preserves %s credentials for %s', async (chatId, terminalId, token) => {
		await createChat().initialize(chatId, { terminalId, currentDirectory: '/workspace' });
		const [, request] = vi.mocked(fetch).mock.calls[0];
		expect(request?.headers).toEqual({
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json',
			'X-Session-Id': chatId
		});
	});
});
