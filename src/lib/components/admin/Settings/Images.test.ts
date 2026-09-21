import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./Images.svelte', import.meta.url), 'utf8');
const names = new Set(['parseJSONObject', 'updateConfigHandler']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const setup = () => {
	const config = {
		IMAGE_GENERATION_ENGINE: 'openai',
		IMAGES_OPENAI_API_KEY: 'synthetic-key',
		IMAGES_OPENAI_API_PARAMS: {},
		AUTOMATIC1111_PARAMS: {},
		IMAGE_GENERATION_ENGINES: [{ id: 'studio', params: { quality: 'low' } }]
	};
	const edited = [{ id: 'studio', params: { quality: 'high' } }];
	const imageGenerationEnginesEditor = { getValidatedEngines: vi.fn(() => edited) };
	const updateConfig = vi.fn(async (_token, payload) => payload);
	const toast = { error: vi.fn() };
	const dependencies = { config, imageGenerationEnginesEditor, updateConfig, toast };
	const body = `
		const { ${Object.keys(dependencies).join(', ')} } = dependencies;
		const localStorage = {token: 'synthetic-token'};
		const $i18n = {t: text => text};
		const backendConfig = {set: () => {}};
		const getBackendConfig = async () => ({});
		const getModels = () => {};
		${declarations}
		return updateConfigHandler;
	`;
	const update = new Function(
		'dependencies',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(dependencies);
	return { update, updateConfig, toast, imageGenerationEnginesEditor, edited };
};

describe('image settings update paths', () => {
	it('materializes current engine parameter drafts for connection verification saves', async () => {
		const context = setup();
		await context.update();
		expect(context.updateConfig).toHaveBeenCalledWith(
			'synthetic-token',
			expect.objectContaining({ IMAGE_GENERATION_ENGINES: context.edited })
		);
	});

	it('prevents all configuration writes when an engine draft is invalid', async () => {
		const context = setup();
		context.imageGenerationEnginesEditor.getValidatedEngines.mockImplementation(() => {
			throw new Error('Invalid JSON');
		});
		expect(await context.update()).toBeNull();
		expect(context.updateConfig).not.toHaveBeenCalled();
		expect(context.toast.error).toHaveBeenCalledWith('Invalid JSON');
	});
});
