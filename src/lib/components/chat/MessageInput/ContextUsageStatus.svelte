<script lang="ts">
	import { getContext } from 'svelte';
	import {
		getContextBudgetStatus,
		getToolBatchOrigin,
		getToolBatchRecordedDate,
		type CompactionMode,
		type ContextUsage
	} from '$lib/utils/context-compaction';

	const i18n: any = getContext('i18n');
	export let usage: ContextUsage | null = null;
	export let mode: CompactionMode = 'off';
	export let currentMessageId: string | undefined = undefined;

	const formatTokens = (value: number) => {
		const trim = (value: number) =>
			value >= 10 ? String(Math.round(value)) : value.toFixed(1).replace(/\.0$/, '');
		if (value >= 1_000_000) return `${trim(value / 1_000_000)}m`;
		if (value >= 1_000) return `${trim(value / 1_000)}k`;
		return String(value);
	};

	$: budget = getContextBudgetStatus(usage);
	$: batch = mode === 'v2' ? usage?.last_tool_batch : null;
	$: origin = batch ? getToolBatchOrigin(batch, currentMessageId) : 'unknown';
	$: recordedDate = getToolBatchRecordedDate(batch?.recorded_at);
	$: barPercent = Math.min(100, Math.max(0, budget.percent ?? 0));
</script>

