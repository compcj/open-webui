import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { get, writable } from 'svelte/store';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { changedUiSettings, applyUiSettingsPatch } from '$lib/utils/ui-settings';
import { createToolFeatureSaveQueue } from '$lib/utils/tool-feature-preferences';
const source = readFileSync(new URL('./SettingsModal.svelte', import.meta.url), 'utf8');
const declarations: any[] = parse(source).instance!.content.body.flatMap(
	(node: any) => node.declarations ?? []
);
const declaration = declarations.find((node: any) => node.id?.name === 'saveSettings');
const pendingDeclaration = declarations.find((node: any) => node.id?.name === 'pendingUiSettings');
const setup = (initial: Record<string, unknown> = {}) => {
	const settings = writable(initial),
		user = writable({ id: 'account-a' });
	let stored = { ...initial };
	const updateUserSettings = vi.fn(
		async (_token: string, payload: { ui: Record<string, unknown> }) => {
			stored = applyUiSettingsPatch(stored, payload.ui);
			return { ui: stored };
		}
	);
	const toast = { error: vi.fn() };
	const models = writable<unknown[]>([]);
	const getModels = vi.fn(async () => [] as unknown[]);
	const localStorage = { token: 'synthetic-token' };
	const dependencies = {
		get,
		settings,
		user,
		updateUserSettings,
		toast,
		models,
		getModels,
		localStorage,
		changedUiSettings,
		applyUiSettingsPatch,
		enqueueChatSettingsSave: createToolFeatureSaveQueue()
	};
	const body = `const { ${Object.keys(dependencies).join(', ')} } = dependencies;
 let $settings, $user, personalUiSettings = {};
 settings.subscribe(value => $settings = value); user.subscribe(value => $user = value);
 const $config = { ui: { default_interface_settings: { theme: 'system' } } };
 const ${source.slice(pendingDeclaration.start, pendingDeclaration.end)};
 const ${source.slice(declaration.start, declaration.end)};
 return { saveSettings, switchAccount: () => { user.set({ id: 'account-b' }); settings.set({ theme: 'light' }); } };`;
	const handlers = new Function(
		'dependencies',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(dependencies);
	return { ...handlers, settings, updateUserSettings, toast, models, getModels, localStorage };
};
describe('personal settings save handler', () => {
	it('sends only changed fields and resets an explicit null to its configured default', async () => {
		const modal = setup({ theme: 'dark', memory: true });
		await modal.saveSettings({ theme: 'dark', memory: false });
		expect(modal.updateUserSettings).toHaveBeenLastCalledWith('synthetic-token', {
			ui: { memory: false }
		});
		await modal.saveSettings({ theme: null });
		expect(get(modal.settings)).toMatchObject({ theme: 'system', memory: false });
	});
	it('remembers two quick opposite changes while the first save is pending', async () => {
		const modal = setup({ memory: false });
		let release!: () => void;
		modal.updateUserSettings.mockImplementationOnce(
			async (_token: string, payload: { ui: Record<string, unknown> }) => {
				await new Promise<void>((resolve) => {
					release = resolve;
				});
				return { ui: payload.ui };
			}
		);
		const first = modal.saveSettings({ memory: true });
		await vi.waitFor(() => expect(release).toBeTypeOf('function'));
		const second = modal.saveSettings({ memory: false });
		release();
		await Promise.all([first, second]);
		expect(modal.updateUserSettings).toHaveBeenCalledTimes(2);
		expect((get(modal.settings) as Record<string, unknown>).memory).toBe(false);
	});
	it('ignores the previous account response', async () => {
		const modal = setup({ theme: 'dark' });
		let release!: () => void;
		modal.updateUserSettings.mockImplementationOnce(
			async (_token: string, payload: { ui: Record<string, unknown> }) => {
				await new Promise<void>((resolve) => {
					release = resolve;
				});
				return { ui: payload.ui };
			}
		);
		const pending = modal.saveSettings({ theme: 'system' });
		await vi.waitFor(() => expect(release).toBeTypeOf('function'));
		modal.switchAccount();
		release();
		await pending;
		expect(get(modal.settings)).toEqual({ theme: 'light' });
	});
	it('retries the same field after a failed optimistic save', async () => {
		const modal = setup({ memory: false });
		modal.updateUserSettings.mockRejectedValueOnce('network failure');
		await expect(modal.saveSettings({ memory: true })).rejects.toEqual('network failure');
		await modal.saveSettings({ memory: true });
		expect(modal.updateUserSettings).toHaveBeenCalledTimes(2);
		expect(modal.updateUserSettings).toHaveBeenLastCalledWith('synthetic-token', {
			ui: { memory: true }
		});
		expect(get(modal.settings)).toEqual({ memory: true });
	});
	it('keeps newer pending edits when an older save fails', async () => {
		const modal = setup({ memory: false });
		let reject!: (reason: unknown) => void;
		modal.updateUserSettings.mockImplementationOnce(
			() =>
				new Promise((_resolve, rejectSave) => {
					reject = rejectSave;
				})
		);
		const first = modal.saveSettings({ memory: true });
		const failed = expect(first).rejects.toEqual('network failure');
		await vi.waitFor(() => expect(reject).toBeTypeOf('function'));
		const second = modal.saveSettings({ memory: false, theme: 'dark' });
		const third = modal.saveSettings({ memory: true });
		reject('network failure');
		await failed;
		await Promise.all([second, third]);
		expect(get(modal.settings)).toEqual({ memory: true, theme: 'dark' });
		expect(modal.updateUserSettings).toHaveBeenCalledTimes(3);
		expect(modal.updateUserSettings).toHaveBeenLastCalledWith('synthetic-token', {
			ui: { memory: true }
		});
	});
	it.each(['account', 'token'])('ignores model refresh after a %s switch', async (identity) => {
		const modal = setup({ theme: 'dark' });
		let release!: (models: unknown[]) => void;
		modal.getModels.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					release = resolve;
				})
		);
		const pending = modal.saveSettings({ theme: 'system' });
		await vi.waitFor(() => expect(release).toBeTypeOf('function'));
		if (identity === 'account') modal.switchAccount();
		else modal.localStorage.token = 'account-b-token';
		modal.models.set(['account-b-model']);
		release(['account-a-private-model']);
		await pending;
		expect(get(modal.models)).toEqual(['account-b-model']);
	});
	it('does not retry acknowledged settings after only model refresh fails', async () => {
		const modal = setup({ memory: false });
		modal.getModels.mockRejectedValueOnce('model refresh failure');
		await expect(modal.saveSettings({ memory: true })).rejects.toEqual('model refresh failure');
		await modal.saveSettings({ memory: true });
		expect(modal.updateUserSettings).toHaveBeenCalledTimes(1);
	});
	it('does not carry failed settings into another account', async () => {
		const modal = setup({ memory: false });
		modal.updateUserSettings.mockRejectedValueOnce('network failure');
		await expect(modal.saveSettings({ memory: true })).rejects.toEqual('network failure');
		modal.switchAccount();
		modal.localStorage.token = 'account-b-token';
		await modal.saveSettings({ theme: 'dark' });
		expect(modal.updateUserSettings).toHaveBeenLastCalledWith('account-b-token', {
			ui: { theme: 'dark' }
		});
	});
	it('surfaces failed saves and allows a later request', async () => {
		const modal = setup();
		modal.updateUserSettings.mockRejectedValueOnce('network failure');
		await expect(modal.saveSettings({ memory: true })).rejects.toEqual('network failure');
		expect(modal.toast.error).toHaveBeenCalledWith('network failure');
		await modal.saveSettings({ theme: 'dark' });
		expect(modal.updateUserSettings).toHaveBeenCalledTimes(2);
		expect(modal.updateUserSettings).toHaveBeenLastCalledWith('synthetic-token', {
			ui: { memory: true, theme: 'dark' }
		});
	});
});
