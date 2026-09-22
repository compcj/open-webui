<script lang="ts">
	import { getContext } from 'svelte';

	import type { ImageEditEngineConfig } from '$lib/apis/images';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';

	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';

	export let config: ImageEditEngineConfig;
	export let paramsDraft = '{}';
	export let idPrefix: string;
	export let editing = false;

	const i18n: any = getContext('i18n');
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 font-mono text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const paramsDescription = (engine: ImageEditEngineConfig['engine']) => {
		if (engine === 'gemini') {
			return $i18n.t(
				'Merge optional values into the Gemini request, including generationConfig and imageConfig.'
			);
		}
		if (engine === 'grok') {
			return $i18n.t(
				'Configure Grok Imagine options such as aspect_ratio and resolution in this JSON object.'
			);
		}
		return editing
			? $i18n.t('Merge optional JSON values into each image editing request.')
			: $i18n.t('Merge optional JSON values into each image generation request.');
	};

	const paramsPlaceholder = (engine: ImageEditEngineConfig['engine']) => {
		if (engine === 'gemini') {
			return $i18n.t('Example: {"generationConfig":{"imageConfig":{"aspectRatio":"16:9"}}}');
		}
		if (engine === 'grok') {
			return $i18n.t('Example: {"aspect_ratio":"16:9","resolution":"2k"}');
		}
		if (engine === 'openai') {
			return $i18n.t('Example: {"quality":"high"}');
		}
		return '{}';
	};

	const modelPlaceholder = (engine: ImageEditEngineConfig['engine']) => {
		if (engine === 'gemini') return $i18n.t('Example: gemini-2.5-flash-image');
		if (engine === 'grok') return $i18n.t('Example: grok-imagine-image');
		if (engine === 'comfyui') return $i18n.t('Enter a ComfyUI model name');
		return $i18n.t('Example: gpt-image-1');
	};

	const baseURLPlaceholder = (engine: ImageEditEngineConfig['engine']) => {
		if (engine === 'gemini') {
			return $i18n.t('Example: https://generativelanguage.googleapis.com/v1beta');
		}
		if (engine === 'grok') return $i18n.t('Example: https://api.x.ai/v1');
		if (engine === 'comfyui') return $i18n.t('Example: http://127.0.0.1:8188');
		return $i18n.t('Example: https://api.openai.com/v1');
	};

	const addWorkflowNode = () => {
		config.comfyui_workflow_nodes = [
			...config.comfyui_workflow_nodes,
			{ type: editing ? 'image' : 'prompt', key: editing ? 'image' : 'text', node_ids: [] }
		];
		config = config;
	};

	const removeWorkflowNode = (index: number) => {
		config.comfyui_workflow_nodes = config.comfyui_workflow_nodes.filter((_, i) => i !== index);
		config = config;
	};
</script>

