import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { expect, it, vi } from 'vitest';
import type { ModelParams } from '$lib/apis';
import {
	controlsForRequest,
	migrateModelConfiguration,
	mergeAdvancedParams,
	supportsModelControls
} from './model-controls';
import {
	getAvailableReasoningEffort,
	getEffectiveDefaultReasoningEffort,
	resolveReasoningEffortOverride
} from './reasoning-effort';

const source = readFileSync(new URL('../components/chat/Chat.svelte', import.meta.url), 'utf8');
const declaration = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'sendMessageSocket');
const sendDeclaration = `const ${source.slice(declaration.start, declaration.end)};`;

it('sends canonical legacy pipe reasoning through the actual chat fallback', async () => {
	const original = {
		id: 'pipe-preset',
		base_model_id: 'pipe-base',
		params: { reasoning_effort: 'low' } as ModelParams,
		meta: { available_reasoning_effort: ['low', 'very high/自定义'] }
	};
	const migrated = migrateModelConfiguration(original);
	const model = { id: original.id, pipe: {}, info: migrated };
	const available = getAvailableReasoningEffort(model);
	expect(available).toEqual(['low', 'very high/自定义']);
	const effort = resolveReasoningEffortOverride('very high/自定义', available)!;
	const controlSelections = controlsForRequest({}, {}, [model], { [model.id]: effort });
	const generateOpenAIChatCompletion = vi.fn(
		async (_token: string, _body: any, _baseUrl: string) => null
	);
	const scope = {
		model,
		controlSelections,
		effort,
		generateOpenAIChatCompletion,
		supportsModelControls
	};
	const body = `
		const { model, controlSelections, effort, generateOpenAIChatCompletion, supportsModelControls } = scope;
		let chatFiles = [];
		const params = {}, controlAdvancedParams = { reasoning_effort: 'low' };
		const reasoningEffortByModel = { [model.id]: effort }, $models = [model];
		const $settings = {}, $config = { features: {} }, $user = {}, $socket = {};
		const $temporaryChatEnabled = false, $selectedFolder = null, embedded = false;
		const selectedToolIds = [], selectedSkillIds = [], selectedFilterIds = [];
		const localStorage = { token: 'synthetic-token' }, WEBUI_BASE_URL = '';
		const tick = async () => {}, scrollToBottom = () => {}, shouldAutoScrollResponse = () => false;
		const eventTarget = { dispatchEvent: () => {} }, CustomEvent = class {};
		const getStopTokens = () => [], getFeatures = () => ({}), getPromptVariables = () => ({});
		const isTemporaryChatId = () => false;
		${sendDeclaration}
		return () => sendMessageSocket(model, [], {
			messages: {
				user: { id: 'user', role: 'user', content: 'Synthetic prompt', parentId: null },
				reply: { id: 'reply', role: 'assistant', parentId: 'user' }
			}
		}, 'reply', 'saved-chat');
	`;
	const send = new Function(
		'scope',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(scope);
	await send();
	expect(generateOpenAIChatCompletion).toHaveBeenCalledOnce();
	expect(generateOpenAIChatCompletion.mock.calls[0][1].params).toMatchObject({
		reasoning_effort: 'very high/自定义',
		model_controls: {}
	});
	expect(original.meta.available_reasoning_effort).toEqual(['low', 'very high/自定义']);
});

const componentSource = readFileSync(
	new URL('../components/chat/MessageInput.svelte', import.meta.url),
	'utf8'
);
function findReasoningDefault(node: any): any {
	if (node?.type === 'Attribute' && node.name === 'defaultEffort') return node.value[0].expression;
	if (node && typeof node === 'object') {
		for (const value of Object.values(node)) {
			const found = findReasoningDefault(value);
			if (found) return found;
		}
	}
}
const defaultExpression = findReasoningDefault(parse(componentSource).html);
const resolveComposerDefault = new Function(
	'scope',
	`const { model, $settings, modelControlAdvancedParams, getEffectiveDefaultReasoningEffort } = scope;
	return ${componentSource.slice(defaultExpression.start, defaultExpression.end)};`
);

it('the actual composer pipe default label uses merged chat and personal advanced parameters', () => {
	const model = { pipe: {}, info: { meta: { default_reasoning_effort: 'medium' } } };
	const personal = { custom_params: { reasoning_effort: 'low' } };
	const chat = { reasoning_effort: 'high' };
	expect(
		resolveComposerDefault({
			model,
			$settings: { params: personal },
			modelControlAdvancedParams: mergeAdvancedParams(personal, chat),
			getEffectiveDefaultReasoningEffort
		})
	).toBe('high');
});
