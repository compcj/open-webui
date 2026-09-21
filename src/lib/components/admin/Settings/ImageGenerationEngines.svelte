<script lang="ts">
	import { getContext } from 'svelte';

	import type { ImageGenerationEngine } from '$lib/apis/images';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';

	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';

	export let engines: ImageGenerationEngine[] = [];

	const i18n: any = getContext('i18n');

	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 font-mono text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	let paramsDrafts: Record<string, string> = {};

	$: {
		for (const engine of engines) {
			if (!(engine.id in paramsDrafts)) {
				paramsDrafts[engine.id] = JSON.stringify(engine.params ?? {}, null, 2);
			}
		}
	}

	const createEngine = (): ImageGenerationEngine => ({
		id: crypto.randomUUID(),
		name: '',
		engine: 'openai',
		model: '',
		base_url: '',
		api_key: '',
		api_version: '',
		params: {},
		size: '',
		steps: null,
		gemini_endpoint_method: 'generateContent',
		comfyui_workflow: '',
		comfyui_workflow_nodes: []
	});

	const addEngine = () => {
		const engine = createEngine();
		paramsDrafts[engine.id] = '{}';
		engines = [...engines, engine];
	};

	const removeEngine = (id: string) => {
		engines = engines.filter((engine) => engine.id !== id);
		delete paramsDrafts[id];
	};

	const addWorkflowNode = (engine: ImageGenerationEngine) => {
		engine.comfyui_workflow_nodes = [
			...engine.comfyui_workflow_nodes,
			{ type: 'prompt', key: 'text', node_ids: [] }
		];
		engines = engines;
	};

	const removeWorkflowNode = (engine: ImageGenerationEngine, index: number) => {
		engine.comfyui_workflow_nodes = engine.comfyui_workflow_nodes.filter((_, i) => i !== index);
		engines = engines;
	};

	const paramsDescription = (engine: ImageGenerationEngine['engine']) => {
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

		return $i18n.t('Merge optional JSON values into each image generation request.');
	};

	const paramsPlaceholder = (engine: ImageGenerationEngine['engine']) => {
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

	const modelPlaceholder = (engine: ImageGenerationEngine['engine']) => {
		if (engine === 'gemini') {
			return $i18n.t('Example: gemini-2.5-flash-image');
		}

		if (engine === 'grok') {
			return $i18n.t('Example: grok-imagine-image');
		}

		if (engine === 'comfyui') {
			return $i18n.t('Enter a ComfyUI model name');
		}

		return $i18n.t('Example: gpt-image-1');
	};

	const baseURLPlaceholder = (engine: ImageGenerationEngine['engine']) => {
		if (engine === 'gemini') {
			return $i18n.t('Example: https://generativelanguage.googleapis.com/v1beta');
		}

		if (engine === 'grok') {
			return $i18n.t('Example: https://api.x.ai/v1');
		}

		if (engine === 'comfyui') {
			return $i18n.t('Example: http://127.0.0.1:8188');
		}

		return $i18n.t('Example: https://api.openai.com/v1');
	};

	const parseJSONObject = (value: string, errorMessage: string) => {
		try {
			const parsed = JSON.parse(value.trim() || '{}');

			if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
				return parsed as Record<string, unknown>;
			}
		} catch (error) {
			// The localized validation message below is more useful than the parser error.
		}

		throw new Error(errorMessage);
	};

	export function getValidatedEngines(): ImageGenerationEngine[] {
		const names = new Set<string>();
		const ids = new Set<string>();

		return engines.map((engine) => {
			const name = engine.name.trim();
			const normalizedName = name.toLocaleLowerCase();

			if (!name) {
				throw new Error($i18n.t('Engine alias is required.'));
			}

			if (names.has(normalizedName)) {
				throw new Error($i18n.t('Engine aliases must be unique.'));
			}
			names.add(normalizedName);

			if (!engine.id || engine.id === 'default' || ids.has(engine.id)) {
				throw new Error($i18n.t('Additional engine IDs must be unique and cannot be default.'));
			}
			ids.add(engine.id);

			const params = parseJSONObject(
				paramsDrafts[engine.id] ?? '{}',
				$i18n.t('Additional parameters must be a valid JSON object.')
			);

			if (engine.engine === 'comfyui' && engine.comfyui_workflow.trim()) {
				parseJSONObject(
					engine.comfyui_workflow,
					$i18n.t('ComfyUI workflow must be a valid JSON object.')
				);
			}

			return {
				...engine,
				name,
				params,
				steps: engine.steps === null || engine.steps === undefined ? null : Number(engine.steps),
				comfyui_workflow_nodes: engine.comfyui_workflow_nodes.map((node) => ({
					...node,
					type: node.type.trim(),
					key: node.key.trim(),
					node_ids: node.node_ids.map((id) => id.trim()).filter(Boolean)
				}))
			};
		});
	}
