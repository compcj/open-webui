<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');
	import WebSearchResults from '../WebSearchResults.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import { t } from 'i18next';

	/** @type {any} Socket status payloads vary by action. */
	export let status = null;
	export let done = false;
</script>

{#if !status?.hidden}
	<div class="status-description flex items-center gap-2 py-0.5 w-full text-left">
		{#if status?.action === 'context_compaction'}
			<div
				role="status"
				class="{!done && !status?.done
					? 'shimmer'
					: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] text-wrap"
			>
				{#if status?.error}
					{$i18n.t('Context compaction failed. Original history is preserved.')}
				{:else if status?.done}
					{$i18n.t('Context compacted')}
				{:else}
					{$i18n.t('Compacting context')}
				{/if}
			</div>
		{:else if status?.action === 'tool_result_compaction'}
			<div role="status" class="text-gray-500 dark:text-gray-400 text-sm text-wrap">
				{#if status?.cancelled || status?.description === 'Tool result preparation cancelled'}
					{$i18n.t('Tool result preparation cancelled')}
				{:else if status?.error}
					{#if status?.description === 'Tool result preparation failed; retry using saved tool outputs'}
						{$i18n.t('Tool result preparation failed; retry using saved tool outputs')}
					{:else}
						{$i18n.t('Tool result processing failed.')}
						{#if status?.description}
							<span class="block">{status.description}</span>
						{/if}
					{/if}
				{:else if !status?.done}
					{$i18n.t('Processing tool result')}
				{:else if status?.method === 'excerpt'}
					{$i18n.t('Tool result excerpt (incomplete)')}
				{:else if status?.method === 'summary'}
					{$i18n.t('Tool result summarized')}
				{:else if status?.method === 'read'}
					{$i18n.t('Exact tool result passage')}
				{:else}
					{$i18n.t('Original tool result retained')}
				{/if}
				{#if status?.tool}<span class="break-all"> · {status.tool}</span>{/if}
				{#if typeof status?.original_tokens === 'number' && typeof status?.processed_tokens === 'number'}
					<span title={status?.estimate_source}>
						· {$i18n.t('{{original}} → {{processed}} tokens', {
							original: status.original_tokens,
							processed: status.processed_tokens
						})}
					</span>
				{/if}
			</div>
		{:else if status?.action === 'web_search' && (status?.urls || status?.items)}
			<WebSearchResults {status}>
				<div class="flex flex-col justify-center -space-y-0.5">
					<div
						class="{(done || status?.done) === false
							? 'shimmer'
							: ''} text-[0.9375rem] line-clamp-1 text-wrap"
					>
						<!-- $i18n.t("Generating search query") -->
						<!-- $i18n.t("No search query generated") -->
						<!-- $i18n.t('Searched {{count}} sites') -->
						{#if status?.description?.includes('{{count}}')}
							{$i18n.t(status?.description, {
								count: (status?.urls || status?.items).length
							})}
						{:else if status?.description === 'No search query generated'}
							{$i18n.t('No search query generated')}
						{:else if status?.description === 'Generating search query'}
							{$i18n.t('Generating search query')}
						{:else}
							{status?.description}
						{/if}
					</div>
				</div>
			</WebSearchResults>
		{:else if status?.action === 'knowledge_search'}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching Knowledge for "{{searchQuery}}"`, {
						searchQuery: status.query
					})}
				</div>
			</div>
		{:else if status?.action === 'web_search_queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] line-clamp-1 text-wrap"
				>
					{$i18n.t(`Searching`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'queries_generated' && status?.queries}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] line-clamp-1 text-wrap"
				>
					{$i18n.t(`Querying`)}
				</div>

				<div class=" flex gap-1 flex-wrap mt-2">
					{#each status.queries as query, idx (query)}
						<div
							class="bg-gray-50 dark:bg-gray-850 flex rounded-lg py-1 px-2 items-center gap-1 text-xs"
						>
							<div>
								<Search className="size-3" />
							</div>

							<span class="line-clamp-1">
								{query}
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else if status?.action === 'sources_retrieved' && status?.count !== undefined}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] line-clamp-1 text-wrap"
				>
					{#if status.count === 0}
						{$i18n.t('No sources found')}
					{:else if status.count === 1}
						{$i18n.t('Retrieved 1 source')}
					{:else}
						<!-- {$i18n.t('Source')} -->
						<!-- {$i18n.t('No source available')} -->
						<!-- {$i18n.t('No distance available')} -->
						<!-- {$i18n.t('Retrieved {{count}} sources')} -->
						{$i18n.t('Retrieved {{count}} sources', {
							count: status.count
						})}
					{/if}
				</div>
			</div>
		{:else}
			<div class="flex flex-col justify-center -space-y-0.5">
				<div
					class="{(done || status?.done) === false
						? 'shimmer'
						: ''} text-gray-500 dark:text-gray-500 text-[0.9375rem] line-clamp-1 text-wrap"
				>
					<!-- $i18n.t(`Searching "{{searchQuery}}"`) -->
					{#if status?.description?.includes('{{searchQuery}}')}
						{$i18n.t(status?.description, {
							searchQuery: status?.query
						})}
					{:else if status?.description === 'No search query generated'}
						{$i18n.t('No search query generated')}
					{:else if status?.description === 'Generating search query'}
						{$i18n.t('Generating search query')}
					{:else if status?.description === 'Searching the web'}
						{$i18n.t('Searching the web')}
					{:else}
						{status?.description}
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/if}
