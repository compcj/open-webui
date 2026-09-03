<script lang="ts">
	import { getContext } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		STANDARD_REASONING_EFFORTS,
		normalizeAvailableReasoningEffort,
		toggleAvailableReasoningEffort
	} from '$lib/utils/reasoning-effort';

	const i18n = getContext('i18n');

	export let values: string[] = [];

	let customValue = '';

	$: customValues = values.filter(
		(value) => !(STANDARD_REASONING_EFFORTS as readonly string[]).includes(value)
	);

	const addCustom = () => {
		const next = normalizeAvailableReasoningEffort([...values, customValue]);
		if (next.length === values.length && customValue.trim() === '') {
			return;
		}
		values = next;
		customValue = '';
	};
</script>

<div class="py-0.5 w-full">
	<Tooltip
		content={$i18n.t(
			'Optional values users can pick in chat. Leave empty to hide the dropdown. Independent of the default Reasoning Effort.'
		)}
		placement="top-start"
		className="inline-tooltip"
	>
		<div class="self-center text-xs text-gray-600 dark:text-gray-400">
			{$i18n.t('Available Reasoning Effort')}
		</div>
	</Tooltip>

	<div class="mt-1.5 flex flex-wrap gap-1.5">
		{#each STANDARD_REASONING_EFFORTS as effort}
			<button
				type="button"
				class="rounded-full px-2.5 py-0.5 text-xs transition {values.includes(effort)
					? 'bg-gray-800 text-white dark:bg-gray-200 dark:text-gray-900'
					: 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-white/[0.04] dark:text-gray-400 dark:hover:bg-white/[0.08]'}"
				aria-pressed={values.includes(effort)}
				on:click={() => {
					values = toggleAvailableReasoningEffort(values, effort);
				}}
			>
				{effort}
			</button>
		{/each}

		{#each customValues as effort}
			<button
				type="button"
				class="flex items-center gap-1 rounded-full bg-gray-800 px-2.5 py-0.5 text-xs text-white dark:bg-gray-200 dark:text-gray-900"
				on:click={() => {
					values = toggleAvailableReasoningEffort(values, effort);
				}}
			>
				<span>{effort}</span>
				<XMark className="size-3" />
			</button>
		{/each}
	</div>

	<div class="mt-1.5 flex items-center gap-1.5">
		<input
			class="min-w-0 flex-1 bg-transparent text-sm outline-hidden outline-none"
			type="text"
			aria-label={$i18n.t('Add custom value')}
			placeholder={$i18n.t('Add custom value')}
			bind:value={customValue}
			autocomplete="off"
			on:keydown={(e) => {
				if (e.key === 'Enter') {
					e.preventDefault();
					addCustom();
				}
			}}
		/>
		<button
			class="flex size-6 shrink-0 items-center justify-center rounded-md text-gray-500 transition hover:text-gray-800 dark:hover:text-gray-200"
			type="button"
			aria-label={$i18n.t('Add')}
			on:click={addCustom}
		>
			<Plus className="size-3.5" />
		</button>
	</div>
</div>
