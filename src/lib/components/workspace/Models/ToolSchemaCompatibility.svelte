<script lang="ts">
	import { getContext } from 'svelte';
	import {
		resolveToolSchemaCompatibility,
		type ToolSchemaCompatibility,
		type ToolSchemaModel
	} from '$lib/utils/model-tool-schema';

	const i18n: any = getContext('i18n');
	export let value: ToolSchemaCompatibility | null = null;
	export let baseModelId: string | null = null;
	export let models: ToolSchemaModel[] = [];

	$: inherited = resolveToolSchemaCompatibility(baseModelId, models);
</script>

<div class="my-3">
	<label
		for="model-tool-schema-compatibility"
		class="block text-xs text-gray-600 dark:text-gray-400"
	>
		{$i18n.t('Tool schema compatibility')}
	</label>
	<select
		id="model-tool-schema-compatibility"
		class="mt-1 w-full rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-850"
		value={value ?? (baseModelId ? '' : 'default')}
		on:change={(event) => {
			value = (event.currentTarget.value || null) as ToolSchemaCompatibility | null;
		}}
	>
		{#if baseModelId}
			<option value="">
				{$i18n.t('Inherit from base model ({{mode}})', {
					mode: inherited.value === 'gemini' ? 'Gemini' : $i18n.t('Default (no conversion)')
				})}
			</option>
		{/if}
		<option value="default">{$i18n.t('Default (no conversion)')}</option>
		<option value="gemini">Gemini</option>
	</select>
	<p class="mt-1 text-xs text-gray-500">
		{$i18n.t(
			'Select Gemini for models that require Gemini-compatible tool schemas. Model names do not enable this setting.'
		)}
	</p>
</div>
