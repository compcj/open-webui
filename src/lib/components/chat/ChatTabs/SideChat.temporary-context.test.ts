import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TemporaryContextController } from '$lib/utils/temporary-context';
import { getChatToolResult } from '$lib/apis/chats';
import { readFile } from '$lib/apis/terminal';
import { config } from '$lib/stores';

const source = readFileSync(new URL('./SideChat.svelte', import.meta.url), 'utf8');
const statements: any[] = parse(source).instance!.content.body;
const credentialDeclaration = statements
	.flatMap((node) => node.declarations ?? [])
	.filter((node) => node.id?.name === 'temporaryTerminalCredential')
	.map((node) => `let ${source.slice(node.start, node.end)};`)
	.join('\n');
const registration = statements
	.filter(
		(node: any) =>
			node.expression?.callee?.name === 'setContext' &&
			node.expression.arguments[0]?.value === 'temporary-context'
	)
	.map((node: any) => source.slice(node.start, node.end))
	.join('\n');
const viewerSource = readFileSync(
	new URL('../Messages/ResponseMessage/ToolResultViewer.svelte', import.meta.url),
	'utf8'
);
const loader: any = parse(viewerSource)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'loadPage');
const fileSource = readFileSync(
	new URL('../Messages/ToolOutputFile.svelte', import.meta.url),
	'utf8'
);
const terminalResolver: any = parse(fileSource).instance!.content.body.find(
	(node: any) => node.id?.name === 'resolveTerminal'
);

