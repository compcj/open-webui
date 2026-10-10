<script lang="ts">
	import { getModels, getTaskConfig, updateTaskConfig } from '$lib/apis';
	import { getChatConfig, updateChatConfig } from '$lib/apis/chats';
	import { createEventDispatcher, onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getBaseModels } from '$lib/apis/models';

	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SettingsSelect from '$lib/components/common/SettingsSelect.svelte';
	import ExperimentalBadge from '$lib/components/common/ExperimentalBadge.svelte';
	import AdvancedParams from '$lib/components/chat/Settings/Advanced/AdvancedParams.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingRow from './AdminSettingRow.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';
	import { config as appConfig } from '$lib/stores';
	import { isValidToolBudgetPercent } from '$lib/utils/context-compaction';

	const dispatch = createEventDispatcher();

	const i18n: any = getContext('i18n');

	let taskConfig = {
		TASK_MODEL: '',
		TASK_MODEL_EXTERNAL: '',
		TASK_MODEL_PARAMS: {},
		ENABLE_TITLE_GENERATION: true,
		TITLE_GENERATION_PROMPT_TEMPLATE: '',
		ENABLE_FOLLOW_UP_GENERATION: true,
		FOLLOW_UP_GENERATION_PROMPT_TEMPLATE: '',
		IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE: '',
		ENABLE_AUTOCOMPLETE_GENERATION: true,
		AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH: -1,
		AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE: '',
		TAGS_GENERATION_PROMPT_TEMPLATE: '',
		ENABLE_TAGS_GENERATION: true,
		ENABLE_SEARCH_QUERY_GENERATION: true,
		ENABLE_RETRIEVAL_QUERY_GENERATION: true,
		QUERY_GENERATION_PROMPT_TEMPLATE: '',
		TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE: '',
		ENABLE_VOICE_MODE_PROMPT: true,
		VOICE_MODE_PROMPT_TEMPLATE: ''
	};

	let chatConfig = {
		CONTEXT_COMPACTION_MODEL: '',
		ENABLE_CONTEXT_COMPACTION: false,
		CONTEXT_COMPACTION_MODE: 'off',
		CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW: 262144,
		CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT: 95,
		CONTEXT_COMPACTION_V2_TRIGGER_PERCENT: 90,
		CONTEXT_COMPACTION_V2_REMINDER_PERCENT: 10,
		CONTEXT_COMPACTION_V2_RECENT_TOKENS: 20000,
		CONTEXT_COMPACTION_V2_TOOL_BUDGET_ENABLED: true,
		CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT: 10,
		CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT: 30,
		CONTEXT_COMPACTION_V2_TOOL_SUMMARY_PROMPT: '',
		CONTEXT_COMPACTION_V2_MODEL: '',
		CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS: null as number | null,
		CONTEXT_COMPACTION_V2_SUMMARY_PROMPT: '',
		CONTEXT_COMPACTION_V2_REMINDER_ENABLED: true,
		CONTEXT_COMPACTION_V2_REMINDER_PROMPT: '',
		CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS: 60,
		CONTEXT_COMPACTION_V2_MAX_RETRIES: 1,
		CONTEXT_COMPACTION_TOKEN_THRESHOLD: 80000,
		CONTEXT_COMPACTION_TOKEN_CAP: 80000,
		CONTEXT_COMPACTION_RETENTION_PERCENTAGE: 40,
		CONTEXT_COMPACTION_PROMPT_TEMPLATE: '',
		ENABLE_TOOL_PERMISSIONS: false
	};
	let showTaskParameters = false;

	const configuredParams = (params: Record<string, any> = {}) =>
		Object.fromEntries(
			Object.entries(params).filter(
				([_, value]) => value !== null && value !== '' && value !== undefined
			)
		);

	const updateInterfaceHandler = async () => {
		if (
			!isValidToolBudgetPercent(
				chatConfig.CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT,
				chatConfig.CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT
			)
		) {
			throw $i18n.t('Tool budget percentages must be integers: 1 ≤ individual ≤ batch ≤ 100.');
		}
		chatConfig.ENABLE_CONTEXT_COMPACTION = chatConfig.CONTEXT_COMPACTION_MODE !== 'off';
		chatConfig.CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS =
			chatConfig.CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS || null;
		const taskConfigPayload = {
			...taskConfig,
			TASK_MODEL_PARAMS: configuredParams(taskConfig.TASK_MODEL_PARAMS)
		};

		[taskConfig, chatConfig] = await Promise.all([
			updateTaskConfig(localStorage.token, taskConfigPayload),
			updateChatConfig(localStorage.token, chatConfig)
		]);
		appConfig.update((current) =>
			current
				? {
						...current,
						features: {
							...current.features,
							enable_context_compaction: chatConfig.ENABLE_CONTEXT_COMPACTION,
							context_compaction_mode: chatConfig.CONTEXT_COMPACTION_MODE as
								| 'off'
								| 'legacy'
								| 'v2',
							context_compaction_default_context_window:
								chatConfig.CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW,
							enable_tool_permissions: chatConfig.ENABLE_TOOL_PERMISSIONS
						}
					}
				: current
		);
	};

	let workspaceModels: any[] = [];
	let baseModels: any[] = [];

	let models: any[] | null = null;
	$: modelOptions = models ?? [];
	const normalizeModelSelection = (modelId: string | null | undefined) => {
		if (!modelId) {
			return '';
		}

		const model = modelOptions.find((m: any) => m.id === modelId);
		if (!model) {
			return '';
		}

		if (
			model?.access_grants &&
			!model.access_grants.some(
				(g: any) => g.principal_type === 'user' && g.principal_id === '*' && g.permission === 'read'
			)
		) {
			toast.error($i18n.t('This model is not publicly available. Please select another model.'));
		}

		return model.id;
	};
	const inputClass =
		'w-full h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
	const textareaClass =
		'w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	const init = async () => {
		try {
			[taskConfig, chatConfig] = await Promise.all([
				getTaskConfig(localStorage.token),
				getChatConfig(localStorage.token)
			]);
			taskConfig.TASK_MODEL_PARAMS = taskConfig.TASK_MODEL_PARAMS ?? {};

			workspaceModels = await getBaseModels(localStorage.token);
			baseModels = await getModels(localStorage.token, null, false);

			models = baseModels.map((m: any) => {
				const workspaceModel = workspaceModels.find((wm: any) => wm.id === m.id);

				if (workspaceModel) {
					return {
						...m,
						...workspaceModel
					};
				} else {
					return {
						...m,
						id: m.id,
						name: m.name,

						is_active: true
					};
				}
			});

			console.debug('models', models);
		} catch (err) {
			const error = err as { detail?: string; message?: string };
			console.error('Failed to initialize Interface settings:', err);
			toast.error(error?.detail ?? error?.message ?? $i18n.t('Failed to load Interface settings'));
			models = [];
		}
	};

	onMount(async () => {
		await init();
	});
