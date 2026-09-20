<script lang="ts">
	import { getContext } from 'svelte';
	import type { i18n as I18n } from 'i18next';
	import type { Writable } from 'svelte/store';
	import type { ContextToolFeature, ContextToolState } from '$lib/utils/tool-feature-preferences';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import BookOpen from '$lib/components/icons/BookOpen.svelte';
	import LightBulb from '$lib/components/icons/LightBulb.svelte';
	import Note from '$lib/components/icons/Note.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext<Writable<I18n>>('i18n');

	export let state: ContextToolState = { knowledge: false, memory: false, notes: false };
	export let available: ContextToolState = { knowledge: false, memory: false, notes: false };
	export let memoryDisabled = false;
	export let compact = false;
	export let onToggle: (feature: ContextToolFeature, enabled: boolean) => void = () => {};

	$: items = [
		{
			id: 'knowledge' as const,
			label: $i18n.t('Knowledge Base'),
			description: $i18n.t('Browse and query knowledge bases'),
			icon: BookOpen
		},
		{
			id: 'memory' as const,
			label: $i18n.t('Memory'),
			description: $i18n.t('Search and manage user memories'),
			icon: LightBulb
		},
		{
			id: 'notes' as const,
			label: $i18n.t('Notes'),
			description: $i18n.t('Search, view, and manage user notes'),
			icon: Note
		}
	];
</script>

{#each items as item (item.id)}
	{#if available[item.id]}
		{@const disabled = item.id === 'memory' && memoryDisabled}
		{@const enabled = state[item.id] && !disabled}
		{#if compact}
			{#if enabled}
				<Tooltip content={item.label} placement="top">
					<button
						type="button"
						aria-label={$i18n.t('Disable {{feature}}', { feature: item.label })}
						aria-pressed={enabled}
						on:click|preventDefault={() => onToggle(item.id, false)}
						class="group p-[0.375rem] flex gap-1.5 items-center text-sm rounded-full transition-colors duration-300 focus:outline-hidden max-w-full overflow-hidden text-sky-500 dark:text-sky-300 bg-sky-50 hover:bg-sky-100 dark:bg-sky-400/10 dark:hover:bg-sky-700/10 border border-sky-200/40 dark:border-sky-500/20"
					>
						<svelte:component this={item.icon} className="size-4" strokeWidth="1.75" />
						<div class="hidden group-hover:block">
							<XMark className="size-4" strokeWidth="1.75" />
						</div>
					</button>
				</Tooltip>
			{/if}
		{:else}
			<Tooltip
				content={disabled
					? $i18n.t('Enable Memory in Settings > Personalization first.')
					: item.description}
				placement="top-start"
			>
				<button
					type="button"
					{disabled}
					aria-pressed={enabled}
					on:click={() => onToggle(item.id, !enabled)}
					class="flex w-full justify-between gap-2 items-center h-[1.6875rem] px-2 text-[0.8125rem] font-normal cursor-pointer rounded-xl hover:bg-gray-50/40 dark:hover:bg-gray-800/40 disabled:cursor-not-allowed disabled:opacity-50"
				>
					<div class="flex min-w-0 flex-1 gap-2 items-center">
						<div class="shrink-0">
							<svelte:component this={item.icon} className="size-3.5" strokeWidth="1.75" />
						</div>
						<div class="truncate">{item.label}</div>
					</div>
					<div class="shrink-0" inert><Switch state={enabled} /></div>
				</button>
			</Tooltip>
		{/if}
	{/if}
{/each}
