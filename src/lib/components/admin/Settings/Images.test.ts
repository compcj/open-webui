import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

import { prepareImageProfiles } from './image-engine-editor';

function handlers(filename: string, names: string[], dependencies: Record<string, any>) {
	const source = readFileSync(new URL(filename, import.meta.url), 'utf8');
	const declarations = parse(source)
		.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
		.filter((node: any) => names.includes(node.id?.name))
		.map((node: any) => `const ${source.slice(node.start, node.end)};`)
		.join('\n');
	const body = `let { ${Object.keys(dependencies).join(', ')} } = dependencies;
		${declarations}
		return { ${names.join(', ')} };`;
	return new Function(
		'dependencies',
		transpileModule(body, {
			compilerOptions: { target: ScriptTarget.ES2022 }
		}).outputText
	)(dependencies);
}

const next = {
	IMAGE_CONNECTIONS: [{ id: 'workspace', config: { access_grants: [] } }],
	IMAGE_PROFILES: [
		{
			id: 'studio',
			name: 'Studio',
			generation_model: 'workspace:artist',
			edit_model: 'disabled',
			tool_description_suffix: '',
			edit_tool_description_suffix: ''
		}
	],
	IMAGE_GENERATION_ENGINES: [{ id: 'studio', api_key: 'legacy-fixture' }],
	IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX: ' Draw\ncarefully ',
	IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX: ' Edit\ncarefully '
};

describe('image settings save', () => {
	it('saves canonical connections, model pairs and independent descriptions', async () => {
		const updateConfig = vi.fn(async (_token, _payload) => null);
		const { save } = handlers('./Images.svelte', ['save'], {
			prepareImageProfiles,
			updateConfig,
			localStorage: { token: 'test' }
		});
		await save(next);
		const payload = updateConfig.mock.calls[0][1];
		expect(payload).toMatchObject({
			IMAGE_CONNECTIONS: next.IMAGE_CONNECTIONS,
			IMAGE_PROFILES: next.IMAGE_PROFILES,
			IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX: 'Draw\ncarefully',
			IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX: 'Edit\ncarefully'
		});
		expect(payload).not.toHaveProperty('IMAGE_GENERATION_ENGINES');
	});
	it('rejects malformed provider parameters before a connection write', async () => {
		const onSubmit = vi.fn();
		const toast = { error: vi.fn() };
		const { submitHandler } = handlers(
			'./Images/ImageConnectionModal.svelte',
			['isJSONObject', 'validate', 'submitHandler'],
			{
				showAdvanced: false,
				loading: false,
				show: true,
				onSubmit,
				toast,
				$i18n: { t: (s: string) => s },
				url: 'https://example.test',
				headers: '',
				params: '{invalid',
				workflow: '',
				editWorkflow: '',
				steps: ''
			}
		);
		await submitHandler();
		expect(toast.error).toHaveBeenCalledWith('Invalid JSON format for Parameters');
		expect(onSubmit).not.toHaveBeenCalled();
	});
});
