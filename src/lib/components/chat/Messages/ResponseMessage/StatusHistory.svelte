<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import StatusItem from './StatusHistory/StatusItem.svelte';
	import ToolResultViewer from './ToolResultViewer.svelte';
	import equal from 'fast-deep-equal';
	/** @type {any[]} */
	export let statusHistory = [];
	export let expand = false;
	export let chatId = '';
	export let branchId = '';
	/** @type {string | null} */
	let selectedResultId = null;
	let showRawResult = false;
	$: toolResults = Array.from(
		statusHistory
			.reduce((results, entry) => {
				if (entry?.action === 'tool_result_compaction' && entry?.result_id) {
					results.set(entry.result_id, { ...results.get(entry.result_id), ...entry });
				}
				return results;
			}, new Map())
			.values()
	);

	let showHistory = true;

	$: if (expand) {
		showHistory = true;
	} else {
		showHistory = false;
	}

	let history = [];
	let status = null;

	$: if (history && history.length > 0) {
		status = history.at(-1);
	}

	$: if (!equal(statusHistory, history)) {
		history = statusHistory;
	}
</script>

{#if history && history.length > 0}
	{#if status?.hidden !== true}
		<div class="text-[0.9375rem] flex flex-col w-full">
			<button
				class="w-full"
				aria-label={$i18n.t('Toggle status history')}
				aria-expanded={showHistory}
				on:click={() => {
					showHistory = !showHistory;
				}}
			>
				<div class="flex items-start gap-2">
					<StatusItem {status} />
				</div>
			</button>

			{#if showHistory}
				<div class="flex flex-row">
					{#if history.length > 1}
						<div class="w-full">
							{#each history as status, idx}
								<div class="flex items-stretch gap-2 mb-1">
									<div class=" ">
										<div class="pt-3 px-1 mb-1.5">
											<span class="relative flex size-1.5 rounded-full justify-center items-center">
												<span
													class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
												></span>
											</span>
										</div>
										{#if idx !== history.length - 1}
											<div
												class="w-[0.03125rem] ml-[0.40625rem] h-[calc(100%-14px)] bg-gray-300 dark:bg-gray-700"
											/>
										{/if}
									</div>

									<StatusItem {status} done={true} />
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
{/if}

{#if chatId && branchId && toolResults.length}
	<div class="flex flex-wrap gap-2 py-1">
		{#each toolResults as entry (entry.result_id)}
			<button
				type="button"
				class="text-xs text-gray-500 underline hover:text-gray-700 dark:hover:text-gray-300"
				on:click={() => {
					selectedResultId = entry.result_id;
					showRawResult = true;
				}}
			>
				{$i18n.t('View original tool result')}{#if entry.tool}
					· {entry.tool}{/if}
			</button>
		{/each}
	</div>
{/if}
{#if showRawResult && selectedResultId}
	{#key `${chatId}:${branchId}:${selectedResultId}`}
		<ToolResultViewer bind:show={showRawResult} {chatId} {branchId} resultId={selectedResultId} />
	{/key}
{/if}
