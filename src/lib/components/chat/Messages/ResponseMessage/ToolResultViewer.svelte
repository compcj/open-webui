<script lang="ts">
	import { getContext, onDestroy, onMount } from 'svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { getChatToolResult, type ToolResultPage } from '$lib/apis/chats';

	const i18n: any = getContext('i18n');
	export let show = false;
	export let chatId: string;
	export let branchId: string;
	export let resultId: string;
	let page: ToolResultPage | null = null;
	let query = '';
	let activeQuery = '';
	let loading = false;
	let error = '';
	let requestVersion = 0;

	const loadPage = async (cursor?: string) => {
		const version = ++requestVersion;
		loading = true;
		error = '';
		try {
			const nextPage = await getChatToolResult(localStorage.token, chatId, resultId, branchId, {
				query: activeQuery,
				cursor
			});
			if (version === requestVersion) page = nextPage;
		} catch (err) {
			if (version === requestVersion) error = String(err);
		} finally {
			if (version === requestVersion) loading = false;
		}
	};
	onMount(() => {
		void loadPage();
	});
	onDestroy(() => {
		requestVersion += 1;
	});
</script>

<Modal bind:show size="lg">
	<div class="p-3 space-y-3">
		<div class="flex items-center justify-between gap-2">
			<h2 class="font-medium">{$i18n.t('Original tool result')}</h2>
			<button
				type="button"
				class="rounded-lg px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800"
				on:click={() => {
					show = false;
				}}>{$i18n.t('Close')}</button
			>
		</div>
		<p class="text-xs text-gray-500">
			{$i18n.t(
				'Exact original text, shown in bounded pages. Summaries and excerpts may omit details.'
			)}
		</p>
		<form
			class="flex gap-2"
			on:submit|preventDefault={() => {
				activeQuery = query;
				page = null;
				void loadPage();
			}}
		>
			<input
				class="min-w-0 flex-1 rounded-lg bg-gray-100 dark:bg-gray-800 px-3 py-2 text-sm"
				aria-label={$i18n.t('Find in original result')}
				placeholder={$i18n.t('Find in original result')}
				bind:value={query}
			/>
			<button
				type="submit"
				disabled={loading}
				class="rounded-lg px-3 py-2 text-sm disabled:opacity-50">{$i18n.t('Search')}</button
			>
		</form>
		{#if loading}<div role="status" class="flex gap-2 text-sm">
				<Spinner />{$i18n.t('Loading')}
			</div>{/if}
		{#if error}
			<div role="alert" class="text-sm text-red-600 dark:text-red-400">
				{$i18n.t('Unable to load original tool result')}: {error}
			</div>
			<button
				type="button"
				class="text-sm underline"
				disabled={loading}
				on:click={() => {
					void loadPage();
				}}>{$i18n.t('Retry')}</button
			>
		{/if}
		{#if page && !error}
			<div class="text-xs text-gray-500 break-all">
				{page.source.name} · {$i18n.t('Characters {{start}}–{{end}} of {{total}}', {
					start: page.start,
					end: page.end,
					total: page.total_chars
				})}
			</div>
			<pre
				class="max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-gray-50 dark:bg-gray-950 p-3 text-xs">{page.text}</pre>
			<div class="flex justify-between gap-2 text-sm">
				<button
					type="button"
					disabled={loading || page.start === 0}
					on:click={() => {
						void loadPage();
					}}
					class="disabled:opacity-50">{$i18n.t('First page')}</button
				>
				{#if page.incomplete}<span class="text-xs text-gray-500"
						>{$i18n.t('More original text is available')}</span
					>{/if}
				<button
					type="button"
					disabled={loading || !page.next_cursor}
					on:click={() => {
						if (page?.next_cursor) void loadPage(page.next_cursor);
					}}
					class="disabled:opacity-50">{$i18n.t('Next page')}</button
				>
			</div>
		{/if}
	</div>
</Modal>
