import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import * as svelte from 'svelte';
// @ts-expect-error Svelte's compiler runtime does not publish type declarations.
import * as server from 'svelte/internal/server';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { ModuleKind, transpileModule } from 'typescript';
import equal from 'fast-deep-equal';
import { describe, expect, it } from 'vitest';
import * as structuredOutput from './structuredOutput';
import * as messageAttachments from './messageAttachments';
import * as contextCompaction from '$lib/utils/context-compaction';

const translations = JSON.parse(
	readFileSync(new URL('../../../i18n/locales/zh-CN/translation.json', import.meta.url), 'utf8')
);
const models = writable<any[]>([]);
const dependencies: Record<string, any> = {
	svelte,
	'svelte/internal/server': server,
	'fast-deep-equal': { default: equal },
	'./structuredOutput': structuredOutput,
	'./messageAttachments': messageAttachments,
	'$lib/utils/context-compaction': contextCompaction,
	'$lib/utils/localizedContent': { resolveLocalizedModelName: (model: any) => model.id },
	'$lib/stores': {
		models,
		settings: writable({}),
		config: writable({}),
		user: writable(null),
		audioQueue: writable(null),
		temporaryChatEnabled: writable(false),
		TTSWorker: writable(null)
	},
	'$lib/utils': {
		removeAllDetails: (text: string) => text,
		sanitizeResponseContent: (text: string) => text
	}
};
const compileComponent = (path: string) => {
	const source = readFileSync(new URL(path, import.meta.url), 'utf8');
	const code = transpileModule(compile(source, { generate: 'server' }).js.code, {
		compilerOptions: { module: ModuleKind.CommonJS }
	}).outputText;
	const component = { exports: {} as any };
	new Function('require', 'module', 'exports', code)(
		(name: string) => dependencies[name] ?? (name.endsWith('.svelte') ? { default: () => {} } : {}),
		component,
		component.exports
	);
	return component.exports;
};
dependencies['./StatusHistory/StatusItem.svelte'] = compileComponent(
	'./ResponseMessage/StatusHistory/StatusItem.svelte'
);
dependencies['./ResponseMessage/StatusHistory.svelte'] = compileComponent(
	'./ResponseMessage/StatusHistory.svelte'
);
const response = compileComponent('./ResponseMessage.svelte');
const renderResponse = (message: Record<string, any> = {}, statusUpdates = true) => {
	models.set([
		{ id: 'external-model', info: { meta: { capabilities: { status_updates: statusUpdates } } } }
	]);
	return render(response.default, {
		props: {
			messageId: 'a',
			chatId: 'chat',
			history: {
				currentId: 'a',
				messages: {
					a: {
						id: 'a',
						role: 'assistant',
						model: 'external-model',
						content: '',
						done: false,
						...message
					}
				}
			},
			siblings: ['a'],
			readOnly: true
		},
		context: new Map([['i18n', writable({ t: (key: string) => translations[key] || key })]])
	}).body;
};

const thinking = translations['Thinking...'];

describe('response waiting indicator', () => {
	it.each([{}, { content: ' \n\t' }, { output: [] }])(
		'shows localized activity before a model sends any reasoning or answer: %o',
		(message) => {
			const html = renderResponse(message);
			expect(html).toContain(thinking);
			expect(html).toMatch(/role="status"[^>]*>[\s\S]*?正在思考……/);
			expect(html).not.toContain('animate-cursor-pulse');
		}
	);

	it.each([
		{ content: [] },
		{ content: [{ type: 'output_text', text: '' }] },
		{ content: [{ type: 'output_text', text: ' \n' }] }
	])('keeps the indicator for an empty Responses message envelope: %o', ({ content }) => {
		expect(
			renderResponse({ output: [{ type: 'message', status: 'in_progress', content }] })
		).toContain(thinking);
	});

	it('follows structured rendering when a legacy content field is also present', () => {
		expect(
			renderResponse({
				content: 'Legacy content is not rendered alongside structured output',
				output: [null, { type: 'message', content: [] }]
			})
		).toContain(thinking);
	});

	it.each([
		{ content: 'The answer is arriving.' },
		{ output: [{ type: 'message', content: [{ type: 'output_text', text: 'Answer' }] }] },
		{ output: [{ type: 'reasoning', status: 'in_progress', summary: [] }] },
		{ output: [{ type: 'function_call', name: 'search', status: 'in_progress' }] },
		{ content: '<details type="reasoning"><summary>Thinking...</summary>Reasoning</details>' }
	])('uses the existing content/cursor once content or details render: %o', (message) => {
		const html = renderResponse(message);
		expect(html).not.toContain(thinking);
		expect(html).toContain('animate-cursor-pulse');
	});

	it.each([{ done: true }, { error: true }, { error: { content: 'Connection failed' } }])(
		'hides activity after completion, stopping, or failure: %o',
		(message) => {
			const html = renderResponse(message);
			expect(html).not.toContain(thinking);
			expect(html).not.toContain('animate-cursor-pulse');
		}
	);

	it.each(['pending', 'in_progress'])('does not call a %s user question thinking', (status) => {
		const html = renderResponse({
			output: [{ type: 'function_call', name: 'ask_user', status, call_id: 'ask-1' }]
		});
		expect(html).not.toContain(thinking);
		expect(html).not.toContain('animate-cursor-pulse');
	});

	it.each([
		{ action: 'tool', description: 'Fetching data', done: false },
		{ action: 'context_compaction', done: false },
		{ action: 'tool_result_compaction', done: false },
		{ action: 'knowledge_search', query: 'sample', done: false },
		{ action: 'queries_generated', queries: ['sample'], done: false },
		{ action: 'sources_retrieved', count: 0, done: false }
	])('preserves an active visible status without a duplicate indicator: %o', (status) => {
		expect(renderResponse({ statusHistory: [status] })).not.toContain(thinking);
	});

	it.each([
		{ action: 'tool', description: 'Fetched data', done: true },
		{ action: 'tool', description: 'Fetching data', done: false, hidden: true },
		{ action: 'tool', description: ' \n', done: false }
	])('shows activity when the latest status is completed, hidden, or empty: %o', (status) => {
		expect(renderResponse({ statusHistory: [status] })).toContain(thinking);
	});

	it('shows activity when ordinary status updates are disabled', () => {
		expect(
			renderResponse(
				{ statusHistory: [{ action: 'tool', description: 'Fetching data', done: false }] },
				false
			)
		).toContain(thinking);
	});
});
