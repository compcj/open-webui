import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const spyOnConsoleError = () => vi.spyOn(console, 'error').mockImplementation(() => {});
let consoleError: ReturnType<typeof spyOnConsoleError>;
beforeEach(() => {
	consoleError = spyOnConsoleError();
});
afterEach(() => {
	try {
		expect(consoleError).not.toHaveBeenCalled();
	} finally {
		consoleError.mockRestore();
	}
});
const expectQueueError = () => {
	expect(consoleError).toHaveBeenCalledOnce();
	expect(consoleError).toHaveBeenCalledWith(expect.any(Error));
	consoleError.mockClear();
};
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set([
	'askFile',
	'usesBackendQueue',
	'isSystemOAuthTool',
	'submitHandler',
	'getChatId',
	'confirmWebSearch',
	'processNextInQueue',
	'captureSubmissionGuard',
	'cancelWebSearch',
	'resetWebSearchConfirmation',
	'sendQueuedMessageNow',
	'settleQueuedSubmission',
	'hasPendingQueuedSubmission',
	'submitQueuedMessages'
]);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const createChat = ({ temporary = false, search = false, busy = false } = {}) => {
	const submitted = vi.fn(),
		initChatHandler = vi.fn(),
		setText = vi.fn(),
		openWebSearchConfirm = vi.fn();
	const scope = {
		submitted,
		initChatHandler,
		setText,
		openWebSearchConfirm,
		temporary,
		search,
		busy
	};
	const code = `
 const {${Object.keys(scope).join(',')}}=scope;
 const contextCompactionMode='legacy',temporaryController={chatId:null};
 let $chatId=temporary?'':'saved-chat', $temporaryChat=temporary, $user={id:'actor'},localStorage={token:'token'};
 let history={currentId:busy?'reply':null,messages:busy?{reply:{id:'reply',role:'assistant',done:false}}:{}};
 let prompt='composer draft', files=[{id:'composer-file'}],chatFiles=[],selectedModels=['model'],selectedModelIds=['model'];
 const $models=[{id:'model'}],$config={features:{enable_web_search_confirmation:search}},$settings={enableMessageQueue:true};
 const chat = {user_id:'actor'}, $tools = [{id:'required', auth_type:'oauth'}], $socket={id:'socket'},embedded=false,tabbed=false,fullWidthTabs=false,agentModelId=undefined,onCreateEmbeddedChat=null,chatId={set:async id=>{$chatId=id;}};
 const isTemporaryChatId=id=>id?.startsWith('temporary:'),createTemporaryChatId=()=> 'temporary:synthetic',tick=async()=>{},emitTyping=()=>{},equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 const getChatVariablesForm=()=>({conflicts:[],missing:false,empty:false}),chatVariables={};
 let pendingOAuthTools=[],processing='',webSearchActive=search,webSearchConfirmed=false,pendingWebSearchPrompt=null,pendingWebSearchSubmission=null,pendingWebSearchToggleModelId,webSearchConfirmTimeout,showWebSearchConfirm=false;
 let chatSubmissionAlive=true; const queuedSubmissionGuards=new Map();
 const $i18n={t:x=>x},toast={error:()=>{},warning:()=>{}},messageInput={setText};
 let $chatRequestQueues={}; const chatRequestQueues={update:fn=>{$chatRequestQueues=fn($chatRequestQueues);}};
 const processingQueueChats=new Set(),stoppingChats=new Map(); let seq=0; const uuidv4=()=>String(++seq);
 let transportGate=null,releaseTransport; const submitPrompt=async(text,attached)=>{submitted({text,attached,chatId:$chatId}); await transportGate; return {status:'submitted'};};
 const stopResponse=async()=>true, clearCommandInput=()=>{}, initializeTerminalDirectory=async()=>{}, tabs=null, $selectedTerminalId=null, terminalContext=null;
 initChatHandler.mockImplementation(async()=>{$chatId='created-chat';return $chatId;});
 messageInput.uploadPendingFiles=async()=>{files[0].status='uploaded';return true;};
 ${declarations}
 return {holdTransport:()=>{transportGate=new Promise(resolve=>releaseTransport=resolve);},releaseTransport:()=>releaseTransport(),submit:submitHandler,preparePending:()=>{$chatId='';files=[{id:'upload',status:'pending',file:new File(['synthetic'],'file.txt')}];},ask:()=>askFile('Explain file',{type:'filesystem',terminal_id:'system',path:'/work/file.md',session_id:$chatId||null}),cancel:cancelWebSearch,sendNow:()=>sendQueuedMessageNow(($chatRequestQueues[$chatId]??[])[0]?.id),removeQueued:()=>{$chatRequestQueues[$chatId]=[];},recoverOAuth:()=>{pendingOAuthTools=[];},queueFor:id=>$chatRequestQueues[id]??[],confirm:confirmWebSearch,drain:()=>processNextInQueue($chatId),
 state:()=>({chatId:$chatId,prompt,files,queue:$chatRequestQueues[$chatId]??[]}),
 finish:()=>{history.messages.reply.done=true;},unconfirm:()=>{webSearchConfirmed=false;},
 change:kind=>{if(kind==='chat'){$chatId='other';history={currentId:null,messages:{}};}if(kind==='account')$user={id:'other'};if(kind==='token')localStorage.token='other';if(kind==='model'){selectedModels=['other'];selectedModelIds=['other'];}if(kind==='destroy')chatSubmissionAlive=false;},
 block:kind=>{if(kind==='oauth')pendingOAuthTools=[{id:'required'}];if(kind==='model')selectedModels=[''];}
 };
 `;
	return {
		...new Function(
			'scope',
			transpileModule(code, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(scope),
		submitted,
		initChatHandler,
		setText,
		openWebSearchConfirm
	};
};
describe('file Ask common submission boundary', () => {
	it('initializes temporary transport identity and preserves the composer draft and attachments', async () => {
		const chat = createChat({ temporary: true });
		await chat.ask();
		expect(chat.submitted).toHaveBeenCalledWith(
			expect.objectContaining({
				chatId: 'temporary:synthetic',
				attached: [expect.objectContaining({ path: '/work/file.md' })]
			})
		);
		expect(chat.initChatHandler).not.toHaveBeenCalled();
		expect(chat.state().prompt).toBe('composer draft');
		expect(chat.state().files).toEqual([{ id: 'composer-file' }]);
		expect(chat.setText).not.toHaveBeenCalled();
	});
	it('defers temporary identity and captured file transport until search consent', async () => {
		const chat = createChat({ temporary: true, search: true });
		await chat.ask();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.openWebSearchConfirm).toHaveBeenCalledOnce();
		await chat.confirm();
		expect(chat.submitted).toHaveBeenCalledWith(
			expect.objectContaining({
				chatId: 'temporary:synthetic',
				attached: [expect.objectContaining({ path: '/work/file.md' })]
			})
		);
		expect(chat.state().files).toEqual([{ id: 'composer-file' }]);
	});
	it('checks search consent before enqueue and again when draining a captured file', async () => {
		const chat = createChat({ search: true, busy: true });
		await chat.ask();
		expect(chat.state().queue).toHaveLength(0);
		expect(chat.submitted).not.toHaveBeenCalled();
		await chat.confirm();
		expect(chat.state().queue).toHaveLength(1);
		chat.finish();
		chat.unconfirm();
		await chat.drain();
		expect(chat.submitted).not.toHaveBeenCalled();
		await chat.confirm();
		expect(chat.submitted).toHaveBeenCalledOnce();
		expect(chat.state().prompt).toBe('composer draft');
	});
	it.each(['chat', 'account', 'token', 'model', 'destroy'])(
		'rejects a deferred file after %s changes',
		async (kind) => {
			const chat = createChat({ search: true });
			await chat.ask();
			chat.change(kind);
			await chat.confirm();
			expect(chat.submitted).not.toHaveBeenCalled();
		}
	);
	it('assigns temporary identity before queuing a file request', async () => {
		const chat = createChat({ temporary: true, busy: true });
		await chat.ask();
		expect(chat.state().chatId).toBe('temporary:synthetic');
		expect(chat.state().queue).toHaveLength(1);
		chat.finish();
		await chat.drain();
		expect(chat.submitted).toHaveBeenCalledWith(
			expect.objectContaining({ chatId: 'temporary:synthetic' })
		);
		expect(chat.initChatHandler).not.toHaveBeenCalled();
	});
	it.each(['account', 'token', 'model', 'destroy'])(
		'rejects queued files after %s changes',
		async (kind) => {
			const chat = createChat({ busy: true });
			await chat.ask();
			chat.change(kind);
			chat.finish();
			await chat.drain();
			expect(chat.submitted).not.toHaveBeenCalled();
			expect(chat.state().queue[0].error).toBeTruthy();
			expectQueueError();
		}
	);
	it.each(['oauth', 'model'])('applies %s preflight to file submissions', async (kind) => {
		const chat = createChat();
		chat.block(kind);
		await chat.ask();
		expect(chat.submitted).not.toHaveBeenCalled();
	});
});

describe('queued submission preflight recovery', () => {
	it('retains a queued file for editing when required OAuth changes before drain', async () => {
		const chat = createChat({ busy: true });
		await chat.ask();
		chat.block('oauth');
		chat.finish();
		await chat.drain();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.state().queue).toHaveLength(1);
		expect(chat.state().queue[0].error).toBeTruthy();
		expectQueueError();
	});
});