</script>

<div class="flex flex-col gap-3">
	<div class="flex items-start justify-between gap-4">
		<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
			{$i18n.t('Add named provider profiles that users can select when generating an image.')}
		</div>

		<button
			class="shrink-0 rounded-lg bg-gray-100 px-2.5 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200 dark:bg-white/5 dark:text-gray-300 dark:hover:bg-white/10"
			type="button"
			on:click={addEngine}
		>
			{$i18n.t('Add Engine')}
		</button>
	</div>

	{#if engines.length === 0}
		<div
			class="rounded-xl border border-dashed border-gray-200 px-3 py-4 text-center text-xs text-gray-400 dark:border-white/10 dark:text-gray-600"
		>
			{$i18n.t('No additional image generation engines configured.')}
		</div>
	{/if}

	{#each engines as engine, engineIndex (engine.id)}
		<div class="rounded-xl border border-gray-100 p-3 dark:border-white/[0.06]">
			<div class="mb-3 flex items-center justify-between gap-3">
				<div class="min-w-0 truncate text-xs font-medium text-gray-700 dark:text-gray-300">
					{engine.name || $i18n.t('Engine {{number}}', { number: engineIndex + 1 })}
				</div>

				<button
					class="shrink-0 text-xs text-gray-400 transition-colors hover:text-red-600 dark:text-gray-600 dark:hover:text-red-400"
					type="button"
					aria-label={$i18n.t('Remove Engine')}
					on:click={() => removeEngine(engine.id)}
				>
					{$i18n.t('Remove')}
				</button>
			</div>

			<div class="flex flex-col gap-2.5">
				<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
					<AdminSettingField label={$i18n.t('Alias')} forId={`engine-${engine.id}-name`}>
						<input
							id={`engine-${engine.id}-name`}
							class={inputClass}
							placeholder={$i18n.t('Enter a unique name')}
							bind:value={engine.name}
						/>
					</AdminSettingField>

					<AdminSettingField label={$i18n.t('Provider')} forId={`engine-${engine.id}-provider`}>
						<SettingsSelect
							id={`engine-${engine.id}-provider`}
							className="w-full"
							selectClassName="w-full"
							bind:value={engine.engine}
							placeholder={$i18n.t('Select Provider')}
						>
							<option value="openai">{$i18n.t('OpenAI')}</option>
							<option value="gemini">{$i18n.t('Gemini')}</option>
							<option value="grok">{$i18n.t('Grok Imagine')}</option>
							<option value="comfyui">{$i18n.t('ComfyUI')}</option>
						</SettingsSelect>
					</AdminSettingField>
				</div>

				<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
					<AdminSettingField label={$i18n.t('Model')} forId={`engine-${engine.id}-model`}>
						<input
							id={`engine-${engine.id}-model`}
							class={inputClass}
							placeholder={modelPlaceholder(engine.engine)}
							bind:value={engine.model}
						/>
					</AdminSettingField>

					<AdminSettingField label={$i18n.t('API Base URL')} forId={`engine-${engine.id}-base-url`}>
						<input
							id={`engine-${engine.id}-base-url`}
							class={inputClass}
							placeholder={baseURLPlaceholder(engine.engine)}
							bind:value={engine.base_url}
						/>
					</AdminSettingField>
				</div>

				<AdminSettingField label={$i18n.t('API Key')} forId={`engine-${engine.id}-api-key`}>
					<SensitiveInput
						id={`engine-${engine.id}-api-key`}
						variant="settings"
						placeholder={$i18n.t('API Key')}
						bind:value={engine.api_key}
						required={false}
					/>
				</AdminSettingField>

				{#if engine.engine === 'openai'}
					<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
						<AdminSettingField
							label={$i18n.t('API Version')}
							forId={`engine-${engine.id}-api-version`}
						>
							<input
								id={`engine-${engine.id}-api-version`}
								class={inputClass}
								placeholder={$i18n.t('API Version')}
								bind:value={engine.api_version}
							/>
						</AdminSettingField>

						<AdminSettingField label={$i18n.t('Image Size')} forId={`engine-${engine.id}-size`}>
							<input
								id={`engine-${engine.id}-size`}
								class={inputClass}
								placeholder={$i18n.t('Enter Image Size (e.g. 1024x1024)')}
								bind:value={engine.size}
							/>
						</AdminSettingField>
					</div>
				{:else if engine.engine === 'gemini'}
					<AdminSettingRow
						label={$i18n.t('Gemini Endpoint Method')}
						description={$i18n.t('Select the Gemini endpoint method to call.')}
					>
						<SettingsSelect
							bind:value={engine.gemini_endpoint_method}
							ariaLabel={$i18n.t('Gemini Endpoint Method')}
							placeholder={$i18n.t('Select Method')}
						>
							<option value="predict">predict</option>
							<option value="generateContent">generateContent</option>
						</SettingsSelect>
					</AdminSettingRow>
				{:else if engine.engine === 'comfyui'}
					<div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
						<AdminSettingField label={$i18n.t('Image Size')} forId={`engine-${engine.id}-size`}>
							<input
								id={`engine-${engine.id}-size`}
								class={inputClass}
								placeholder={$i18n.t('Enter Image Size (e.g. 1024x1024)')}
								bind:value={engine.size}
							/>
						</AdminSettingField>

						<AdminSettingField label={$i18n.t('Steps')} forId={`engine-${engine.id}-steps`}>
							<input
								id={`engine-${engine.id}-steps`}
								class={inputClass}
								type="number"
								min="1"
								placeholder={$i18n.t('Enter Number of Steps (e.g. 50)')}
								bind:value={engine.steps}
							/>
						</AdminSettingField>
					</div>
				{/if}

				<AdminSettingField
					label={$i18n.t('Additional Parameters')}
					description={paramsDescription(engine.engine)}
				>
					<Textarea
						className={textareaClass}
						bind:value={paramsDrafts[engine.id]}
						placeholder={paramsPlaceholder(engine.engine)}
						ariaLabel={$i18n.t('Additional Parameters')}
						minSize={96}
					/>
				</AdminSettingField>

				{#if engine.engine === 'comfyui'}
					<AdminSettingField
						label={$i18n.t('ComfyUI API Workflow')}
						description={$i18n.t('Paste workflow JSON exported in API format from ComfyUI.')}
					>
						<Textarea
							className={textareaClass}
							bind:value={engine.comfyui_workflow}
							placeholder={$i18n.t('Workflow JSON')}
							ariaLabel={$i18n.t('ComfyUI API Workflow')}
							minSize={140}
						/>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('ComfyUI Workflow Nodes')}
						description={$i18n.t('Map workflow node inputs used for image generation.')}
					>
						<div class="flex flex-col gap-2">
							{#if engine.comfyui_workflow_nodes.length === 0}
								<div class="text-xs text-gray-400 dark:text-gray-600">
									{$i18n.t('No node mappings configured.')}
								</div>
							{/if}

							{#each engine.comfyui_workflow_nodes as node, nodeIndex}
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
											engines = engines;
										}}
									/>
									<button
										class="h-7 px-2 text-xs text-gray-400 transition-colors hover:text-red-600 dark:text-gray-600 dark:hover:text-red-400"
										type="button"
										aria-label={$i18n.t('Remove Node Mapping')}
										on:click={() => removeWorkflowNode(engine, nodeIndex)}
									>
										{$i18n.t('Remove')}
									</button>
								</div>
							{/each}

							<button
								class="w-fit text-xs text-gray-500 transition-colors hover:text-gray-900 hover:underline dark:text-gray-500 dark:hover:text-white"
								type="button"
								on:click={() => addWorkflowNode(engine)}
							>
								{$i18n.t('Add Node Mapping')}
							</button>
						</div>
					</AdminSettingField>
				{/if}
			</div>
		</div>
	{/each}
</div>
