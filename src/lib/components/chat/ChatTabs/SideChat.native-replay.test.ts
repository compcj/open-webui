import { readFileSync } from 'node:fs';
import {
	createSourceFile,
	forEachChild,
	isVariableDeclaration,
	ScriptTarget,
	transpileModule
} from 'typescript';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./SideChat.svelte', import.meta.url), 'utf8').match(
	/<script[^>]*>([\s\S]*?)<\/script>/
)![1];
const ast = createSourceFile('SideChat.ts', source, ScriptTarget.ES2022, true);
let projection = '';
const findProjection = (node: any) => {
	if (isVariableDeclaration(node) && node.name.getText(ast) === 'conversation') {
		projection = `const ${node.getText(ast)};`;
	}
	forEachChild(node, findProjection);
};
findProjection(ast);
const project = (context: any[], messages: any[] = [], selectedModel = 'native-model') =>
	new Function(
		'context',
		'messages',
		'sideModelId',
		transpileModule(
			`const userMessage = {id:'next',role:'user',content:'next question'};
			const isRasterImageContentType = () => false;
			${projection}
			return conversation;`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)(context, messages, selectedModel);

describe('temporary side-chat native replay projection', () => {
	it.each(['gemini', 'anthropic'])(
		'preserves the %s source model and opaque signed output',
		(protocol) => {
			const output = [
				{
					type: 'function_call',
					call_id: 'call',
					name: 'read',
					arguments: '{}',
					status: 'completed'
				},
				{
					type: 'reasoning',
					reasoning_details: [
						{
							type: 'reasoning.native',
							format: 'open-webui-native-v1',
							data: {
								binding: { protocol, model: 'native-model' },
								blocks:
									protocol === 'gemini'
										? [{ functionCall: { name: 'read', args: {} }, thoughtSignature: 'synthetic' }]
										: [{ type: 'thinking', thinking: 'reason', signature: 'synthetic' }]
							}
						}
					]
				},
				{
					type: 'function_call_output',
					call_id: 'call',
					output: [{ type: 'input_text', text: 'raw' }]
				}
			];
			const original = { id: 'answer', role: 'assistant', model: 'native-model', output };
			const snapshot = structuredClone(original);
			const [replay] = project([original]);
			expect(replay.model).toBe('native-model');
			expect(replay.output).toEqual(snapshot.output);
			expect(original).toEqual(snapshot);
		}
	);

	it('keeps each historical model when the selected model changes', () => {
		const context = [
			{ id: 'parent', role: 'assistant', model: 'parent-model', output: [{ type: 'reasoning' }] }
		];
		const messages = [
			{ id: 'side', role: 'assistant', model: 'side-model', output: [{ type: 'message' }] }
		];
		expect(
			project(context, messages, 'new-model')
				.slice(0, 2)
				.map((message: any) => message.model)
		).toEqual(['parent-model', 'side-model']);
	});
});