it('keeps ordinary pending uploads working when first-send initialization adopts a saved ID', async () => {
	const chat = createChat();
	chat.preparePending();
	await chat.submit('Upload this file');
	expect(chat.initChatHandler).toHaveBeenCalledOnce();
	expect(chat.submitted).toHaveBeenCalledWith(expect.objectContaining({ chatId: 'created-chat' }));
});

describe.each(['drain', 'sendNow'] as const)('deferred queued consent ownership: %s', (mode) => {
	const defer = async () => {
		const chat = createChat({ search: true, busy: true });
		await chat.ask();
		await chat.confirm();
		chat.finish();
		chat.unconfirm();
		const entry = structuredClone(chat.state().queue[0]);
		await chat[mode]();
		return { chat, entry };
	};
	it('keeps the exact queued request when resumed OAuth preflight fails, then retries once', async () => {
		const { chat, entry } = await defer();
		chat.block('oauth');
		await chat.confirm();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.state().queue).toHaveLength(1);
		expect(chat.state().queue[0]).toMatchObject(entry);
		expect(chat.state().queue[0].error).toBeTruthy();
		chat.recoverOAuth();
		await chat.sendNow();
		expect(chat.submitted).toHaveBeenCalledOnce();
		expect(chat.state().queue).toHaveLength(0);
		expect(chat.state().prompt).toBe('composer draft');
	});
	it('retains the captured request when the selected model changes during consent', async () => {
		const { chat, entry } = await defer();
		chat.change('model');
		await chat.confirm();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.state().queue[0]).toMatchObject(entry);
		expect(chat.state().queue[0].error).toBeTruthy();
	});
	it('retains the captured request in its original chat on navigation', async () => {
		const { chat, entry } = await defer();
		chat.change('chat');
		await chat.confirm();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.queueFor('saved-chat')[0]).toMatchObject(entry);
		expect(chat.queueFor('saved-chat')[0].error).toBeTruthy();
	});
	it('retains the request on cancellation and allows an explicit retry', async () => {
		const { chat, entry } = await defer();
		chat.cancel();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.state().queue[0]).toMatchObject(entry);
		expect(chat.state().queue[0].error).toBeTruthy();
		await chat.sendNow();
		await chat.confirm();
		expect(chat.submitted).toHaveBeenCalledOnce();
		expect(chat.state().queue).toHaveLength(0);
	});
	it('does not submit or resurrect a queued request deleted while consent is pending', async () => {
		const { chat } = await defer();
		chat.removeQueued();
		await chat.confirm();
		expect(chat.submitted).not.toHaveBeenCalled();
		expect(chat.state().queue).toHaveLength(0);
	});
	it('keeps one owner while consent is open and removes the entry only after successful transport', async () => {
		const { chat, entry } = await defer();
		expect(chat.state().queue[0]).toEqual(entry);
		await chat.drain();
		await chat.sendNow();
		expect(chat.submitted).not.toHaveBeenCalled();
		await chat.confirm();
		expect(chat.submitted).toHaveBeenCalledOnce();
		expect(chat.state().queue).toHaveLength(0);
	});
});

it('holds queue ownership while the confirmed submission is awaiting transport', async () => {
	const chat = createChat({ search: true, busy: true });
	await chat.ask();
	await chat.confirm();
	chat.finish();
	chat.unconfirm();
	await chat.drain();
	chat.holdTransport();
	const submitting = chat.confirm();
	await vi.waitFor(() => expect(chat.submitted).toHaveBeenCalledOnce());
	expect(chat.state().queue).toHaveLength(1);
	const duplicate = chat.sendNow();
	await chat.drain();
	await Promise.resolve();
	expect(chat.submitted).toHaveBeenCalledOnce();
	chat.releaseTransport();
	await Promise.all([submitting, duplicate]);
	expect(chat.submitted).toHaveBeenCalledOnce();
	expect(chat.state().queue).toHaveLength(0);
});