</script>

{#if models !== null && taskConfig && chatConfig}
	<form
		class="flex h-full flex-col justify-between text-sm"
		on:submit|preventDefault={async () => {
			try {
				await updateInterfaceHandler();
				dispatch('save');
			} catch (error) {
				toast.error(String(error));
			}
		}}
	>
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
			{$i18n.t('settings.admin.interface.title')}
		</h2>

		<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
			<AdminSettingSection title={$i18n.t('settings.admin.interface.sections.tasks.title')} first>
				<div>
					<div class="mb-2">
						<div class="text-xs text-gray-600 dark:text-gray-400">
							{$i18n.t('settings.admin.interface.taskModel.label')}
						</div>
						<div class="mt-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$i18n.t('settings.admin.interface.taskModel.description')}
						</div>
					</div>

					<div class="grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2">
						<AdminSettingField label={$i18n.t('settings.admin.interface.localTaskModel.label')}>
							<SettingsSelect
								bind:value={taskConfig.TASK_MODEL}
								className="w-full"
								placeholder={$i18n.t('Select a model')}
								on:change={() => {
									taskConfig.TASK_MODEL = normalizeModelSelection(taskConfig.TASK_MODEL);
								}}
							>
								<option value="" selected>{$i18n.t('Current Model')}</option>
								{#each modelOptions as model}
									<option value={model.id} class="bg-gray-100 dark:bg-gray-700">
										{model.name}
										{model?.connection_type === 'local' ? `(${$i18n.t('Local')})` : ''}
									</option>
								{/each}
							</SettingsSelect>
						</AdminSettingField>

						<AdminSettingField label={$i18n.t('settings.admin.interface.externalTaskModel.label')}>
							<SettingsSelect
								bind:value={taskConfig.TASK_MODEL_EXTERNAL}
								className="w-full"
								placeholder={$i18n.t('Select a model')}
								on:change={() => {
									taskConfig.TASK_MODEL_EXTERNAL = normalizeModelSelection(
										taskConfig.TASK_MODEL_EXTERNAL
									);
								}}
							>
								<option value="" selected>{$i18n.t('Current Model')}</option>
								{#each modelOptions as model}
									<option value={model.id} class="bg-gray-100 dark:bg-gray-700">
										{model.name}
										{model?.connection_type === 'local' ? `(${$i18n.t('Local')})` : ''}
									</option>
								{/each}
							</SettingsSelect>
						</AdminSettingField>
					</div>

					<div class="mt-2.5">
						<button
							class="flex w-full items-center justify-between gap-4 py-0.5 text-left"
							type="button"
							on:click={() => {
								showTaskParameters = !showTaskParameters;
							}}
						>
							<span class="text-xs text-gray-600 dark:text-gray-400">
								{$i18n.t('settings.admin.interface.taskModelParameters.label')}
							</span>
							<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
								{showTaskParameters ? $i18n.t('Close') : $i18n.t('Configure')}
							</span>
						</button>

						{#if showTaskParameters}
							<div class="max-h-[24rem] overflow-y-auto pb-2 pr-1 scrollbar-hover">
								<AdvancedParams
									admin={true}
									custom={true}
									bind:params={taskConfig.TASK_MODEL_PARAMS}
								/>
							</div>
						{/if}
					</div>
				</div>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('settings.admin.interface.sections.chat.title')}>
				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.toolPermissions.label')}
					description={$i18n.t('settings.admin.interface.toolPermissions.description')}
					let:labelId
				>
					<div slot="label" class="flex items-center gap-2">
						<span>{$i18n.t('settings.admin.interface.toolPermissions.label')}</span>
						<ExperimentalBadge />
					</div>
					<Switch bind:state={chatConfig.ENABLE_TOOL_PERMISSIONS} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.contextCompaction.label')}
					description={$i18n.t('settings.admin.interface.contextCompaction.description')}
					let:labelId
				>
					<SettingsSelect bind:value={chatConfig.CONTEXT_COMPACTION_MODE} aria-labelledby={labelId}>
						<option value="off">{$i18n.t('Compaction off')}</option>
						<option value="legacy">{$i18n.t('Legacy compaction')}</option>
						<option value="v2">{$i18n.t('V2 compaction')}</option>
					</SettingsSelect>
				</AdminSettingRow>

				<AdminSettingField
					label={$i18n.t('Default context window for compaction')}
					description={$i18n.t(
						'Used when neither the model nor its base model specifies a context window.'
					)}
				>
					<input
						type="number"
						min="1"
						step="1"
						required
						class={inputClass}
						aria-label={$i18n.t('Default context window for compaction')}
						bind:value={chatConfig.CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW}
					/>
				</AdminSettingField>

				{#if chatConfig.CONTEXT_COMPACTION_MODE === 'legacy'}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.contextCompactionModel.label')}
						description={$i18n.t('settings.admin.interface.contextCompactionModel.description')}
					>
						<SettingsSelect
							bind:value={chatConfig.CONTEXT_COMPACTION_MODEL}
							className="w-full"
							placeholder={$i18n.t('Select a model')}
							on:change={() => {
								chatConfig.CONTEXT_COMPACTION_MODEL = normalizeModelSelection(
									chatConfig.CONTEXT_COMPACTION_MODEL
								);
							}}
						>
							<option value="" selected>{$i18n.t('Current Model')}</option>
							{#each modelOptions as model}
								<option value={model.id} class="bg-gray-100 dark:bg-gray-700">
									{model.name}
									{model?.connection_type === 'local' ? `(${$i18n.t('Local')})` : ''}
								</option>
							{/each}
						</SettingsSelect>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('settings.admin.interface.tokenThreshold.label')}
						description={$i18n.t('settings.admin.interface.tokenThreshold.description')}
					>
						<input
							type="number"
							min="1"
							step="1"
							class={inputClass}
							bind:value={chatConfig.CONTEXT_COMPACTION_TOKEN_THRESHOLD}
						/>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('settings.admin.interface.tokenCap.label')}
						description={$i18n.t('settings.admin.interface.tokenCap.description')}
					>
						<input
							type="number"
							min="1"
							step="1"
							class={inputClass}
							bind:value={chatConfig.CONTEXT_COMPACTION_TOKEN_CAP}
						/>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('settings.admin.interface.retainedMessages.label')}
						description={$i18n.t('settings.admin.interface.retainedMessages.description')}
					>
						<input
							type="number"
							min="10"
							max="50"
							step="1"
							class={inputClass}
							bind:value={chatConfig.CONTEXT_COMPACTION_RETENTION_PERCENTAGE}
						/>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('settings.admin.interface.contextCompactionPrompt.label')}
						description={$i18n.t('settings.admin.interface.contextCompactionPrompt.description')}
					>
						<Textarea
							className={textareaClass}
							bind:value={chatConfig.CONTEXT_COMPACTION_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
						<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$i18n.t('settings.admin.interface.availableVariables.label')}:
							<code>{'{{PREVIOUS_SUMMARY}}'}</code>,
							<code>{'{{COMPACTED_MESSAGES}}'}</code>,
							<code>{'{{RECENT_MESSAGES}}'}</code>,
							<code>{'{{MESSAGES}}'}</code>,
							<code>{'{{CURRENT_DATE}}'}</code>
						</div>
					</AdminSettingField>
				{/if}
				{#if chatConfig.CONTEXT_COMPACTION_MODE === 'v2'}
					<p class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'V2 combines continuation summaries, complete recent turns and token budget reminders. Settings apply to the next request.'
						)}
					</p>
					<AdminSettingField label={$i18n.t('V2 summary model')}>
						<SettingsSelect
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_MODEL}
							className="w-full"
							ariaLabel={$i18n.t('V2 summary model')}
						>
							<option value="">{$i18n.t('Current Model')}</option>
							{#each modelOptions as model}
								<option value={model.id}>{model.name}</option>
							{/each}
						</SettingsSelect>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Effective context window (%)')}>
						<input
							type="number"
							min="1"
							max="100"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Effective context window (%)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT}
						/>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Automatic compaction threshold (%)')}>
						<input
							type="number"
							min="1"
							max="100"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Automatic compaction threshold (%)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_TRIGGER_PERCENT}
						/>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Remaining budget for reminder (%)')}>
						<input
							type="number"
							min="0"
							max="100"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Remaining budget for reminder (%)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_REMINDER_PERCENT}
						/>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Recent original context budget (tokens)')}>
						<input
							type="number"
							min="0"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Recent original context budget (tokens)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_RECENT_TOKENS}
						/>
					</AdminSettingField>
					<AdminSettingField
						label={$i18n.t('Summary output limit (tokens)')}
						description={$i18n.t('Leave empty to inherit the summary model output limit.')}
					>
						<input
							type="number"
							min="1"
							step="1"
							class={inputClass}
							aria-label={$i18n.t('Summary output limit (tokens)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS}
						/>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Summary timeout (seconds)')}>
						<input
							type="number"
							min="1"
							max="600"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Summary timeout (seconds)')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS}
						/>
					</AdminSettingField>
					<AdminSettingField label={$i18n.t('Summary failure retries')}>
						<input
							type="number"
							min="0"
							max="5"
							step="1"
							required
							class={inputClass}
							aria-label={$i18n.t('Summary failure retries')}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_MAX_RETRIES}
						/>
					</AdminSettingField>
					<AdminSettingRow label={$i18n.t('Context budget reminders')} let:labelId>
						<Switch
							bind:state={chatConfig.CONTEXT_COMPACTION_V2_REMINDER_ENABLED}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>
					<AdminSettingRow label={$i18n.t('Tool result budgets')} let:labelId>
						<Switch
							bind:state={chatConfig.CONTEXT_COMPACTION_V2_TOOL_BUDGET_ENABLED}
							ariaLabelledbyId={labelId}
						/>
					</AdminSettingRow>
					<p class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t(
							'Tool limits use the resolved model context window. Older history is compacted first; oversized results are summarized, with exact excerpts as fallback. Originals remain available.'
						)}
					</p>
					{#if chatConfig.CONTEXT_COMPACTION_V2_TOOL_BUDGET_ENABLED}
						<div class="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
							<AdminSettingField label={$i18n.t('Individual tool result limit (%)')}>
								<input
									type="number"
									min="1"
									max={chatConfig.CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT}
									step="1"
									required
									class={inputClass}
									aria-label={$i18n.t('Individual tool result limit (%)')}
									bind:value={chatConfig.CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT}
								/>
							</AdminSettingField>
							<AdminSettingField label={$i18n.t('Tool batch limit (%)')}>
								<input
									type="number"
									min={chatConfig.CONTEXT_COMPACTION_V2_TOOL_RESULT_PERCENT}
									max="100"
									step="1"
									required
									class={inputClass}
									aria-label={$i18n.t('Tool batch limit (%)')}
									bind:value={chatConfig.CONTEXT_COMPACTION_V2_TOOL_BATCH_PERCENT}
								/>
							</AdminSettingField>
						</div>
						<AdminSettingField label={$i18n.t('Tool result summary prompt')}>
							<Textarea
								className={textareaClass}
								bind:value={chatConfig.CONTEXT_COMPACTION_V2_TOOL_SUMMARY_PROMPT}
								placeholder={$i18n.t(
									'Leave empty to use the default prompt, or enter a custom prompt'
								)}
							/>
						</AdminSettingField>
					{/if}
					<AdminSettingField label={$i18n.t('V2 summary prompt')}>
						<Textarea
							className={textareaClass}
							bind:value={chatConfig.CONTEXT_COMPACTION_V2_SUMMARY_PROMPT}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
					{#if chatConfig.CONTEXT_COMPACTION_V2_REMINDER_ENABLED}
						<AdminSettingField
							label={$i18n.t('Context budget reminder prompt')}
							description={$i18n.t('Variables: {remaining_tokens}, {threshold}, {input_budget}.')}
						>
							<Textarea
								className={textareaClass}
								bind:value={chatConfig.CONTEXT_COMPACTION_V2_REMINDER_PROMPT}
								placeholder={$i18n.t(
									'Leave empty to use the default prompt, or enter a custom prompt'
								)}
							/>
						</AdminSettingField>
					{/if}
				{/if}
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('settings.admin.interface.sections.generation.title')}>
				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.titleGeneration.label')}
					description={$i18n.t('settings.admin.interface.titleGeneration.description')}
					let:labelId
				>
					<Switch bind:state={taskConfig.ENABLE_TITLE_GENERATION} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if taskConfig.ENABLE_TITLE_GENERATION}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.titleGenerationPrompt.label')}
						description={$i18n.t('settings.admin.interface.titleGenerationPrompt.description')}
					>
						<Textarea
							className={textareaClass}
							bind:value={taskConfig.TITLE_GENERATION_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.voiceModePrompt.label')}
					description={$i18n.t('settings.admin.interface.voiceModePrompt.description')}
					let:labelId
				>
					<Switch bind:state={taskConfig.ENABLE_VOICE_MODE_PROMPT} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if taskConfig.ENABLE_VOICE_MODE_PROMPT}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.promptTemplate.label')}
						description={$i18n.t('settings.admin.interface.promptTemplate.description')}
					>
						<Textarea
							className={textareaClass}
							bind:value={taskConfig.VOICE_MODE_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.followUpGeneration.label')}
					description={$i18n.t('settings.admin.interface.followUpGeneration.description')}
					let:labelId
				>
					<Switch bind:state={taskConfig.ENABLE_FOLLOW_UP_GENERATION} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if taskConfig.ENABLE_FOLLOW_UP_GENERATION}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.followUpGenerationPrompt.label')}
						description={$i18n.t('settings.admin.interface.followUpGenerationPrompt.description')}
					>
						<Textarea
							className={textareaClass}
							bind:value={taskConfig.FOLLOW_UP_GENERATION_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.tagsGeneration.label')}
					description={$i18n.t('settings.admin.interface.tagsGeneration.description')}
					let:labelId
				>
					<Switch bind:state={taskConfig.ENABLE_TAGS_GENERATION} ariaLabelledbyId={labelId} />
				</AdminSettingRow>

				{#if taskConfig.ENABLE_TAGS_GENERATION}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.tagsGenerationPrompt.label')}
						description={$i18n.t('settings.admin.interface.tagsGenerationPrompt.description')}
					>
						<Textarea
							className={textareaClass}
							bind:value={taskConfig.TAGS_GENERATION_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.retrievalQueryGeneration.label')}
					description={$i18n.t('settings.admin.interface.retrievalQueryGeneration.description')}
					let:labelId
				>
					<Switch
						bind:state={taskConfig.ENABLE_RETRIEVAL_QUERY_GENERATION}
						ariaLabelledbyId={labelId}
					/>
				</AdminSettingRow>

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.webSearchQueryGeneration.label')}
					description={$i18n.t('settings.admin.interface.webSearchQueryGeneration.description')}
					let:labelId
				>
					<Switch
						bind:state={taskConfig.ENABLE_SEARCH_QUERY_GENERATION}
						ariaLabelledbyId={labelId}
					/>
				</AdminSettingRow>

				<AdminSettingField
					label={$i18n.t('settings.admin.interface.queryGenerationPrompt.label')}
					description={$i18n.t('settings.admin.interface.queryGenerationPrompt.description')}
				>
					<Textarea
						className={textareaClass}
						bind:value={taskConfig.QUERY_GENERATION_PROMPT_TEMPLATE}
						placeholder={$i18n.t('Leave empty to use the default prompt, or enter a custom prompt')}
					/>
				</AdminSettingField>

				<AdminSettingRow
					label={$i18n.t('settings.admin.interface.autocompleteGeneration.label')}
					description={$i18n.t('settings.admin.interface.autocompleteGeneration.description')}
					let:labelId
				>
					<Switch
						bind:state={taskConfig.ENABLE_AUTOCOMPLETE_GENERATION}
						ariaLabelledbyId={labelId}
					/>
				</AdminSettingRow>

				{#if taskConfig.ENABLE_AUTOCOMPLETE_GENERATION}
					<AdminSettingField
						label={$i18n.t('settings.admin.interface.autocompleteGenerationInputMaxLength.label')}
						description={$i18n.t(
							'settings.admin.interface.autocompleteGenerationInputMaxLength.description'
						)}
					>
						<input
							type="number"
							min="-1"
							step="1"
							class={inputClass}
							bind:value={taskConfig.AUTOCOMPLETE_GENERATION_INPUT_MAX_LENGTH}
							placeholder={$i18n.t('-1 for no limit, or a positive integer for a specific limit')}
						/>
					</AdminSettingField>

					<AdminSettingField
						label={$i18n.t('settings.admin.interface.autocompleteGenerationPrompt.label')}
						description={$i18n.t(
							'settings.admin.interface.autocompleteGenerationPrompt.description'
						)}
					>
						<Textarea
							className={textareaClass}
							bind:value={taskConfig.AUTOCOMPLETE_GENERATION_PROMPT_TEMPLATE}
							placeholder={$i18n.t(
								'Leave empty to use the default prompt, or enter a custom prompt'
							)}
						/>
					</AdminSettingField>
				{/if}

				<AdminSettingField
					label={$i18n.t('settings.admin.interface.imagePromptGenerationPrompt.label')}
					description={$i18n.t('settings.admin.interface.imagePromptGenerationPrompt.description')}
				>
					<Textarea
						className={textareaClass}
						bind:value={taskConfig.IMAGE_PROMPT_GENERATION_PROMPT_TEMPLATE}
						placeholder={$i18n.t('Leave empty to use the default prompt, or enter a custom prompt')}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('settings.admin.interface.toolsFunctionCallingPrompt.label')}
					description={$i18n.t('settings.admin.interface.toolsFunctionCallingPrompt.description')}
				>
					<Textarea
						className={textareaClass}
						bind:value={taskConfig.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE}
						placeholder={$i18n.t('Leave empty to use the default prompt, or enter a custom prompt')}
					/>
				</AdminSettingField>
			</AdminSettingSection>
		</div>

		<div class="flex justify-end pt-6 text-sm font-normal">
			<button
				class="px-3.5 py-1.5 text-sm font-normal bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				type="submit"
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</form>
{:else}
	<div class="flex h-full w-full items-center justify-center">
		<Spinner className="size-5" />
	</div>
{/if}
