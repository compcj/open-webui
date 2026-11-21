<script lang="ts">
	import { getContext } from 'svelte';

	import type { ImageProfile } from '$lib/apis/images';
	import AdminSettingField from './AdminSettingField.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';

	type Option = { value: string; label: string };
	export let engines: ImageProfile[] = [];
	export let generationModelOptions: Option[] = [];
	export let editModelOptions: Option[] = [];

	const i18n: any = getContext('i18n');
	const inputClass = 'w-full rounded-lg bg-gray-50 px-2 py-1 text-xs dark:bg-white/5';

	const addEngine = () => {
		engines = [
			...engines,
			{
				id: crypto.randomUUID(),
				name: '',
				generation_model: generationModelOptions[0]?.value ?? '',
				edit_model: null,
				tool_description_suffix: '',
				edit_tool_description_suffix: ''
			}
		];
	};
</script>

<div class="flex flex-col gap-3">
	<div class="flex items-start justify-between gap-4">
		<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
			{$i18n.t(
				'Each configuration pairs separate generation and editing providers under one name.'
			)}
		</div>
		<button
			class="shrink-0 rounded-lg bg-gray-100 px-2.5 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200 dark:bg-white/5 dark:text-gray-300 dark:hover:bg-white/10"
			type="button"
			on:click={addEngine}
		>
			{$i18n.t('Add Configuration')}
		</button>
	</div>

	{#if engines.length === 0}
		<div
			class="rounded-xl border border-dashed border-gray-200 px-3 py-4 text-center text-xs text-gray-400 dark:border-white/10 dark:text-gray-600"
		>
			{$i18n.t('No additional image engine configurations configured.')}
		</div>
	{/if}

	{#each engines as engine, engineIndex (engine.id)}
		<div class="rounded-xl border border-gray-100 p-3 dark:border-white/[0.06]">
			<div class="mb-3 flex items-center justify-between gap-3">
				<div class="min-w-0 truncate text-xs font-medium text-gray-700 dark:text-gray-300">
					{engine.name || $i18n.t('Configuration {{number}}', { number: engineIndex + 1 })}
				</div>
				<button
					class="shrink-0 text-xs text-gray-400 transition-colors hover:text-red-600 dark:text-gray-600 dark:hover:text-red-400"
					type="button"
					aria-label={$i18n.t('Remove Configuration')}
					on:click={() => (engines = engines.filter((item) => item.id !== engine.id))}
				>
					{$i18n.t('Remove')}
				</button>
			</div>

			<div class="flex flex-col gap-3">
				<AdminSettingField label={$i18n.t('Alias')} forId={`engine-${engine.id}-name`}>
					<input
						id={`engine-${engine.id}-name`}
						class={inputClass}
						placeholder={$i18n.t('Enter a unique name')}
						bind:value={engine.name}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('Generation settings')}
					forId={`engine-${engine.id}-generation`}
				>
					<SettingsSelect
						id={`engine-${engine.id}-generation`}
						bind:value={engine.generation_model}
					>
						{#if engine.generation_model && !generationModelOptions.some((item) => item.value === engine.generation_model)}
							<option value={engine.generation_model}>{engine.generation_model}</option>
						{/if}
						{#each generationModelOptions as option (option.value)}
							<option value={option.value}>{option.label}</option>
						{/each}
					</SettingsSelect>
				</AdminSettingField>
				<AdminSettingField
					label={$i18n.t('Tool Description Suffix')}
					forId={`engine-${engine.id}-generation-description`}
				>
					<textarea
						id={`engine-${engine.id}-generation-description`}
						class={inputClass}
						bind:value={engine.tool_description_suffix}
					></textarea>
				</AdminSettingField>
				<AdminSettingField label={$i18n.t('Editing settings')} forId={`engine-${engine.id}-edit`}>
					<select id={`engine-${engine.id}-edit`} class={inputClass} bind:value={engine.edit_model}>
						<option value={null}>{$i18n.t('Use system default editor')}</option>
						<option value="disabled">{$i18n.t('Off')}</option>
						{#if engine.edit_model && engine.edit_model !== 'disabled' && !editModelOptions.some((item) => item.value === engine.edit_model)}
							<option value={engine.edit_model}>{engine.edit_model}</option>
						{/if}
						{#each editModelOptions as option (option.value)}
							<option value={option.value}>{option.label}</option>
						{/each}
					</select>
				</AdminSettingField>
				{#if engine.edit_model && engine.edit_model !== 'disabled'}
					<AdminSettingField
						label={$i18n.t('Tool Description Suffix')}
						forId={`engine-${engine.id}-edit-description`}
					>
						<textarea
							id={`engine-${engine.id}-edit-description`}
							class={inputClass}
							bind:value={engine.edit_tool_description_suffix}
						></textarea>
					</AdminSettingField>
				{/if}
			</div>
		</div>
	{/each}
</div>
