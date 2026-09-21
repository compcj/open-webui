import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { get, writable } from 'svelte/store';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { createToolFeatureSaveQueue } from '$lib/utils/tool-feature-preferences';
import { resolveImageGenerationEngineId } from '$lib/utils/image-generation';

// Exercise the real account/save/fetch handlers without mounting the chat editor.
const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const handlerNames = new Set([
	'refreshImageGenerationEngines',
	'persistChatUserSettings',
	'handleImageGenerationEngineChange'
]);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => handlerNames.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const catalog = [{ id: 'studio', name: 'Studio' }];

const createChat = (initialSettings: Record<string, unknown> = {}) => {
	const updateUserSettings = vi.fn(async (_token: string, payload: unknown) => payload);
	const getImageGenerationEngines = vi.fn(async (_token: string) => catalog);
	const toast = { error: vi.fn() };
	const dependencies = {
		get,
		writable,
		initialSettings,
		updateUserSettings,
		getImageGenerationEngines,
		toast,
		resolveImageGenerationEngineId,
		enqueueChatSettingsSave: createToolFeatureSaveQueue()
	};
	const body = `
		const { ${Object.keys(dependencies).join(', ')} } = dependencies;
		let $settings = initialSettings;
		const settings = writable($settings);
		settings.subscribe(value => $settings = value);
		let $user = {id: 'account-a'};
		const user = writable($user);
		user.subscribe(value => $user = value);
		const localStorage = {token: 'token-a'};
		const $i18n = {t: text => text};
		const console = {error: () => {}};
		let imageGenerationEngines = null, imageGenerationEnginesLoading = false;
		let imageEngineAccountId = null, imageEngineRequestVersion = 0;
		${declarations}
		return {
			load: (enabled = true) => refreshImageGenerationEngines($user.id, enabled),
			choose: handleImageGenerationEngineChange,
			switchAccount: () => {user.set({id: 'account-b'}); localStorage.token = 'token-b'; settings.set({});},
			settings: () => get(settings),
			state: () => ({engines: imageGenerationEngines, loading: imageGenerationEnginesLoading,
				selected: resolveImageGenerationEngineId($settings.imageGenerationEngineId, imageGenerationEngines)})
		};
	`;
	const handlers = new Function(
		'dependencies',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(dependencies);
	return { ...handlers, updateUserSettings, getImageGenerationEngines, toast };
};

describe('chat image engine account settings', () => {
	it('saves aliases by stable ID and preserves unrelated UI settings', async () => {
		const chat = createChat({ textScale: 1.1 });
		await chat.load();
		await chat.choose('studio');
		expect(chat.updateUserSettings).toHaveBeenLastCalledWith('token-a', {
			ui: { textScale: 1.1, imageGenerationEngineId: 'studio' }
		});
		await chat.choose('');
		expect(chat.settings()).toEqual({ textScale: 1.1, imageGenerationEngineId: '' });
	});

	it('cancels queued settings writes after the account changes', async () => {
		const chat = createChat();
		await chat.load();
		const pending = chat.choose('studio');
		chat.switchAccount();
		await pending;
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
		expect(chat.settings()).toEqual({});
	});

	it('ignores a previous account catalog response arriving after the new account', async () => {
		const chat = createChat({ imageGenerationEngineId: 'studio' });
		let release!: (value: typeof catalog) => void;
		chat.getImageGenerationEngines.mockImplementationOnce(
			() => new Promise((resolve) => (release = resolve))
		);
		const previous = chat.load();
		chat.switchAccount();
		chat.getImageGenerationEngines.mockResolvedValueOnce([{ id: 'other', name: 'Other' }]);
		await chat.load();
		release(catalog);
		await previous;
		expect(chat.state()).toEqual({
			engines: [{ id: 'other', name: 'Other' }],
			loading: false,
			selected: ''
		});
	});

	it('keeps remembered selections on fetch failure and reports the error', async () => {
		const chat = createChat({ imageGenerationEngineId: 'studio' });
		chat.getImageGenerationEngines.mockRejectedValueOnce(new Error('offline'));
		await chat.load();
		expect(chat.state()).toEqual({ engines: null, loading: false, selected: 'studio' });
		expect(chat.toast.error).toHaveBeenCalledOnce();
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});

	it('restores a saved selection and falls back only after a successful catalog refresh', async () => {
		const chat = createChat({ imageGenerationEngineId: 'studio' });
		await chat.load();
		expect(chat.state().selected).toBe('studio');
		chat.getImageGenerationEngines.mockResolvedValueOnce([]);
		await chat.load();
		expect(chat.state().selected).toBe('');
		expect(chat.updateUserSettings).not.toHaveBeenCalled();
	});
});
