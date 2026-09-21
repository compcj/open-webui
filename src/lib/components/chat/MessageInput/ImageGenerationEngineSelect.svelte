<script lang="ts">
	import { getContext } from 'svelte';
	import type { ImageGenerationEngineOption } from '$lib/apis/images';

	const i18n: any = getContext('i18n');
	export let engines: ImageGenerationEngineOption[] | null = null;
	export let value = '';
	export let loading = false;
	export let onChange: (id: string) => void = () => {};
</script>

<select
	aria-label={$i18n.t('Image Engine Configuration')}
	title={$i18n.t('Image Engine Configuration')}
	class="h-7 min-w-0 max-w-36 truncate rounded-full border border-sky-200/40 bg-sky-50 pl-2 pr-6 text-xs text-sky-600 outline-hidden focus:ring-2 focus:ring-sky-400 disabled:opacity-60 dark:border-sky-500/20 dark:bg-sky-400/10 dark:text-sky-300"
	{value}
	disabled={loading || engines === null}
	on:change={(event) => onChange(event.currentTarget.value)}
>
	{#if engines === null}
		<option {value}>
			{loading ? $i18n.t('Loading...') : $i18n.t('Unavailable')}
		</option>
	{:else}
		<option value="">{$i18n.t('Default')}</option>
		{#each engines as engine (engine.id)}
			<option value={engine.id}>{engine.name}</option>
		{/each}
	{/if}
</select>
