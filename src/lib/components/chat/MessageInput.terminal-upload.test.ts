import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
const source = readFileSync(new URL('./MessageInput.svelte', import.meta.url), 'utf8');
const node: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'uploadTerminalFile');
const createUpload = (cwdPending = false) => {
	const updates = vi.fn(),
		events = vi.fn(),
		uploads = vi.fn();
	const scope = { updates, events, uploads, cwdPending };
	const code = `const {updates,events,uploads,cwdPending}=scope;
 let chatId='old-chat',$_user={id:'actor'},localStorage={token:'token'},$selectedTerminalId='terminal',terminalUploadAlive=true;
 let terminal={url:'https://terminal.example',key:'key',contexts:{}},resolveUpload,resolveCwd;
 const terminalUploadOperations=new WeakMap();const fileItem={terminal_id:'terminal',session_id:'old-chat',file:{},status:'pending',name:'file'};let files=[fileItem];
 const terminalContext=null,$i18n={t:x=>x},toast={error:()=>{}},onUpdate=updates;
 const getFilesystemUploadTerminal=()=>terminal,isSavedChatId=()=>true;
 const getCwd=()=>cwdPending?new Promise(resolve=>resolveCwd=resolve):Promise.resolve({cwd:'/old/'});
 const uploadNewFileToTerminal=(...args)=>{uploads(...args);return new Promise(resolve=>resolveUpload=resolve);};
 const showFileNavDir={set:events};
 const ${source.slice(node.start, node.end)};
 return {run:()=>uploadTerminalFile(fileItem,'old-chat'),state:()=>fileItem,finish:()=>resolveUpload({path:'/old/private.txt'}),cwd:()=>resolveCwd({cwd:'/old/'}),change:kind=>{if(kind==='chat')chatId='new-chat';if(kind==='terminal')$selectedTerminalId='other';if(kind==='account')$_user={id:'other'};if(kind==='token')localStorage.token='other';if(kind==='credential')terminal={...terminal,key:'other'};if(kind==='remove')files=[];if(kind==='destroy')terminalUploadAlive=false;}};`;
	return {
		...new Function(
			'scope',
			transpileModule(code, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(scope),
		updates,
		events,
		uploads
	};
};
describe('terminal upload completion identity', () => {
	it.each(['chat', 'terminal', 'account', 'token', 'credential', 'remove', 'destroy'])(
		'does not publish old results after %s changes',
		async (kind) => {
			const upload = createUpload();
			const pending = upload.run();
			await Promise.resolve();
			upload.change(kind);
			upload.finish();
			await pending;
			expect(upload.events).not.toHaveBeenCalled();
			expect(upload.updates).not.toHaveBeenCalled();
			expect(upload.state().status).not.toBe('uploaded');
		}
	);
	it.each(['chat', 'terminal', 'account', 'token'])(
		'does not begin the upload after %s changes during CWD lookup',
		async (kind) => {
			const upload = createUpload(true);
			const pending = upload.run();
			upload.change(kind);
			upload.cwd();
			await pending;
			expect(upload.uploads).not.toHaveBeenCalled();
			expect(upload.events).not.toHaveBeenCalled();
		}
	);
	it('publishes a valid completion once to the original attachment', async () => {
		const upload = createUpload();
		const pending = upload.run();
		await Promise.resolve();
		upload.finish();
		await pending;
		expect(upload.state().status).toBe('uploaded');
		expect(upload.events).toHaveBeenCalledWith('/old/private.txt');
		expect(upload.updates).toHaveBeenCalledOnce();
	});
});
