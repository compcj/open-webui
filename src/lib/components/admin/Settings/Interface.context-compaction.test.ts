import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { isValidToolBudgetPercent } from '$lib/utils/context-compaction';

const source = readFileSync(new URL('./Interface.svelte', import.meta.url), 'utf8');
const names = new Set(['chatConfig', 'taskConfig', 'configuredParams', 'updateInterfaceHandler']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `let ${source.slice(node.start, node.end)};`)
	.join('\n');
const createForm = () => {
	const updateChatConfig = vi.fn(async (_token, config) => ({ ...config }));
	const body = `
        const { updateChatConfig, isValidToolBudgetPercent } = dependencies;
        const updateTaskConfig = async (_token, config) => config;
        const localStorage = { token: 'synthetic' }, $i18n = { t: text => text };
        const appConfig = { update: () => {} };
        ${declarations}
        return { config: chatConfig, save: updateInterfaceHandler };
    `;
	return {
		updateChatConfig,
		...new Function(
			'dependencies',
			transpileModule(body, {
				compilerOptions: { target: ScriptTarget.ES2022 }
			}).outputText
		)({ updateChatConfig, isValidToolBudgetPercent })
	};
};

describe('admin summary output percentage', () => {
	it('defaults to five percent and saves both percentage and absolute configuration', async () => {
		const form = createForm();
		expect(form.config.CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT).toBe(5);
		form.config.CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT = 8;
		form.config.CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS = 9000;
		await form.save();
		expect(form.updateChatConfig.mock.calls[0][1]).toMatchObject({
			CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT: 8,
			CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS: 9000
		});
	});
	it.each([0, 101, 1.5, null, true])(
		'rejects invalid percentage %s before saving',
		async (value) => {
			const form = createForm();
			form.config.CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT = value;
			await expect(form.save()).rejects.toBe(
				'Summary output percentage must be an integer between 1 and 100.'
			);
			expect(form.updateChatConfig).not.toHaveBeenCalled();
		}
	);
});
