import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { expect, it } from 'vitest';
import { isTemporaryChatId } from '$lib/utils/chatId';
const source = readFileSync(
	new URL('./MessageInput/Commands/AtCommands.svelte', import.meta.url),
	'utf8'
);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => ['chatContext', 'getSelectedTerminal'].includes(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const select = (id: string, scope: string | null, enabled = true) =>
	new Function(
		'isTemporaryChatId',
		'id',
		'scope',
		'enabled',
		transpileModule(
			`const filesystemEnabled=enabled,$activeChatId=id,$selectedTerminalId='admin',localStorage={token:'token'},$settings={},$config={features:{enable_tool_servers:true}};const $terminalServers=[{id:'admin',url:'/api/v1/terminals/admin',contexts:{chat:{context_id:'chat_id'}}}];const temporaryContext=scope?{chatId:scope,terminalCredential:()=>({token:'token',temporaryContext:{id:'scope',epoch:'epoch'}})}:null;${declarations}; return getSelectedTerminal();`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(isTemporaryChatId, id, scope, enabled);
it('allows a per-chat terminal for the authenticated page scope', () => {
	expect(select('temporary:scope', 'temporary:scope')).toMatchObject({
		url: '/api/v1/terminals/admin',
		key: { temporaryContext: { id: 'scope', epoch: 'epoch' } }
	});
});
it('keeps unscoped temporary chats excluded', () => {
	expect(select('temporary:old', null)).toBeNull();
});
it('keeps saved chat terminal resolution unchanged', () => {
	expect(select('saved', null)).toMatchObject({ key: 'token' });
});
it('does not offer filesystem mentions in a composer with terminal selection disabled', () => {
	expect(select('saved', null, false)).toBeNull();
});
