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
const renderStatus = (statusHistory: any[], statusUpdates = true, done = false) => {
	models.set([
		{ id: 'model', info: { meta: { capabilities: { status_updates: statusUpdates } } } }
	]);
	return render(response.default, {
		props: {
			messageId: 'a',
			history: {
				messages: {
					a: {
						id: 'a',
						role: 'assistant',
						model: 'model',
						content: '',
						statusHistory,
						done
					}
				}
			},
			siblings: ['a'],
			readOnly: true
		},
		context: new Map([['i18n', writable({ t: (key: string) => translations[key] || key })]])
	}).body;
};

describe('inline compaction status', () => {
	it('renders localized start, success and failure messages in the response', () => {
		expect(renderStatus([{ action: 'context_compaction', done: false }])).toContain(
			'正在压缩上下文'
		);
		expect(renderStatus([{ action: 'context_compaction', done: true }], true, true)).toContain(
			'上下文已压缩'
		);
		expect(renderStatus([{ action: 'context_compaction', done: true, error: true }])).toContain(
			'上下文压缩失败，原始历史已保留。'
		);
	});
	it('keeps compaction visible when model tool status updates are disabled', () => {
		const html = renderStatus(
			[
				{ action: 'context_compaction', done: true },
				{ action: 'other', done: true, description: 'Hidden tool status' }
			],
			false,
			true
		);
		expect(html).toContain('上下文已压缩');
		expect(html).not.toContain('Hidden tool status');
	});
});