const createSideView = () => {
	const actor = { id: 'actor' };
	const controller = new TemporaryContextController({
		actor: () => ({ id: actor.id, token: localStorage.token }),
		session: () => 'socket',
		applyEvent: () => {},
		applyState: () => {},
		cancelInteractions: () => {},
		onError: () => {},
		interval: () => 1,
		clearInterval: () => {}
	});
	const inheritedContext = { chatId: 'temporary:main-scope', identity: { id: 'main-scope' } };
	const context = new Function(
		'temporaryController',
		'parent',
		'$user',
		transpileModule(
			`const contexts = new Map([['temporary-context',parent]]);
			const setContext = (key,value) => contexts.set(key,value);
			${credentialDeclaration}
			${registration}
			return contexts.get('temporary-context');`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(controller, inheritedContext, actor);
	const viewer = new Function(
		'temporaryContext',
		'getChatToolResult',
		transpileModule(
			`let chatId = 'temporary:side-scope', page = null, activeQuery = 'needle', loading = false, error = '', requestVersion = 0;
			const branchId = 'side-reply', resultId = 'raw-result';
			const ${viewerSource.slice(loader.start, loader.end)};
			return {loadPage,state:()=>({page,error}),save:(id)=>{chatId=id;}};`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(context, getChatToolResult);
	const resolveFile = (chatId: string) =>
		new Function(
			'temporaryContext',
			'chatId',
			transpileModule(
				`const selector = 'server', signedDelivery = true, $selectedTerminalId = 'other-terminal';
				const $terminalServers = [{id:'server',url:'/api/v1/terminals/server'}];
				const $settings = {}, $config = {features:{}};
				${fileSource.slice(terminalResolver.start, terminalResolver.end)}
				return resolveTerminal();`,
				{ compilerOptions: { target: ScriptTarget.ES2022 } }
			).outputText
		)(context, chatId);
	return { actor, controller, context, viewer, resolveFile };
};

beforeEach(() => {
	vi.stubGlobal('localStorage', { token: 'actor-token' });
	config.set({ features: { enable_tool_servers: true } } as any);
	vi.stubGlobal(
		'fetch',
		vi.fn(async (url: string, request: RequestInit) => {
			if (url.includes('/files/read?'))
				return new Response(JSON.stringify({ content: 'terminal original' }), { status: 200 });
			const payload = url.endsWith('/temporary-contexts/')
				? { id: 'side-scope', epoch: 'side-epoch', revision: 1 }
				: request.method === 'DELETE'
					? { id: 'side-scope', epoch: 'side-epoch', revision: 2, status: 'closed_confirmed' }
					: {
							text: 'exact original',
							start: 0,
							end: 14,
							total_chars: 14,
							source: { name: 'tool' }
						};
			return new Response(JSON.stringify(payload), { status: 200 });
		})
	);
});

describe('side-chat terminal output credentials', () => {
	it('lets ToolOutputFile resolve and read a file with its own scope headers', async () => {
		const view = createSideView();
		await view.controller.ensure();
		const terminal = view.resolveFile('temporary:side-scope');
		expect(view.resolveFile('temporary:side-scope').key).toBe(terminal.key);
		await expect(
			readFile(terminal.url, terminal.key, '/workspace/result.txt', 'temporary:side-scope')
		).resolves.toBe('terminal original');
		expect(vi.mocked(fetch).mock.calls.at(-1)).toEqual([
			'/api/v1/terminals/server/files/read?path=%2Fworkspace%2Fresult.txt',
			{
				headers: {
					Authorization: 'Bearer actor-token',
					'X-Session-Id': 'temporary:side-scope',
					'X-Temporary-Context-Id': 'side-scope',
					'X-Temporary-Context-Epoch': 'side-epoch'
				}
			}
		]);
	});

	it('keeps saved output requests free of another active temporary identity', async () => {
		const view = createSideView();
		await view.controller.ensure();
		const terminal = view.resolveFile('saved-side-chat');
		await readFile(terminal.url, terminal.key, '/workspace/result.txt', 'saved-side-chat');
		expect(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.headers).toEqual({
			Authorization: 'Bearer actor-token',
			'X-Session-Id': 'saved-side-chat'
		});
	});

	it.each(['actor', 'token', 'id', 'epoch', 'close'])(
		'rejects captured output credentials after %s changes',
		async (change) => {
			const view = createSideView();
			await view.controller.ensure();
			const terminal = view.resolveFile('temporary:side-scope');
			if (change === 'actor') view.actor.id = 'other-actor';
			if (change === 'token') localStorage.token = 'other-token';
			if (change === 'id')
				view.controller.identity = { ...view.controller.identity!, id: 'other-scope' };
			if (change === 'epoch')
				view.controller.identity = { ...view.controller.identity!, epoch: 'new-epoch' };
			if (change === 'close') await view.controller.close();
			vi.mocked(fetch).mockClear();
			await expect(
				readFile(terminal.url, terminal.key, '/workspace/result.txt', 'temporary:side-scope')
			).rejects.toThrow('Terminal context changed');
			expect(fetch).not.toHaveBeenCalled();
		}
	);
});
afterEach(() => {
	vi.unstubAllGlobals();
});

describe('side-chat original tool results', () => {
	it('routes the viewer through its own controller and temporary result API', async () => {
		const { controller, viewer } = createSideView();
		await controller.ensure();
		await viewer.loadPage('next-page');
		const [url, request] = vi.mocked(fetch).mock.calls.at(-1)!;
		const target = new URL(String(url), 'https://webui.example.test');
		expect(target.pathname).toBe('/api/v1/temporary-contexts/side-scope/tool-results/raw-result');
		expect(Object.fromEntries(target.searchParams)).toEqual({
			epoch: 'side-epoch',
			branch_id: 'side-reply',
			query: 'needle',
			cursor: 'next-page'
		});
		expect(request?.headers).toMatchObject({ authorization: 'Bearer actor-token' });
		expect(viewer.state()).toMatchObject({ page: { text: 'exact original' }, error: '' });
	});

	it('keeps identity getters current when the scope is created and released', async () => {
		const { controller, context, viewer } = createSideView();
		expect(context.chatId).toBeNull();
		expect(context.identity).toBeNull();
		await controller.ensure();
		expect(context.chatId).toBe('temporary:side-scope');
		expect(context.identity).toBe(controller.identity);
		await controller.close();
		expect(context.chatId).toBeNull();
		expect(context.identity).toBeNull();
		viewer.save('saved-side-chat');
		await viewer.loadPage();
		expect(vi.mocked(fetch).mock.calls.at(-1)?.[0]).toContain(
			'/chats/saved-side-chat/tool-results/raw-result?'
		);
		expect(viewer.state().error).toBe('');
	});
});