<div class="flex flex-col gap-2.5">
	<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
		<AdminSettingField label={$i18n.t('Provider')} forId={`${idPrefix}-provider`}>
			<SettingsSelect
				id={`${idPrefix}-provider`}
				className="w-full"
				selectClassName="w-full"
				bind:value={config.engine}
				placeholder={$i18n.t('Select Provider')}
			>
				{#if editing}
					<option value="disabled">{$i18n.t('Off')}</option>
				{/if}
				<option value="openai">{$i18n.t('OpenAI')}</option>
				<option value="gemini">{$i18n.t('Gemini')}</option>
				<option value="grok">{$i18n.t('Grok Imagine')}</option>
				<option value="comfyui">{$i18n.t('ComfyUI')}</option>
			</SettingsSelect>
		</AdminSettingField>

		{#if config.engine !== 'disabled'}
			<AdminSettingField label={$i18n.t('Model')} forId={`${idPrefix}-model`}>
				<input
					id={`${idPrefix}-model`}
					class={inputClass}
					placeholder={modelPlaceholder(config.engine)}
					bind:value={config.model}
				/>
			</AdminSettingField>
		{/if}
	</div>

	{#if config.engine !== 'disabled'}
		<AdminSettingField
			label={$i18n.t('Tool Description Suffix')}
			description={editing
				? $i18n.t(
						'Append to the image editing tool description to guide the model when writing image prompts. Leave empty to use the built-in description.'
					)
				: $i18n.t(
						'Append to the image generation tool description to guide the model when writing image prompts. Leave empty to use the built-in description.'
					)}
		>
			<Textarea
				className={textareaClass}
				bind:value={config.tool_description_suffix}
				ariaLabel={$i18n.t('Tool Description Suffix')}
				minSize={96}
			/>
		</AdminSettingField>

		<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
			<AdminSettingField label={$i18n.t('API Base URL')} forId={`${idPrefix}-base-url`}>
				<input
					id={`${idPrefix}-base-url`}
					class={inputClass}
					placeholder={baseURLPlaceholder(config.engine)}
					bind:value={config.base_url}
				/>
			</AdminSettingField>

			<AdminSettingField label={$i18n.t('API Key')} forId={`${idPrefix}-api-key`}>
				<SensitiveInput
					id={`${idPrefix}-api-key`}
					variant="settings"
					placeholder={$i18n.t('API Key')}
					bind:value={config.api_key}
					required={false}
				/>
			</AdminSettingField>
		</div>

		{#if config.engine === 'openai'}
			<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
				<AdminSettingField label={$i18n.t('API Version')} forId={`${idPrefix}-api-version`}>
					<input
						id={`${idPrefix}-api-version`}
						class={inputClass}
						placeholder={$i18n.t('API Version')}
						bind:value={config.api_version}
					/>
				</AdminSettingField>
				<AdminSettingField label={$i18n.t('Image Size')} forId={`${idPrefix}-size`}>
					<input
						id={`${idPrefix}-size`}
						class={inputClass}
						placeholder={$i18n.t('Enter Image Size (e.g. 1024x1024)')}
						bind:value={config.size}
					/>
				</AdminSettingField>
			</div>
		{:else if config.engine === 'gemini' && !editing}
			<AdminSettingRow
				label={$i18n.t('Gemini Endpoint Method')}
				description={$i18n.t('Select the Gemini endpoint method to call.')}
			>
				<SettingsSelect
					bind:value={config.gemini_endpoint_method}
					ariaLabel={$i18n.t('Gemini Endpoint Method')}
					placeholder={$i18n.t('Select Method')}
				>
					<option value="predict">predict</option>
					<option value="generateContent">generateContent</option>
				</SettingsSelect>
			</AdminSettingRow>
		{:else if config.engine === 'comfyui'}
			<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
				<AdminSettingField label={$i18n.t('Image Size')} forId={`${idPrefix}-size`}>
					<input
						id={`${idPrefix}-size`}
						class={inputClass}
						placeholder={$i18n.t('Enter Image Size (e.g. 1024x1024)')}
						bind:value={config.size}
					/>
				</AdminSettingField>
				<AdminSettingField label={$i18n.t('Steps')} forId={`${idPrefix}-steps`}>
					<input
						id={`${idPrefix}-steps`}
						class={inputClass}
						type="number"
						min="1"
						placeholder={$i18n.t('Enter Number of Steps (e.g. 50)')}
						bind:value={config.steps}
					/>
				</AdminSettingField>
			</div>
		{/if}

		<AdminSettingField
			label={$i18n.t('Additional Parameters')}
			description={paramsDescription(config.engine)}
		>
			<Textarea
				className={textareaClass}
				bind:value={paramsDraft}
				placeholder={paramsPlaceholder(config.engine)}
				ariaLabel={$i18n.t('Additional Parameters')}
				minSize={96}
			/>
		</AdminSettingField>

		{#if config.engine === 'comfyui'}
			<AdminSettingField
				label={$i18n.t('ComfyUI API Workflow')}
				description={$i18n.t('Paste workflow JSON exported in API format from ComfyUI.')}
			>
				<Textarea
					className={textareaClass}
					bind:value={config.comfyui_workflow}
					placeholder={$i18n.t('Workflow JSON')}
					ariaLabel={$i18n.t('ComfyUI API Workflow')}
					minSize={140}
				/>
			</AdminSettingField>

			<AdminSettingField
				label={$i18n.t('ComfyUI Workflow Nodes')}
				description={editing
					? $i18n.t('Map workflow node inputs used for image editing.')
					: $i18n.t('Map workflow node inputs used for image generation.')}
			>
				<div class="flex flex-col gap-2">
					{#if config.comfyui_workflow_nodes.length === 0}
						<div class="text-xs text-gray-400 dark:text-gray-600">
							{$i18n.t('No node mappings configured.')}
						</div>
					{/if}
					{#each config.comfyui_workflow_nodes as node, nodeIndex}
						<div class="grid grid-cols-1 gap-1.5 sm:grid-cols-[1fr_1fr_1.5fr_auto]">
							<input
								class={inputClass}
								placeholder={$i18n.t('Type')}
								aria-label={$i18n.t('Type')}
								bind:value={node.type}
							/>
							<input
								class={inputClass}
								placeholder={$i18n.t('Input Key')}
								aria-label={$i18n.t('Input Key')}
								bind:value={node.key}
							/>
							<input
								class={inputClass}
								placeholder={$i18n.t('Comma-separated node IDs')}
								aria-label={$i18n.t('Comma-separated node IDs')}
								value={node.node_ids.join(', ')}
								on:input={(event) => {
									node.node_ids = (event.currentTarget as HTMLInputElement).value
										.split(',')
										.map((id) => id.trim());
									config = config;
								}}
							/>
							<button
								class="h-7 px-2 text-xs text-gray-400 transition-colors hover:text-red-600 dark:text-gray-600 dark:hover:text-red-400"
								type="button"
								aria-label={$i18n.t('Remove Node Mapping')}
								on:click={() => removeWorkflowNode(nodeIndex)}
							>
								{$i18n.t('Remove')}
							</button>
						</div>
					{/each}
					<button
						class="inline-flex w-fit items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100 focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-blue-500 dark:border-blue-400/30 dark:bg-blue-400/10 dark:text-blue-300 dark:hover:bg-blue-400/20"
						type="button"
						on:click={addWorkflowNode}
					>
						<Plus className="size-3.5" />
						{$i18n.t('Add Node Mapping')}
					</button>
				</div>
			</AdminSettingField>
		{/if}
	{/if}
</div>
