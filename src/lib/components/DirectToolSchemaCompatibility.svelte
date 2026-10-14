<script lang="ts">
	import { getContext } from 'svelte';
	import type { ToolSchemaCompatibility } from '$lib/utils/model-tool-schema';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Minus from '$lib/components/icons/Minus.svelte';

	const i18n: any = getContext('i18n');
	export let value: Record<string, ToolSchemaCompatibility> = {};
	export let modelIds: string[] = [];
	let modelId = '';

	const addModel = () => {
		const id = modelId.trim();
		if (!id) return;
		value = { ...value, [id]: value[id] === 'gemini' ? 'gemini' : 'default' };
		modelId = '';
	};
</script>

<div class="mt-3">
	<div class="text-xs text-gray-500">{$i18n.t('Tool schema compatibility')}</div>
	<p class="my-1 text-xs text-gray-500">
		{$i18n.t(
			'Configure each model using its ID without the connection prefix. Unconfigured models use Default (no conversion).'
		)}
	</p>
	{#each Object.entries(value) as [id, mode], index (id)}
		<div class="my-2">
			<label for={`direct-tool-schema-${index}`} class="block break-all text-xs mb-1">{id}</label>
			<div class="flex items-center gap-2">
				<select
					id={`direct-tool-schema-${index}`}
					class="min-w-0 flex-1 rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-850"
					value={mode}
					on:change={(event) => {
						value = { ...value, [id]: event.currentTarget.value as ToolSchemaCompatibility };
					}}
				>
					<option value="default">{$i18n.t('Default (no conversion)')}</option>
					<option value="gemini">Gemini</option>
				</select>
				<button
					type="button"
					aria-label={$i18n.t('Remove {{MODELID}} from list.', { MODELID: id })}
					on:click={() => {
						value = Object.fromEntries(Object.entries(value).filter(([key]) => key !== id));
					}}
				>
					<Minus className="size-3.5" strokeWidth="2" />
				</button>
			</div>
		</div>
	{/each}
	<div class="flex items-center gap-2 mt-2">
		<label class="sr-only" for="direct-tool-schema-model-id">{$i18n.t('Model ID')}</label>
		<input
			id="direct-tool-schema-model-id"
			class="min-w-0 flex-1 bg-transparent text-sm outline-hidden"
			placeholder={$i18n.t('Model ID')}
			bind:value={modelId}
			list="direct-tool-schema-model-ids"
			on:keydown={(event) => {
				if (event.key === 'Enter') {
					event.preventDefault();
					addModel();
				}
			}}
		/>
		<datalist id="direct-tool-schema-model-ids">
			{#each [...new Set(modelIds)].filter((id) => !Object.hasOwn(value, id)) as id}
				<option value={id}></option>
			{/each}
		</datalist>
		<button
			type="button"
			aria-label={$i18n.t('Add')}
			on:click={addModel}
			disabled={!modelId.trim()}
		>
			<Plus className="size-3.5" strokeWidth="2" />
		</button>
	</div>
</div>
