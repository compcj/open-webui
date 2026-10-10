import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import * as svelte from 'svelte';
// @ts-expect-error Svelte's compiler runtime does not publish type declarations.
import * as server from 'svelte/internal/server';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { ModuleKind, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
import * as compaction from '$lib/utils/context-compaction';

const renderUsage = (usage: any, mode = 'v2', currentMessageId = 'reply', language = 'en-US') => {
	const source = readFileSync(new URL('./ContextUsageStatus.svelte', import.meta.url), 'utf8');
	const code = transpileModule(compile(source, { generate: 'server' }).js.code, {
		compilerOptions: { module: ModuleKind.CommonJS }
	}).outputText;
	const dependencies: Record<string, any> = {
		svelte,
		'svelte/internal/server': server,
		'$lib/utils/context-compaction': compaction
	};
	const component = { exports: {} as any };
	new Function('require', 'module', 'exports', code)(
		(name: string) => dependencies[name],
		component,
		component.exports
	);
	const translations = JSON.parse(
		readFileSync(
			new URL(`../../../i18n/locales/${language}/translation.json`, import.meta.url),
			'utf8'
		)
	);
	return render(component.exports.default, {
		props: { usage, mode, currentMessageId },
		context: new Map([
			[
				'i18n',
				writable({
					language,
					t: (key: string, args: Record<string, any> = {}) =>
						(translations[key] || key).replace(/{{(\w+)}}/g, (_: string, name: string) =>
							String(args[name])
						)
				})
			]
		])
	}).body;
};
const usage = {
	tokens: 80000,
	threshold: 90000,
	percent: 89,
	context_window: 131072,
	input_budget: 120000,
	remaining_tokens: 10000,
	input_remaining_tokens: 40000,
	tool_budget_enabled: true,
	tool_result_limit: 13107,
	tool_batch_limit: 39321
};
const batch = {
	batch_id: 'old-batch',
	source_message_id: 'earlier',
	recorded_at: 1728000000000000000,
	complete: true,
	limits_at_decision: {
		context_window: 100000,
		input_budget: 80000,
		result_limit: 10000,
		batch_limit: 30000
	},
	original_tokens: 19000,
	processed_tokens: 10000,
	results: [
		{
			tool: 'synthetic_tool',
			original_tokens: 19000,
			processed_tokens: 10000,
			allocated_tokens: 10000,
			reduction_reasons: ['single_limit', 'batch_limit', 'input_space', 'final_payload_fit']
		}
	]
};

describe('context budget status rendering', () => {
	it('labels the trigger fraction and input budget separately', () => {
		const html = renderUsage(usage);
		expect(html).toContain('Auto-compaction trigger');
		expect(html).toContain('89%');
		expect(html).toContain('80k/90k');
		expect(html).toContain('Context window: 131k');
		expect(html).toContain('Input budget: 120k');
		expect(html).toContain('Until trigger: 10k');
		expect(html).toContain('Available input: 40k');
	});
	it('omits unknown input capacity and preserves off and legacy labels', () => {
		const html = renderUsage({ tokens: 3000, estimated_tokens: 3000 }, 'off');
		expect(html).toContain('3k tokens');
		expect(html).not.toContain('V2 compaction');
		expect(html).not.toContain('Available input');
		expect(html).not.toContain('Auto-compaction trigger');
		expect(renderUsage({ tokens: 3000, threshold: 6000, percent: 50 }, 'legacy')).toContain('50%');
		expect(renderUsage(null)).toContain('unknown');
		expect(renderUsage(usage, 'off')).not.toContain('Auto-compaction trigger');
	});
	it('keeps a previous decision and its then-effective caps expandable', () => {
		const html = renderUsage({ ...usage, last_tool_batch: batch });
		expect(html).toContain('<details');
		expect(html).toContain('Previous reply');
		expect(html).toContain('19k → 10k tokens');
		expect(html).toContain('Complete batch');
		expect(html).toContain('2024-10-04T00:00:00.000Z');
		expect(html).toContain('Limits at decision: 10k per result · 30k per batch');
		expect(html).toContain('Allocated: 10k tokens');
		for (const reason of [
			'Per-result limit',
			'Batch limit',
			'Available input space',
			'Final payload fit'
		])
			expect(html).toContain(reason);
	});
	it('keeps legacy source, timestamp, caps and completeness unknown', () => {
		const html = renderUsage({
			...usage,
			last_tool_batch: { original_tokens: 19000, processed_tokens: 10000, results: [] }
		});
		expect(html).toContain('Reply unknown');
		expect(html).toContain('Decision time unknown');
		expect(html).toContain('Batch completeness unknown');
		expect(html).toContain('Limits at decision unknown');
		expect(html).not.toContain('Current reply');
	});
	it('shows only an accounting warning when persistence fails', () => {
		const html = renderUsage({ ...usage, accounting_error: true });
		expect(html).toContain('Tool batch statistics are unavailable or incomplete.');
		expect(html).toContain('role="status"');
	});
	it('renders translated labels and incomplete historical batches', () => {
		const html = renderUsage(
			{ ...usage, last_tool_batch: { ...batch, historical: true, complete: false } },
			'v2',
			'reply',
			'zh-CN'
		);
		expect(html).toContain('历史记录');
		expect(html).toContain('批次统计不完整');
		expect(html.match(/<summary[^>]*>[\s\S]*?<\/summary>/)?.[0]).toContain('批次统计不完整');
		expect(html).toContain('可用输入');
	});
});
