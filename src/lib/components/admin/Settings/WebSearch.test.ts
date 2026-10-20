import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { assembleSettingsTranslations } from '../../../i18n/settings-translations';

const source = readFileSync(new URL('./WebSearch.svelte', import.meta.url), 'utf8');
const component = parse(source);
const script = component
	.instance!.content.body.filter(
		(node: any) => !['ImportDeclaration', 'ExportNamedDeclaration'].includes(node.type)
	)
	.map((node: any) => source.slice(node.start, node.end))
	.join('\n');

const setup = (web: Record<string, unknown>) => {
	let mount: () => Promise<void>;
	const getRAGConfig = vi.fn(async () => ({ web: { ...web } }));
	const updateRAGConfig = vi.fn(async (_token, payload) => structuredClone(payload));
	const dependencies = {
		getRAGConfig,
		updateRAGConfig,
		getContext: () => ({}),
		onMount: (callback: typeof mount) => (mount = callback),
		localStorage: { token: 'synthetic-token' }
	};
	const body = `
		const { ${Object.keys(dependencies).join(', ')} } = dependencies;
		${script}
		return { submit: submitHandler, getConfig: () => webConfig };
	`;
	const handlers = new Function(
		'dependencies',
		transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
	)(dependencies);
	return { ...handlers, mount: () => mount(), updateRAGConfig };
};

const nodes: any[] = [];
const visit = (node: any) => {
	if (!node || typeof node !== 'object') return;
	if (node.type) nodes.push(node);
	for (const value of Object.values(node)) {
		if (Array.isArray(value)) value.forEach(visit);
		else if (value && typeof value === 'object') visit(value);
	}
};
visit(component.html);

describe('Search1API endpoint admin settings', () => {
	it('resolves endpoint labels and help in English and Chinese, with fallback for other locales', () => {
		const localeRoot = new URL('../../../i18n/locales/', import.meta.url);
		const load = (code: string) =>
			JSON.parse(readFileSync(new URL(`${code}/translation.json`, localeRoot), 'utf8'));
		const english = load('en-US');
		const label = 'settings.admin.web.search1apiEndpoint.label';
		const description = 'settings.admin.web.search1apiEndpoint.description';
		expect(english[label]).toBe('Search1API Endpoint');
		for (const code of ['en-US', 'en-GB', 'zh-CN', 'zh-TW']) {
			const translated = load(code);
			expect(translated[label]).toBeTruthy();
			expect(translated[description]).toContain('/search');
			expect(translated[description]).toContain('/ask');
			expect(translated[description]).toContain('10');
		}
		const languages = JSON.parse(readFileSync(new URL('languages.json', localeRoot), 'utf8'));
		for (const { code } of languages) {
			const translated = assembleSettingsTranslations(load(code), english);
			expect(translated[label]).toBeTruthy();
			expect(translated[description]).toBeTruthy();
		}
	});

	it.each([undefined, null])(
		'defaults a missing or null endpoint on load and save (%s)',
		async (endpoint) => {
			const context = setup({ WEB_SEARCH_ENGINE: 'search1api', SEARCH1API_ENDPOINT: endpoint });
			await context.mount();
			expect(context.getConfig().SEARCH1API_ENDPOINT).toBe('search');
			await context.submit();
			expect(context.updateRAGConfig).toHaveBeenCalledWith(
				'synthetic-token',
				expect.objectContaining({ web: expect.objectContaining({ SEARCH1API_ENDPOINT: 'search' }) })
			);
		}
	);

	it.each(['search', 'ask'])(
		'preserves the saved endpoint through provider changes (%s)',
		async (endpoint) => {
			const context = setup({ WEB_SEARCH_ENGINE: 'search1api', SEARCH1API_ENDPOINT: endpoint });
			await context.mount();
			context.getConfig().WEB_SEARCH_ENGINE = 'tavily';
			await context.submit();
			context.getConfig().WEB_SEARCH_ENGINE = 'search1api';
			await context.submit();
			expect(context.getConfig().SEARCH1API_ENDPOINT).toBe(endpoint);
			for (const [, payload] of context.updateRAGConfig.mock.calls) {
				expect(payload.web.SEARCH1API_ENDPOINT).toBe(endpoint);
			}
		}
	);

	it('binds the settings dropdown to the persisted endpoint and offers the provider paths', async () => {
		const select = nodes.find(
			(node) =>
				node.name === 'SettingsSelect' &&
				node.attributes?.some(
					(attribute: any) =>
						attribute.type === 'Binding' &&
						attribute.expression?.property?.name === 'SEARCH1API_ENDPOINT'
				)
		);
		expect(select).toBeDefined();
		const options = select.children.filter((node: any) => node.name === 'option');
		expect(
			options.map(
				(node: any) =>
					node.attributes.find((attribute: any) => attribute.name === 'value').value[0].data
			)
		).toEqual(['search', 'ask']);
		expect(options.map((node: any) => node.children[0].data)).toEqual(['/search', '/ask']);
		const context = setup({ WEB_SEARCH_ENGINE: 'search1api', SEARCH1API_ENDPOINT: 'search' });
		await context.mount();
		const binding = select.attributes.find(
			(attribute: any) => attribute.type === 'Binding'
		).expression;
		// Apply the actual Svelte value binding to the chosen option, then execute the real save handler.
		new Function('webConfig', 'value', `${source.slice(binding.start, binding.end)} = value;`)(
			context.getConfig(),
			options[1].attributes.find((attribute: any) => attribute.name === 'value').value[0].data
		);
		await context.submit();
		expect(context.updateRAGConfig.mock.calls[0][1].web.SEARCH1API_ENDPOINT).toBe('ask');
	});
});
