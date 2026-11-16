<script lang="ts">
	import { getContext } from 'svelte';

	const i18n = getContext<typeof import('$lib/i18n').default>('i18n');

	export let score: unknown = undefined;
	export let id: string | undefined = undefined;

	$: validScore =
		typeof score === 'number' && Number.isInteger(score) && score >= 0 && score <= 99
			? score
			: null;
</script>

{#if validScore !== null}
	<span
		{id}
		title={$i18n.t('Intelligence score: {{score}}/99', { score: validScore })}
		class="inline-flex min-w-7 shrink-0 items-center justify-center rounded-md border border-violet-200 bg-linear-to-br from-violet-50 to-indigo-100 px-1.5 py-0.5 text-[0.6875rem] font-semibold leading-none text-violet-700 tabular-nums dark:border-violet-400/30 dark:from-violet-500/20 dark:to-indigo-500/20 dark:text-violet-200"
	>
		<span class="sr-only">{$i18n.t('Intelligence score: {{score}}/99', { score: validScore })}</span
		>
		<span aria-hidden="true">{validScore}</span>
	</span>
{/if}
