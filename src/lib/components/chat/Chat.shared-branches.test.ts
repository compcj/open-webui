import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const node: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'mergeChatMessages');
const createViewer = () =>
	new Function(
		transpileModule(
			`
let history={currentId:'old',messages:{prompt:{id:'prompt',role:'user',parentId:null,childrenIds:['old']},old:{id:'old',role:'assistant',parentId:'prompt',childrenIds:[],done:true,user_id:'viewer'}}};
const generating=false,taskIds=null,$user={id:'viewer'},chat={user_id:'viewer'};let scrolls=0;const autoScrollToBottom=()=>scrolls++;
const ${source.slice(node.start, node.end)};
return {merge:mergeChatMessages,state:()=>({history,scrolls})};`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)();
describe('shared viewer branch selection', () => {
	it.each([
		['assistant', 'prompt'],
		['user', null]
	])('retains selection for a new %s sibling', (role, parentId) => {
		const viewer = createViewer();
		viewer.merge({
			currentId: 'sibling',
			messages: {
				sibling: { id: 'sibling', role, parentId, childrenIds: [], user_id: 'participant' }
			}
		});
		expect(viewer.state().history.currentId).toBe('old');
		expect(viewer.state().history.messages.sibling).toBeDefined();
		expect(viewer.state().scrolls).toBe(0);
	});
	it('follows an ordinary linear continuation', () => {
		const viewer = createViewer();
		viewer.merge({
			currentId: 'next',
			messages: {
				next: { id: 'next', role: 'user', parentId: 'old', childrenIds: [], user_id: 'participant' }
			}
		});
		expect(viewer.state().history.currentId).toBe('next');
		expect(viewer.state().scrolls).toBe(1);
	});
});

describe('shared continuation ancestry', () => {
	const sibling = {
		id: 'sibling',
		role: 'assistant',
		parentId: 'prompt',
		childrenIds: [],
		done: true,
		user_id: 'participant'
	};
	it('keeps the viewer on the selected leaf when a known regenerated branch continues', () => {
		const viewer = createViewer();
		viewer.merge({ currentId: 'sibling', messages: { sibling } });
		viewer.merge({
			currentId: 'next',
			messages: { next: { id: 'next', role: 'user', parentId: 'sibling', childrenIds: [] } }
		});
		expect(viewer.state().history.currentId).toBe('old');
		expect(viewer.state().scrolls).toBe(0);
		expect(viewer.state().history.messages.next).toBeDefined();
	});
	it('preserves selection across a reconnect snapshot continuing a known other branch', () => {
		const viewer = createViewer();
		viewer.merge({ currentId: 'sibling', messages: { sibling } });
		viewer.merge({
			currentId: 'reply',
			messages: {
				...viewer.state().history.messages,
				next: { id: 'next', role: 'user', parentId: 'sibling', childrenIds: ['reply'] },
				reply: { id: 'reply', role: 'assistant', parentId: 'next', childrenIds: [] }
			}
		});
		expect(viewer.state().history.currentId).toBe('old');
		expect(viewer.state().scrolls).toBe(0);
	});
	it('follows a selected-branch reconnect continuation even when the snapshot includes another new sibling', () => {
		const viewer = createViewer();
		viewer.merge({
			currentId: 'reply',
			messages: {
				sibling,
				next: { id: 'next', role: 'user', parentId: 'old', childrenIds: ['reply'] },
				reply: { id: 'reply', role: 'assistant', parentId: 'next', childrenIds: [] }
			}
		});
		expect(viewer.state().history.currentId).toBe('reply');
		expect(viewer.state().scrolls).toBe(1);
	});
	it.each(['missing', 'cycle'])('does not follow unverifiable %s ancestry', (kind) => {
		const viewer = createViewer();
		viewer.merge({
			currentId: 'next',
			messages: {
				next: {
					id: 'next',
					role: 'user',
					parentId: kind === 'cycle' ? 'next' : 'missing',
					childrenIds: []
				}
			}
		});
		expect(viewer.state().history.currentId).toBe('old');
		expect(viewer.state().scrolls).toBe(0);
	});
});
