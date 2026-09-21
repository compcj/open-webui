<script lang="ts">
	import { getContext } from 'svelte';

	import type { ImageEngineConfig, ImageGenerationEngine } from '$lib/apis/images';
	import ImageEngineProviderFields from './ImageEngineProviderFields.svelte';
	import { createImageEngineConfig, prepareImageEngineProfiles } from './image-engine-editor';
	import AdminSettingField from './AdminSettingField.svelte';

	export let engines: ImageGenerationEngine[] = [];

	const i18n: any = getContext('i18n');
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	let paramsDrafts: Record<string, { generation: string; edit: string }> = {};
	let editStash: Record<string, ImageEngineConfig> = {};

	$: {
		for (const engine of engines) {
			if (!(engine.id in paramsDrafts)) {
				paramsDrafts[engine.id] = {
					generation: JSON.stringify(engine.params ?? {}, null, 2),
					edit: JSON.stringify(engine.edit?.params ?? {}, null, 2)
				};
			}
		}
	}

	const addEngine = () => {
		const id = crypto.randomUUID();
		paramsDrafts[id] = { generation: '{}', edit: '{}' };
		engines = [
			...engines,
			{ id, name: '', ...createImageEngineConfig(), edit: createImageEngineConfig() }
		];
	};

	const removeEngine = (id: string) => {
		engines = engines.filter((engine) => engine.id !== id);
		delete paramsDrafts[id];
		delete editStash[id];
	};

	const toggleEdit = (engine: ImageGenerationEngine) => {
		if (engine.edit) {
			editStash[engine.id] = engine.edit;
			engine.edit = null;
		} else {
			engine.edit = editStash[engine.id] ?? createImageEngineConfig();
		}
		engines = engines;
	};

	export function getValidatedEngines(): ImageGenerationEngine[] {
		return prepareImageEngineProfiles(engines, paramsDrafts);
	}
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
					on:click={() => removeEngine(engine.id)}
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

				<fieldset
					class="flex flex-col gap-2.5 border-t border-gray-100 pt-3 dark:border-white/[0.06]"
				>
					<legend class="px-1 text-xs font-medium text-gray-700 dark:text-gray-300">
						{$i18n.t('Generation settings')}
					</legend>
					<ImageEngineProviderFields
						config={engine}
						idPrefix={`engine-${engine.id}-generation`}
						bind:paramsDraft={paramsDrafts[engine.id].generation}
					/>
				</fieldset>

				<fieldset
					class="flex flex-col gap-2.5 border-t border-gray-100 pt-3 dark:border-white/[0.06]"
				>
					<legend class="px-1 text-xs font-medium text-gray-700 dark:text-gray-300">
						{$i18n.t('Editing settings')}
					</legend>
					{#if engine.edit}
						<button
							class="w-fit text-xs text-gray-500 transition-colors hover:text-gray-900 hover:underline dark:text-gray-500 dark:hover:text-white"
							type="button"
							on:click={() => toggleEdit(engine)}
						>
							{$i18n.t('Use system default editor')}
						</button>
						<ImageEngineProviderFields
							config={engine.edit}
							idPrefix={`engine-${engine.id}-editing`}
							editing
							bind:paramsDraft={paramsDrafts[engine.id].edit}
						/>
					{:else}
						<p class="text-xs text-gray-400 dark:text-gray-600">
							{$i18n.t('This configuration uses the system default image editor.')}
						</p>
						<button
							class="w-fit text-xs text-gray-500 transition-colors hover:text-gray-900 hover:underline dark:text-gray-500 dark:hover:text-white"
							type="button"
							on:click={() => toggleEdit(engine)}
						>
							{$i18n.t('Configure separate editor')}
						</button>
					{/if}
				</fieldset>
			</div>
		</div>
	{/each}
</div>
