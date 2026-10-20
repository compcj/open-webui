import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import * as svelte from 'svelte';
// @ts-expect-error Svelte's compiler runtime does not publish type declarations.
import * as server from 'svelte/internal/server';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { ModuleKind, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
import { assembleSettingsTranslations } from '$lib/i18n/settings-translations';

const compileComponent = (source: string, dependencies: Record<string, any> = {}) => {
	const code = transpileModule(compile(source, { generate: 'server' }).js.code, {
		compilerOptions: { module: ModuleKind.CommonJS }
	}).outputText;
	const component = { exports: {} as any };
	new Function('require', 'module', 'exports', code)(
		(name: string) => ({ svelte, 'svelte/internal/server': server, ...dependencies })[name],
		component,
		component.exports
	);
	return component.exports.default;
};

const load = (code: string) =>
	JSON.parse(
		readFileSync(new URL(`../../../i18n/locales/${code}/translation.json`, import.meta.url), 'utf8')
	);
const tooltip = compileComponent(
	'<script>export let content="";</script><div data-tooltip={content}><slot /></div>'
);
const toggle = compileComponent(
	'<script>export let state=false;</script><span data-selected={state}></span>'
);
const component = compileComponent(
	readFileSync(new URL('./ContextToolFeatures.svelte', import.meta.url), 'utf8'),
	{
		'$lib/components/common/Tooltip.svelte': { default: tooltip },
		'$lib/components/common/Switch.svelte': { default: toggle },
		...Object.fromEntries(
			['BookOpen', 'LightBulb', 'Note', 'XMark'].map((name) => [
				`$lib/components/icons/${name}.svelte`,
				{ default: () => {} }
			])
		)
	}
);
const renderFeatures = (props: Record<string, any> = {}, language = 'en-US') => {
	const translations = load(language);
	return render(component, {
		props: {
			state: { knowledge: true, knowledge_write: false, memory: false, notes: false },
			available: { knowledge: true, knowledge_write: true, memory: false, notes: false },
			...props
		},
		context: new Map([['i18n', writable({ t: (key: string) => translations[key] || key })]])
	}).body;
};

describe('Knowledge Write composer control', () => {
	it('offers a separate keyboard button with an explicit unselected state', () => {
		const html = renderFeatures();
		expect(html).toContain('Knowledge Write');
		expect(html).toMatch(/<button[^>]*type="button"[^>]*aria-pressed="false"/);
	});

	it('shows the selected write choice and a visible pause explanation while reading is off', () => {
		const html = renderFeatures({ state: { knowledge: false, knowledge_write: true } });
		expect(html).toContain('Knowledge Write is paused while Knowledge Base is disabled.');
		expect(html).toMatch(/Knowledge Write[\s\S]*data-selected="true"/);
		expect(html).toMatch(/<button[^>]*aria-pressed="true"/);
	});

	it('hides writes when unavailable and keeps a selected paused choice in the compact composer', () => {
		expect(
			renderFeatures({ available: { knowledge: true, knowledge_write: false } })
		).not.toContain('Knowledge Write');
		expect(
			renderFeatures({ compact: true, state: { knowledge: false, knowledge_write: true } })
		).toContain('Knowledge Write is paused while Knowledge Base is disabled.');
	});

	it.each(['en-US', 'en-GB', 'zh-CN', 'zh-TW'])(
		'translates the control and model permission in %s',
		(language) => {
			const locale = load(language);
			for (const key of [
				'Knowledge Write',
				'Create and edit files in knowledge bases',
				'Knowledge Write is paused while Knowledge Base is disabled.',
				'settings.admin.models.builtinTools.knowledgeWrite.label',
				'settings.admin.models.builtinTools.knowledgeWrite.description'
			])
				expect(locale[key]?.trim()).toBeTruthy();
			expect(renderFeatures({}, language)).toContain(locale['Knowledge Write']);
			const fallback = assembleSettingsTranslations({}, load('en-US'));
			expect(fallback['settings.admin.models.builtinTools.knowledgeWrite.label']).toBe(
				'Knowledge Write'
			);
		}
	);
});