<div class="rounded-xl py-0.5 text-gray-600 dark:text-gray-400">
	<div class="flex min-h-4 flex-wrap items-center justify-between gap-x-3 gap-y-1">
		<span
			>{$i18n.t('Context usage')}{#if mode === 'v2'}
				· {$i18n.t('V2 compaction')}{/if}</span
		>
		<span class="font-mono text-[0.625rem] text-gray-500 dark:text-gray-400">
			{#if budget.tokens === null}
				{$i18n.t('unknown')}
			{:else if mode !== 'off' && budget.threshold !== null}
				{$i18n.t('Auto-compaction trigger')}: {budget.percent === null
					? $i18n.t('unknown')
					: `${Math.round(budget.percent)}%`}
				{formatTokens(budget.tokens)}/{formatTokens(budget.threshold)}
			{:else}
				{formatTokens(budget.tokens)} {$i18n.t('tokens')}
			{/if}
		</span>
	</div>
	{#if usage?.source}
		<div class="mt-1 text-[0.625rem] text-gray-500 dark:text-gray-400" title={usage.source}>
			{usage.source.includes('tiktoken')
				? $i18n.t('tiktoken estimate')
				: usage.source === 'estimated:stored-history'
					? $i18n.t('Saved history estimate')
					: usage.source === 'provider:single-call'
						? $i18n.t('Provider input usage')
						: $i18n.t('Estimated')}
		</div>
	{/if}
	{#if mode === 'v2' && usage}
		<div
			class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[0.625rem] text-gray-500 dark:text-gray-400"
		>
			{#if typeof usage.context_window === 'number'}<span
					>{$i18n.t('Context window: {{tokens}}', {
						tokens: formatTokens(usage.context_window)
					})}</span
				>{/if}
			{#if typeof usage.input_budget === 'number'}<span
					>{$i18n.t('Input budget: {{tokens}}', { tokens: formatTokens(usage.input_budget) })}</span
				>{/if}
			{#if budget.triggerRemaining !== null}<span
					>{$i18n.t('Until trigger: {{tokens}}', {
						tokens: formatTokens(budget.triggerRemaining)
					})}</span
				>{/if}
			{#if budget.inputRemaining !== null}<span
					>{$i18n.t('Available input: {{tokens}}', {
						tokens: formatTokens(budget.inputRemaining)
					})}</span
				>{/if}
		</div>
		{#if usage.tool_budget_enabled && typeof usage.tool_result_limit === 'number' && typeof usage.tool_batch_limit === 'number'}
			<div class="mt-1 text-[0.625rem] text-gray-500 dark:text-gray-400">
				{$i18n.t('Current tool limits: {{single}} per result · {{batch}} per batch', {
					single: formatTokens(usage.tool_result_limit),
					batch: formatTokens(usage.tool_batch_limit)
				})}
			</div>
		{/if}
		{#if usage.accounting_error}
			<div role="status" class="mt-1 text-[0.625rem] text-amber-700 dark:text-amber-400">
				{$i18n.t('Tool batch statistics are unavailable or incomplete.')}
			</div>
		{/if}
	{/if}
	{#if batch}
		<details class="mt-1 text-[0.625rem] text-gray-500 dark:text-gray-400">
			<summary class="cursor-pointer break-words">
				{$i18n.t('Last tool batch: {{original}} → {{processed}} tokens', {
					original: formatTokens(batch.original_tokens),
					processed: formatTokens(batch.processed_tokens)
				})}
				· {#if origin === 'current'}{$i18n.t(
						'Current reply'
					)}{:else if origin === 'previous'}{$i18n.t(
						'Previous reply'
					)}{:else if origin === 'historical'}{$i18n.t('Historical record')}{:else}{$i18n.t(
						'Reply unknown'
					)}{/if}
				{#if batch.complete === false}
					· {$i18n.t('Incomplete batch statistics')}{/if}
			</summary>
			<div class="mt-1 space-y-1 break-words pl-2">
				<div>
					{#if batch.complete === true}{$i18n.t(
							'Complete batch'
						)}{:else if batch.complete === false}{$i18n.t(
							'Incomplete batch statistics'
						)}{:else}{$i18n.t('Batch completeness unknown')}{/if}
					· {#if recordedDate}<time datetime={recordedDate.toISOString()}
							>{$i18n.t('Decision time: {{time}}', {
								time: recordedDate.toLocaleString($i18n.language)
							})}</time
						>{:else}{$i18n.t('Decision time unknown')}{/if}
				</div>
				{#if batch.limits_at_decision}
					<div>
						{$i18n.t('Limits at decision: {{single}} per result · {{batch}} per batch', {
							single: formatTokens(batch.limits_at_decision.result_limit),
							batch: formatTokens(batch.limits_at_decision.batch_limit)
						})}
					</div>
					<div>
						{$i18n.t('Context window: {{tokens}}', {
							tokens: formatTokens(batch.limits_at_decision.context_window)
						})} · {$i18n.t('Input budget: {{tokens}}', {
							tokens: formatTokens(batch.limits_at_decision.input_budget)
						})}
					</div>
				{:else}
					<div>{$i18n.t('Limits at decision unknown')}</div>
				{/if}
				{#each batch.results ?? [] as result}
					<div class="border-l border-gray-200 pl-2 dark:border-gray-700">
						<span class="break-all">{result.tool}</span>
						{#if typeof result.original_tokens === 'number' && typeof result.processed_tokens === 'number'}
							· {$i18n.t('{{original}} → {{processed}} tokens', {
								original: formatTokens(result.original_tokens),
								processed: formatTokens(result.processed_tokens)
							})}{/if}
						{#if typeof result.allocated_tokens === 'number'}
							· {$i18n.t('Allocated: {{tokens}} tokens', {
								tokens: formatTokens(result.allocated_tokens)
							})}{/if}
						{#if result.reduction_reasons?.length}
							<div>
								{$i18n.t('Reduction reasons')}:
								{#each result.reduction_reasons as reason, index}
									{#if index > 0}
										·
									{/if}
									{#if reason === 'single_limit'}{$i18n.t(
											'Per-result limit'
										)}{:else if reason === 'batch_limit'}{$i18n.t(
											'Batch limit'
										)}{:else if reason === 'input_space'}{$i18n.t(
											'Available input space'
										)}{:else if reason === 'final_payload_fit'}{$i18n.t(
											'Final payload fit'
										)}{:else}{$i18n.t('Other reduction')}{/if}
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		</details>
	{/if}
	{#if mode !== 'off' && budget.threshold !== null}
		<div
			class="mt-1.5 h-0.5 overflow-hidden rounded-full bg-gray-100 dark:bg-white/8"
			title={$i18n.t('Auto-compaction trigger')}
		>
			<div
				class="h-full rounded-full bg-gray-300 dark:bg-white/20"
				style={`width: ${barPercent}%`}
			></div>
		</div>
	{/if}
</div>
