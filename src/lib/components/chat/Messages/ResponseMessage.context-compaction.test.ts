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
			chatId: 'chat',
			history: {
				currentId: 'a',
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

describe('tool result compaction status', () => {
	it('shows summaries and explicit excerpt fallback despite disabled tool statuses', () => {
		expect(
			renderStatus(
				[{ action: 'tool_result_compaction', done: true, method: 'summary' }],
				false,
				true
			)
		).toContain('工具结果已生成摘要');
		expect(
			renderStatus(
				[{ action: 'tool_result_compaction', done: true, method: 'excerpt' }],
				false,
				true
			)
		).toContain('工具结果摘录（不完整）');
	});
});

describe('tool result status review regressions', () => {
	it('renders a localized failure and escaped actionable backend guidance', () => {
		const html = renderStatus(
			[
				{
					action: 'tool_result_compaction',
					done: true,
					error: true,
					description:
						'Reduce the tool batch or increase the context window. <script>unsafe</script>'
				}
			],
			false,
			true
		);
		expect(html).toContain('工具结果处理失败。');
		expect(html).toContain(
			'Reduce the tool batch or increase the context window. &lt;script>unsafe&lt;/script>'
		);
		expect(html).not.toContain('已保留原始工具结果');
		expect(html).not.toContain('<script>unsafe</script>');
	});

	it('shows one original viewer button per result through progress and repeated completion', () => {
		const history = dependencies['./ResponseMessage/StatusHistory.svelte'];
		const html = render(history.default, {
			props: {
				chatId: 'chat',
				branchId: 'a',
				statusHistory: [
					{
						action: 'tool_result_compaction',
						done: false,
						result_id: 'raw-1',
						tool: 'Earlier tool'
					},
					{
						action: 'tool_result_compaction',
						done: true,
						method: 'summary',
						result_id: 'raw-1',
						tool: 'Latest tool'
					},
					{ action: 'tool_result_compaction', done: true, method: 'summary', result_id: 'raw-1' },
					{
						action: 'tool_result_compaction',
						done: true,
						method: 'excerpt',
						result_id: 'raw-2',
						tool: 'Other tool'
					},
					{ action: 'tool_result_compaction', done: true, method: 'original', result_id: null }
				]
			},
			context: new Map([['i18n', writable({ t: (key: string) => translations[key] || key })]])
		}).body;
		expect(html.match(/查看原始工具结果/g)).toHaveLength(2);
		const buttons = [...html.matchAll(/<button[^>]*>[\s\S]*?<\/button>/g)]
			.map((match) => match[0])
			.filter((button) => button.includes('查看原始工具结果'));
		expect(buttons[0]).toContain('Latest tool');
		expect(buttons[0]).not.toContain('Earlier tool');
		expect(buttons[1]).toContain('Other tool');
	});

	it('suppresses original-only completed statuses when ordinary tool statuses are disabled', () => {
		expect(
			renderStatus(
				[
					{ action: 'tool_result_compaction', done: true, method: 'summary' },
					{ action: 'tool_result_compaction', done: true, method: 'original', result_id: null }
				],
				false,
				true
			)
		).toContain('工具结果已生成摘要');
		expect(
			renderStatus(
				[{ action: 'tool_result_compaction', done: true, method: 'original', result_id: null }],
				false,
				true
			)
		).not.toContain('已保留原始工具结果');
	});

	it.each([
		[{ done: false, method: 'original' }, '正在处理工具结果'],
		[{ done: true, method: 'read' }, '工具结果原文片段'],
		[{ done: true, method: 'original', error: true }, '工具结果处理失败。']
	])('preserves required tool result status %o despite the preference', (status, label) => {
		expect(renderStatus([{ action: 'tool_result_compaction', ...status }], false, true)).toContain(
			label
		);
	});

	it('preserves original-result statuses when ordinary tool statuses are enabled', () => {
		expect(
			renderStatus(
				[{ action: 'tool_result_compaction', done: true, method: 'original' }],
				true,
				true
			)
		).toContain('已保留原始工具结果');
	});
});
