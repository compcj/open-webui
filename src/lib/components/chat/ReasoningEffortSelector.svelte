<script lang="ts">
	import { getContext } from 'svelte';
	import Select from '$lib/components/common/Select.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { formatDefaultReasoningEffortLabel } from '$lib/utils/reasoning-effort';

	const i18n = getContext('i18n');

	export let available: string[] = [];
	export let value = '';
	export let label = '';
	export let defaultEffort = '';
	export let onChange: (value: string) => void = () => {};

	$: defaultLabel = formatDefaultReasoningEffortLabel($i18n.t('Default'), defaultEffort);
	$: items = [
		{ value: '', label: defaultLabel },
		...available.map((effort) => ({ value: effort, label: effort }))
	];

	$: selectedLabel = items.find((item) => item.value === value)?.label ?? defaultLabel;
</script>

<Tooltip
	className="flex shrink-0"
	content={label ? `${label}: ${$i18n.t('Reasoning Effort')}` : $i18n.t('Reasoning Effort')}
>
	<div class="flex min-w-0 items-center">
		<Select
			{value}
			{items}
			align="end"
			side="top"
			contentClass="min-w-[7.5rem]"
			triggerClass="reasoning-effort-trigger flex min-w-0 max-w-[9rem] items-center gap-1 rounded-lg pl-2 pr-1.5 py-1 text-[0.8125rem] font-normal text-gray-600 transition-colors duration-100 hover:bg-gray-50/40 hover:text-gray-700 dark:text-gray-300 dark:hover:bg-gray-800/40 dark:hover:text-gray-200"
			onChange={(next) => onChange(next)}
		>
			<div slot="trigger" class="flex min-w-0 items-center gap-1">
				{#if label}
					<span class="max-w-[2.75rem] truncate text-[0.6875rem] text-gray-400 dark:text-gray-500">
						{label}
					</span>
				{/if}
				<span class="min-w-0 truncate">{selectedLabel}</span>
				<ChevronDown className="size-3 shrink-0 text-gray-400 dark:text-gray-500" />
			</div>
		</Select>
	</div>
</Tooltip>
