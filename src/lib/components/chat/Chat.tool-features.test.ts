import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { get, writable } from 'svelte/store';
import { transpileModule, ScriptTarget } from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';
import equal from 'fast-deep-equal';
import * as preferences from '../../utils/tool-feature-preferences';

// Run the actual chat handlers without starting the editor, sockets or backend.
// Rendering/bindings still need browser verification, as in NoteEditor.test.ts.
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const ast = parse(source);
const handlers = new Set([
	'getToolFeatureContext',
	'applyToolFeatureDefaults',
	'persistChatUserSettings',
	'persistToolFeaturePreference',
	'persistLastUsedReasoningEffort',
	'handleWebSearchToggle',
	'handleImageGenerationToggle',
	'openWebSearchConfirm',
	'resetWebSearchConfirmation',
	'confirmWebSearch',
	'cancelWebSearch',
	'resetInput',
	'setDefaults',
	'restoreChatInput',
	'getFeatures',
	'isTerminalAvailable'
]);
const declarations = ast
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => handlers.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createChat = (initialSettings: Record<string, any> = {}) => {
	const updateUserSettings = vi.fn(async (_token, payload) => structuredClone(payload));
	const toast = { error: vi.fn() };
	const submitHandler = vi.fn();
	const getTools = vi.fn(async () => []);
	const dependencies = {
		...preferences,
		get,
		writable,
		equal,
		updateUserSettings,
		toast,
		submitHandler,
		getTools,
		initialSettings
	};
	const body = `
		const { ${Object.keys(dependencies).join(', ')} } = dependencies;
		let $settings = initialSettings;
		const settings = writable($settings);
		settings.subscribe(value => $settings = value);
		let $user = { id: 'user-a', role: 'admin' };
		const user = writable($user);
		user.subscribe(value => $user = value);
		const localStorage = { token: 'synthetic-token' };
		const window = { setTimeout, clearTimeout };
		const $i18n = { t: text => text };
		const console = { error: () => {} };
		let $config = { features: {
			enable_web_search: true, enable_image_generation: true,
			enable_code_interpreter: true, enable_web_search_confirmation: false
		} };
		let $models = ['a', 'b'].map(id => ({ id, info: { meta: {
			capabilities: { web_search: true, image_generation: true }, defaultFeatureIds: []
		} } }));
		let selectedModels = ['a'], selectedModelIds = ['a'], atSelectedModel;
		let webSearchEnabled = false, imageGenerationEnabled = false, codeInterpreterEnabled = false;
		let webSearchActive = false, webSearchConfirmed = false, showWebSearchConfirm = false;
		let pendingWebSearchPrompt = null, pendingWebSearchToggleModelId;
		let webSearchConfirmTimeout;
		let selectedToolIds = [], selectedSkillIds = [], selectedFilterIds = [], pendingOAuthTools = [];
		let $tools = [], $functions = [], $skills = [], $terminalServers = [];
		const tools = { set: value => $tools = value };
		const functions = { set: value => $functions = value };
		const skills = { set: value => $skills = value };
		const getFunctions = async () => [], getSkills = async () => [];
		const continueOAuthRedirect = async () => {};
		const selectedTerminalId = { set: () => {} };
		let settingDefaults = false, $temporaryChatEnabled = false, $showCallOverlay = false;
		let chatIdProp = 'history-chat', prompt = '', files = [], messageInput;
		const handleToolApprovalModeChange = async () => {};
		${declarations}
		return {
			resetInput, setDefaults, restoreChatInput, handleWebSearchToggle, confirmWebSearch, getFeatures,
			imageToggle: enabled => handleImageGenerationToggle(enabled),
			reasoning: (modelId, value) => persistLastUsedReasoningEffort(modelId, value),
			cancel: () => cancelWebSearch(),
			state: () => ({ webSearchEnabled, imageGenerationEnabled, webSearchConfirmed, showWebSearchConfirm, settings: $settings }),
			select: (ids, atId) => { selectedModels = ids; atSelectedModel = $models.find(m => m.id === atId); selectedModelIds = atId ? [atId] : ids; },
			config: $config, models: $models, settings, user, localStorage,
			newChat: () => { chatIdProp = ''; return resetInput(); },
			pendingSubmit: text => { pendingWebSearchPrompt = text; },
			unloadTools: () => { $tools = null; },
			setSearchActive: active => { webSearchActive = active; }
		};
	`;
	const chat = new Function(
		'dependencies',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(dependencies);
	return { ...chat, updateUserSettings, toast, submitHandler, getTools };
};

afterEach(() => {
	vi.useRealTimers();
});

describe('chat tool feature memory', () => {
	it('restores each model and @ override without writing settings', async () => {
		const chat = createChat({
			toolFeaturesByModel: {
				a: { web_search: true, image_generation: false },
				b: { web_search: false, image_generation: true }
			}
		});
		await chat.resetInput();
		expect(chat.state()).toMatchObject({ webSearchEnabled: true, imageGenerationEnabled: false });
		chat.select(['a'], 'b');
		await chat.resetInput();
		expect(chat.state()).toMatchObject({ webSearchEnabled: false, imageGenerationEnabled: true });
		chat.select(['a']);
		await chat.resetInput();
		expect(chat.state().webSearchEnabled).toBe(true);
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});

	it('saves explicit changes without dropping unrelated account settings', async () => {
		const chat = createChat({ textScale: 1.2, reasoningEffortByModel: { a: 'high' } });
		await chat.handleWebSearchToggle(true);
		await chat.imageToggle(false);
		expect(chat.state().settings).toEqual({
			textScale: 1.2,
			reasoningEffortByModel: { a: 'high' },
			toolFeaturesByModel: { a: { web_search: true, image_generation: false } }
		});
		expect(chat.updateUserSettings).toHaveBeenLastCalledWith('synthetic-token', {
			ui: chat.state().settings
		});
	});

	it('keeps multi-model toggles local and leaves their remembered preferences alone', async () => {
		const chat = createChat({ toolFeaturesByModel: { a: { web_search: true } } });
		chat.select(['a', 'b']);
		await chat.resetInput();
		expect(chat.state().webSearchEnabled).toBe(false);
		await chat.handleWebSearchToggle(true);
		expect(chat.state().webSearchEnabled).toBe(true);
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});

	it('honors historical draft values while filling missing values from memory', async () => {
		const chat = createChat({
			toolFeaturesByModel: { a: { web_search: true, image_generation: true } }
		});
		await chat.restoreChatInput(JSON.stringify({ prompt: 'draft', webSearchEnabled: false }));
		expect(chat.state()).toMatchObject({ webSearchEnabled: false, imageGenerationEnabled: true });
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		await chat.newChat();
		await chat.restoreChatInput(JSON.stringify({ webSearchEnabled: false }));
		expect(chat.state().webSearchEnabled).toBe(true);
	});

	it('does not let a delayed default load overwrite a newer explicit toggle', async () => {
		const chat = createChat();
		let resolveTools!: (tools: any[]) => void;
		chat.getTools.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					resolveTools = resolve;
				})
		);
		chat.unloadTools();
		const initializing = chat.resetInput();
		await chat.handleWebSearchToggle(true);
		resolveTools([]);
		await initializing;
		expect(chat.state().webSearchEnabled).toBe(true);
	});

	it('waits for confirmation before remembering search and cancels without saving', async () => {
		vi.useFakeTimers();
		const chat = createChat();
		chat.config.features.enable_web_search_confirmation = true;
		await chat.handleWebSearchToggle(true);
		await vi.runAllTimersAsync();
		expect(chat.state()).toMatchObject({ webSearchEnabled: false, showWebSearchConfirm: true });
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		chat.cancel();
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		await chat.handleWebSearchToggle(true);
		await chat.confirmWebSearch();
		expect(chat.state().settings.toolFeaturesByModel.a.web_search).toBe(true);
	});

	it('invalidates a pending enable when switching models', async () => {
		vi.useFakeTimers();
		const chat = createChat();
		chat.config.features.enable_web_search_confirmation = true;
		await chat.handleWebSearchToggle(true);
		chat.select(['b']);
		await chat.resetInput();
		await vi.runAllTimersAsync();
		await chat.confirmWebSearch();
		expect(chat.state()).toMatchObject({ webSearchEnabled: false, showWebSearchConfirm: false });
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});

	it('restores search without remembering consent and does not save submission confirmation', async () => {
		const chat = createChat({ toolFeaturesByModel: { a: { web_search: true } } });
		await chat.resetInput();
		expect(chat.state()).toMatchObject({ webSearchEnabled: true, webSearchConfirmed: false });
		chat.pendingSubmit('synthetic question');
		await chat.confirmWebSearch();
		expect(chat.submitHandler).toHaveBeenCalledWith('synthetic question');
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});

	it.each(['capability', 'config', 'permission'])(
		'gates remembered features by %s without erasing them',
		async (gate) => {
			const remembered = { a: { web_search: true, image_generation: true } };
			const chat = createChat({ toolFeaturesByModel: remembered });
			if (gate === 'capability')
				chat.models[0].info.meta.capabilities = { web_search: false, image_generation: false };
			if (gate === 'config') {
				chat.config.features.enable_web_search = false;
				chat.config.features.enable_image_generation = false;
			}
			if (gate === 'permission')
				chat.user.set({
					id: 'user-a',
					role: 'user',
					permissions: { features: { web_search: false, image_generation: false } }
				});
			await chat.resetInput();
			expect(chat.state()).toMatchObject({
				webSearchEnabled: false,
				imageGenerationEnabled: false
			});
			expect(chat.getFeatures()).toMatchObject({ web_search: false, image_generation: false });
			expect(chat.state().settings.toolFeaturesByModel).toEqual(remembered);
			expect(chat.updateUserSettings).not.toHaveBeenCalled();
		}
	);

	it('skips a queued save after the account changes', async () => {
		const chat = createChat();
		const saving = chat.imageToggle(true);
		chat.user.set({ id: 'user-b', role: 'admin' });
		chat.settings.set({});
		await saving;
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		expect(chat.state().settings).toEqual({});
	});

	it('retains the current selection and reports a save failure', async () => {
		const chat = createChat();
		chat.updateUserSettings.mockRejectedValueOnce(new Error('network failure'));
		await chat.handleWebSearchToggle(true);
		expect(chat.state().webSearchEnabled).toBe(true);
		expect(chat.state().settings.toolFeaturesByModel.a.web_search).toBe(true);
		expect(chat.toast.error).toHaveBeenCalledWith('Failed to update settings');
		await chat.imageToggle(true);
		expect(chat.updateUserSettings).toHaveBeenCalledTimes(2);
	});

	it.each(['feature', 'reasoning'])(
		'preserves concurrent settings when %s is saved first',
		async (first) => {
			const chat = createChat();
			let releaseFirst!: () => void;
			const firstResponse = new Promise<void>((resolve) => {
				releaseFirst = resolve;
			});
			let stored: any;
			let calls = 0;
			chat.updateUserSettings.mockImplementation(
				async (_token: string, payload: { ui: Record<string, unknown> }) => {
					const snapshot = structuredClone(payload);
					if (++calls === 1) await firstResponse;
					stored = snapshot.ui;
					return snapshot;
				}
			);
			const changeFeature = () => chat.handleWebSearchToggle(true);
			const changeReasoning = () => chat.reasoning('a', 'high');
			const firstSave = first === 'feature' ? changeFeature() : changeReasoning();
			await vi.waitFor(() => expect(calls).toBe(1));
			const secondSave = first === 'feature' ? changeReasoning() : changeFeature();
			await Promise.resolve();
			releaseFirst();
			await Promise.all([firstSave, secondSave]);
			expect(stored).toMatchObject({
				toolFeaturesByModel: { a: { web_search: true } },
				reasoningEffortByModel: { a: 'high' }
			});
		}
	);
});
