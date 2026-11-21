import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { projectRawChat } from '$lib/utils/chat-context-copy';
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const declarations = new Map(
	parse(source)
		.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
		.map((node: any) => [node.id?.name, `const ${source.slice(node.start, node.end)};`])
);
const execute = (names: string[], setup: string, deps: any) =>
	new Function(
		'deps',
		transpileModule(
			setup +
				'\n' +
				names.map((n) => declarations.get(n)).join('\n') +
				'\nreturn {' +
				names.join(',') +
				'};',
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(deps);
const graph = {
	currentId: 'a',
	messages: {
		u: { id: 'u', role: 'user', parentId: null, childrenIds: ['a', 'b'], content: 'question' },
		a: { id: 'a', role: 'assistant', parentId: 'u', model: 'm', done: false },
		b: { id: 'b', role: 'assistant', parentId: 'u', model: 'm', done: false }
	}
};
describe('temporary Chat boundaries', () => {
	it('synchronizes the complete branch graph before sending one generation with the user branch identity', async () => {
		const order: string[] = [];
		const scope = { id: 'scope', epoch: 'epoch', revision: 3 };
		const prepare: any = vi.fn(async () => {
			order.push('sync');
			return { ...scope, branch_id: 'u' };
		});
		const send: any = vi.fn(async () => {
			order.push('send');
			return {};
		});
		const harness = execute(
			['sendMessageSocket'],
			`
   const {prepare,send,scope,graph}=deps;
   const temporaryController={prepare,identity:scope,chatId:'temporary:scope',capture:()=>()=>true};
   let $chatId='temporary:scope',temporaryScope,temporaryConnectionSessionId,history=structuredClone(graph),chatFiles=[];
   const terminalContext=null, embedded=false,$temporaryChat=true,contextCompactionMode='v2',localStorage={token:'captured'},params={},$settings={},$config={features:{}},$user={},$socket={id:'socket'},$selectedTerminalId=null,$selectedFolder=null;
   const $models=[{id:'m'}],model=$models[0],selectedToolIds=[],selectedSkillIds=[],selectedFilterIds=[];
   const chatId={set:async id=>{$chatId=id;}},getTemporaryConnectionSessionId=()=> 'connection';
   const scrollToBottom=()=>{},eventTarget={dispatchEvent:()=>{}},CustomEvent=class{},tick=async()=>{};
   const processDetails=x=>x,getStopTokens=()=>[],getFeatures=()=>({}),getPromptVariables=()=>({});
   const controlAdvancedParams={},controlSelections={},reasoningEffortByModel={},supportsModelControls=()=>false,chatVariables={};
   const WEBUI_BASE_URL='',generateOpenAIChatCompletion=send,onToolCallResolved=()=>{},shouldAutoScrollResponse=()=>false;
  `,
			{ prepare, send, scope, graph }
		);
		await harness.sendMessageSocket(
			{ id: 'm' },
			[],
			structuredClone(graph),
			'a',
			'temporary:scope',
			{
				messageIdsList: [
					{ model_id: 'm', message_id: 'a' },
					{ model_id: 'm', message_id: 'b' }
				]
			}
		);
		expect(order).toEqual(['sync', 'send']);
		expect(prepare.mock.calls[0][0]).toEqual(graph);
		expect(send.mock.calls[0][0]).toBe('captured');
		expect(send.mock.calls[0][1]).toMatchObject({
			temporary_context: { ...scope, branch_id: 'u' },
			chat_id: 'temporary:scope',
			message_ids: [
				{ model_id: 'm', message_id: 'a' },
				{ model_id: 'm', message_id: 'b' }
			]
		});
	});
	const copyHarness = (state: any, stopResult = true) => {
		const stop = vi.fn(async () => stopResult),
			reconcile = vi.fn(async () => state);
		return {
			...execute(
				['prepareTemporaryCopy'],
				`
   const {stop,reconcile,projectRawChat,graph}=deps;
   const temporaryController={capture:()=>()=>true,identity:{id:'scope'},reconcile};
   const $chatId='temporary:scope',$i18n={t:x=>x},history=graph,params={},chatFiles=[];
   let generating=false,taskIds=['active']; const hasPendingAssistantLeaf=()=>true,stopResponse=stop;
  `,
				{ stop, reconcile, projectRawChat, graph }
			),
			stop,
			reconcile
		};
	};
	it('does not project or advance after an unconfirmed stop', async () => {
		const h = copyHarness({ tasks: [{ status: 'registered' }], uncertain: false }, false);
		await expect(h.prepareTemporaryCopy()).rejects.toThrow();
		expect(h.stop).toHaveBeenCalledOnce();
	});
	it('blocks export and promotion on uncertain execution even if no task remains', async () => {
		const h = copyHarness({ tasks: [], uncertain: true });
		await expect(h.prepareTemporaryCopy()).rejects.toThrow();
		expect(h.stop).not.toHaveBeenCalled();
	});
	it('projects all raw branches only after confirmed stop and reconciliation', async () => {
		const h = copyHarness({ tasks: [], uncertain: false });
		const raw = await h.prepareTemporaryCopy();
		expect(h.stop).toHaveBeenCalledOnce();
		expect(h.reconcile).toHaveBeenCalledTimes(2);
		expect(raw.history).toEqual(graph);
	});
});
