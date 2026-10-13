import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { get, writable } from 'svelte/store';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import equal from 'fast-deep-equal';
import * as controls from '$lib/utils/model-controls';
import { createToolFeatureSaveQueue } from '$lib/utils/tool-feature-preferences';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set([
	'setModelControl',
	'persistChatUserSettings',
	'saveControls',
	'handleManualCompact'
]);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const model = {
	id: 'm',
	info: {
		params: {
			model_controls: {
				reasoning: {
					label: 'Reasoning',
					options: {
						low: { label: 'Low', params: { reasoning_effort: 'low' } },
						high: { label: 'High', params: { reasoning_effort: 'high' } }
					}
				}
			}
		}
	}
};
const createChat = (shared = false) => {
	const updateUserSettings = vi.fn(async (_token, value) => value);
	const updateChatById = vi.fn(async (_token, _id, value) => ({ chat: value }));
	const compactChatById = vi.fn();
	const enqueueChatSettingsSave = createToolFeatureSaveQueue();
	const scope = {
		...controls,
		get,
		writable,
		equal,
		updateUserSettings,
		updateChatById,
		compactChatById,
		enqueueChatSettingsSave,
		model,
		shared
	};
	const body = `
  const { ${Object.keys(scope).join(', ')} } = scope;
  let $user = { id: 'owner' }; const user = writable($user); user.subscribe(value => $user = value);
  let $settings = { reasoningEffortByModel: { m: 'low' }, params: {} };
  const settings = writable($settings); settings.subscribe(value => $settings = value);
  const localStorage = { token: 'token-owner' }; const $i18n = { t: value => value };
  const toast = { error: () => {} }; const console = { error: () => {} };
  let $chatId = 'chat', isShared = shared, $temporaryChatEnabled = false, chat = { chat: {} }, chatFiles = [];
  let params = {}, reasoningEffortByModel = { m: 'low' }, $models = [model];
  let controlSelections = controlsForRequest(params, $settings.params, $models, reasoningEffortByModel, $settings.reasoningEffortByModel);
  ${declarations}
  return { choose: setModelControl, compact: handleManualCompact, settings, user, localStorage,
   state: () => ({ params, reasoningEffortByModel, settings: $settings }),
   recompute: () => controlSelections = controlsForRequest(params, $settings.params, $models, reasoningEffortByModel, $settings.reasoningEffortByModel),
   block: async gate => enqueueChatSettingsSave(() => gate, () => {})
  };
 `;
	return {
		...new Function(
			'scope',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(scope),
		updateUserSettings,
		updateChatById,
		compactChatById
	};
};

describe('actual chat model-control handlers', () => {
	it('consumes old choices, saves approved options, and keeps reset cleared after replay', async () => {
		const chat = createChat();
		await chat.choose('m', 'reasoning', 'high');
		expect(chat.state().params.model_controls).toEqual({ m: { reasoning: 'high' } });
		chat.recompute();
		await chat.choose('m', 'reasoning', '');
		expect(chat.state().settings.reasoningEffortByModel).toEqual({});
		expect(chat.state().reasoningEffortByModel).toEqual({});
		expect(
			controls.controlsForRequest(
				chat.state().params,
				chat.state().settings.params,
				[model],
				{ m: 'low' },
				{ m: 'low' }
			)
		).toEqual({ m: {} });
		expect(chat.updateUserSettings.mock.calls.at(-1)?.[1].ui.params.model_controls).toEqual({
			m: {}
		});
		expect(chat.updateChatById.mock.calls.at(-1)?.[2].params.model_controls).toEqual({ m: {} });
	});
	it('serializes choices and prevents queued saves crossing account boundaries', async () => {
		const chat = createChat();
		let release!: () => void;
		const gate = new Promise<void>((resolve) => (release = resolve));
		const blocked = chat.block(gate);
		const first = chat.choose('m', 'reasoning', 'low');
		chat.recompute();
		const second = chat.choose('m', 'reasoning', 'high');
		chat.user.set({ id: 'other' });
		chat.localStorage.token = 'token-other';
		release();
		await Promise.all([blocked, first, second]);
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		expect(chat.updateChatById).not.toHaveBeenCalled();
	});
	it('collaborators save their own choices and cannot write owner controls or manually compact', async () => {
		const chat = createChat(true);
		await chat.choose('m', 'reasoning', 'high');
		await chat.compact();
		expect(chat.updateUserSettings).toHaveBeenCalledOnce();
		expect(chat.updateChatById).not.toHaveBeenCalled();
		expect(chat.compactChatById).not.toHaveBeenCalled();
	});
	it('excludes owner parameters, remembered controls and variables at the shared-chat read boundary', () => {
		const chat = {
			user_id: 'owner',
			variables: { secret: 'private' },
			chat: {
				params: { system: 'private', model_controls: { m: { reasoning: 'high' } } },
				reasoning_effort_by_model: { m: 'high' }
			}
		};
		expect(controls.actorChatPreferences(chat, 'collaborator')).toEqual({
			params: {},
			variables: {},
			legacyReasoning: {}
		});
		const actor = controls.actorChatPreferences(chat, 'collaborator');
		expect(
			controls.controlsForRequest(
				actor.params,
				{ model_controls: { m: { reasoning: 'low' } } },
				[model],
				actor.legacyReasoning
			)
		).toEqual({ m: { reasoning: 'low' } });
		expect(controls.actorChatPreferences(chat, 'owner').params.system).toBe('private');
	});
});
