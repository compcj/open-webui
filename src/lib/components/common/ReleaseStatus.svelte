<script lang="ts">
	import { getContext } from 'svelte';
	import { getReleaseChannels, type ReleaseVersions } from '$lib/utils/release-updates';

	const i18n = getContext<any>('i18n');
	export let version: ReleaseVersions;
	export let loading = false;
	export let updatesOnly = false;
</script>

{#each getReleaseChannels(version).filter((channel) => !updatesOnly || channel.available) as channel}
	<span class="inline-flex flex-wrap items-center gap-1">
		{channel.fork ? $i18n.t('Fork release') : $i18n.t('Upstream release')}
		{#if loading}
			{$i18n.t('Checking for updates...')}
		{:else if channel.version}
			<a class="underline" href={channel.url} target="_blank" rel="noreferrer">
				v{channel.version}
				{channel.available ? $i18n.t('available!') : $i18n.t('(latest)')}
			</a>
		{:else}
			{$i18n.t('Could not check for updates')}
		{/if}
	</span>
{/each}
