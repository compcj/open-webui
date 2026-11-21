<script lang="ts">
	import type { AskUserResult } from '../askUser';
	import { resolveToolApprovalMode } from '$lib/utils/toolPermissions';
	import { getContext, onMount, setContext, tick } from 'svelte';
	import type { TerminalCredential } from '$lib/apis/terminal';
	import { v4 as uuidv4 } from 'uuid';
	import {
		socket,
		user,
		openSideChatIds,
		tags,
		models,
		settings,
		config,
		toolServers,
		terminalServers,
		tools,
		skills,
		selectedTerminalId,
		temporaryChatAllowed,
		type Model
	} from '$lib/stores';
	import { createTemporaryChatId } from '$lib/utils/chatId';
	import { TemporaryContextController, isTemporaryContextRpc } from '$lib/utils/temporary-context';
	import { getCompactionMode } from '$lib/utils/context-compaction';
	import {
		actorChatPreferences,
		controlsForRequest,
		mergeAdvancedParams,
		migrateControlSelections,
		supportsModelControls
	} from '$lib/utils/model-controls';
	import {
		enqueueChatSettingsSave,
		getContextToolAvailability,
		resolveToolFeatureState,
		updateToolFeaturePreference,
		type ContextToolFeature
	} from '$lib/utils/tool-feature-preferences';
	import {
		resolveReasoningEffortOverride,
		getAvailableReasoningEffort
	} from '$lib/utils/reasoning-effort';
	import { updateUserSettings } from '$lib/apis/users';
	import { projectRawChat } from '$lib/utils/chat-context-copy';
	import type { TemporaryRequestIdentity } from '$lib/apis/temporary-contexts';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { generateOpenAIChatCompletion, getErrorMessage } from '$lib/apis/openai';
	import { getTaskIdsByChatId, resumeTemporaryChat, stopTasksByChatId } from '$lib/apis';
	import {
		createNewChat,
		getAllTags,
		getChatById,
		resolveChatMessageToolCall,
		updateChatById,
		updateChatParams
	} from '$lib/apis/chats';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';
	import {
		applyResponseStreamEvent,
		getPendingAskUserFromMessage
	} from '../Messages/structuredOutput';
	import Messages from '../Messages.svelte';
	import MessageInput from '../MessageInput.svelte';
	import FilesOverlay from '../MessageInput/FilesOverlay.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import ElicitationDialog from '../ElicitationDialog.svelte';
	import {
		convertMessagesToHistory,
		createMessagesList,
		isRasterImageContentType
	} from '$lib/utils';

	export let initialPrompt = '';
	export let initialView: any = null;
	export const captureView = () => ({ draft, files, context });
	export let chatId = '';
	export let onChatUpdate: (chat: { id: string; title?: string }) => void = () => {};
	export let parentChatId = '';
	export let parentFolderId: string | null = null;
	export let history;
	export let modelId: string;
	export let systemPrompt = '';
	const i18n = getContext<any>('i18n');
	let dragged = false;
	const originalChatId = parentChatId;
	const originalActor = $user?.id;
	const terminalId = $selectedTerminalId;
	$: sideChatAllowed =
		$user?.role === 'admin' ||
		($user?.role === 'user' && ($user.permissions?.chat?.side_chat ?? false));
	// Snapshot the active branch once; later parent edits and streaming stay in the parent.
	const context = (() => {
		if (initialView?.context) return projectRawChat({ messages: initialView.context }).messages;
		if (chatId) return [];
		const branch: any[] = [];
		let current = history?.currentId;
		const seen = new Set<string>();
		while (current && !seen.has(current) && history.messages[current]) {
			seen.add(current);
			const message = history.messages[current];
			branch.unshift(message);
			current = message.parentId;
		}
		while (branch.at(-1)?.role === 'assistant' && !branch.at(-1)?.done) branch.pop();
		return projectRawChat({
			messages: branch.map(
				(
					{ id, role, content, output, files, reasoning_details, model, models, timestamp },
					index
				) => ({
					id,
					role,
					content: content ?? '',
					...(output?.length && role === 'assistant' ? { output } : {}),
					files,
					reasoning_details,
					model,
					models,
					timestamp,
					done: true,
					parentId: branch[index - 1]?.id ?? null,
					childrenIds: branch[index + 1] ? [branch[index + 1].id] : []
				})
			)
		}).messages;
	})();
	const contextIds = new Set(context.map((message) => message.id));
	let loaded = !chatId;
	let isShared = false;
	let readOnly = false;
	let temporary =
		!chatId &&
		$user?.role !== 'admin' &&
		$temporaryChatAllowed &&
		($user?.permissions?.chat?.temporary_enforced ?? false);
	$: canUseTemporary = $temporaryChatAllowed;
	$: canSave =
		$user?.role === 'admin' ||
		!canUseTemporary ||
		!($user?.permissions?.chat?.temporary_enforced ?? false);
	$: canSend =
		loaded &&
		!closed &&
		originalActor === $user?.id &&
		!readOnly &&
		sideChatAllowed &&
		(temporary ? canUseTemporary : canSave);
	$: contextCompactionMode = sideModelId?.startsWith('agent:')
		? 'off'
		: getCompactionMode($config?.features);
	let selectedModels = [modelId];
	let atSelectedModel: Model | undefined = undefined;
	$: sideModelId = atSelectedModel?.id ?? selectedModels[0];
	let files: any[] = initialView?.files ?? [];
	let params: Record<string, any> = {};
	let legacyReasoning: Record<string, string> = {};
	$: modelControlSelections = controlsForRequest(
		params,
		$settings?.params ?? {},
		$models,
		legacyReasoning,
		$settings?.reasoningEffortByModel
	);
	$: modelControlAdvancedParams = mergeAdvancedParams($settings?.params ?? {}, params);
	$: reasoningEffortByModel = { ...($settings?.reasoningEffortByModel ?? {}), ...legacyReasoning };
	const savePreferences = async (
		fields: ('params' | 'toolFeaturesByModel' | 'reasoningEffortByModel')[]
	) => {
		const token = localStorage.token;
		await enqueueChatSettingsSave(
			async () => {
				if (disposed || $user?.id !== originalActor || localStorage.token !== token) return;
				const ui = Object.fromEntries(fields.map((field) => [field, $settings?.[field] ?? null]));
				if (!(await updateUserSettings(token, { ui })))
					throw new Error($i18n.t('Failed to update settings'));
			},
			(reason) => {
				if (!disposed && $user?.id === originalActor) error = getErrorMessage(reason);
			}
		);
	};
	const setModelControl = async (model: string, control: string, value: string) => {
		const target = id;
		const token = localStorage.token;
		const choices = { ...modelControlSelections[model] };
		if (value) choices[control] = value;
		else delete choices[control];
		const chat = migrateControlSelections(params.model_controls, legacyReasoning, $models);
		const personal = migrateControlSelections(
			$settings?.params?.model_controls,
			$settings?.reasoningEffortByModel,
			$models
		);
		params = { ...params, model_controls: { ...chat, [model]: choices } };
		const legacy = { ...($settings?.reasoningEffortByModel ?? {}) };
		delete legacy[model];
		delete legacyReasoning[model];
		settings.set({
			...$settings,
			reasoningEffortByModel: legacy,
			params: { ...$settings?.params, model_controls: { ...personal, [model]: choices } }
		});
		await savePreferences(['params', 'reasoningEffortByModel']);
		if (
			target &&
			target === id &&
			!temporary &&
			!isShared &&
			$user?.id === originalActor &&
			localStorage.token === token &&
			!disposed
		) {
			await updateChatParams(token, target, { model_controls: params.model_controls }).catch(
				(reason) => {
					error = getErrorMessage(reason);
				}
			);
		}
	};
	const setReasoningEffort = (model: string, value: string) => {
		legacyReasoning = { ...legacyReasoning, [model]: value };
		const remembered = { ...$settings?.reasoningEffortByModel };
		if (value) remembered[model] = value;
		else delete remembered[model];
		settings.set({ ...$settings, reasoningEffortByModel: remembered });
		void savePreferences(['reasoningEffortByModel']);
	};
	let selectedToolIds: string[] = [];
	let selectedSkillIds: string[] = [];
	let selectedFilterIds: string[] = [];
	let webSearchEnabled = false;
	let imageGenerationEnabled = false;
	let codeInterpreterEnabled = false;
	let knowledgeEnabled = true,
		knowledgeWriteEnabled = false,
		memoryEnabled = true,
		notesEnabled = true;
	let confirmSearch: (() => void) | null = null;
	let confirmedSearchModel: string | null = null;
	$: if (confirmedSearchModel && confirmedSearchModel !== sideModelId) confirmedSearchModel = null;
	const setContextFeature = (feature: ContextToolFeature, enabled: boolean) => {
		if (feature === 'knowledge') knowledgeEnabled = enabled;
		if (feature === 'knowledge_write') knowledgeWriteEnabled = enabled;
		if (feature === 'memory') memoryEnabled = enabled;
		if (feature === 'notes') notesEnabled = enabled;
		settings.set({
			...$settings,
			toolFeaturesByModel: updateToolFeaturePreference(
				$settings?.toolFeaturesByModel,
				sideModelId,
				feature,
				enabled
			)
		});
		void savePreferences(['toolFeaturesByModel']);
	};

	const applyModelDefaults = async (selectedModelId: string) => {
		if (!$tools) tools.set(await getTools(localStorage.token));
		if (!$skills) skills.set(await getSkills(localStorage.token));
		const selectedModel = $models.find((model) => model.id === selectedModelId);
		if (!selectedModel || $tools === null || $skills === null) return;
		if (selectedModelId !== sideModelId || originalActor !== $user?.id) return;
		const meta = selectedModel.info?.meta;
		const defaultFeature = (feature: 'web_search' | 'image_generation' | 'code_interpreter') =>
			!!meta?.defaultFeatureIds?.includes(feature) &&
			(meta?.capabilities?.[feature] ?? true) &&
			!!$config?.features?.[`enable_${feature}`] &&
			($user?.role === 'admin' || !!$user?.permissions?.features?.[feature]);
		selectedToolIds =
			meta?.toolIds?.filter((toolId: string) =>
				($tools as any[]).some((tool) => tool.id === toolId && tool.authenticated !== false)
			) ??
			$settings?.tools ??
			[];
		selectedSkillIds = (meta?.skillIds ?? []).filter((skillId: string) =>
			($skills as any[]).some((skill) => skill.id === skillId && skill.is_active)
		);
		selectedFilterIds = (meta?.defaultFilterIds ?? []).filter((filterId: string) =>
			selectedModel.filters?.some((filter: { id: string }) => filter.id === filterId)
		);
		const requested = resolveToolFeatureState({
			modelId: selectedModelId,
			preferences: $settings?.toolFeaturesByModel,
			defaults: {
				web_search: defaultFeature('web_search'),
				image_generation: defaultFeature('image_generation'),
				knowledge: true,
				knowledge_write: false,
				memory: true,
				notes: true
			},
			available: {
				web_search: true,
				image_generation: true,
				knowledge: true,
				knowledge_write: true,
				memory: true,
				notes: true
			}
		});
		webSearchEnabled = requested.web_search;
		imageGenerationEnabled = requested.image_generation;
		knowledgeEnabled = requested.knowledge;
		knowledgeWriteEnabled = requested.knowledge_write;
		memoryEnabled = requested.memory;
		notesEnabled = requested.notes;
		codeInterpreterEnabled = defaultFeature('code_interpreter');
	};
	$: if (sideModelId && loaded) void applyModelDefaults(sideModelId);
	$: toolApprovalMode = resolveToolApprovalMode(
		params?.tool_approval_mode ?? $settings?.params?.tool_approval_mode,
		$config?.features?.enable_tool_permissions_auto ?? false
	);
	let interactions: { id: string; type: string; data: any; callback: (value: any) => void }[] = [];
	$: interaction = interactions[0];
	const resolveInteraction = (pending: typeof interaction, value: any) => {
		if (!interactions.includes(pending)) return;
		interactions = interactions.filter((item) => item !== pending);
		pending.callback(value);
	};
	const cancelInteractions = () => {
		for (const pending of interactions)
			resolveInteraction(pending, { status: 'cancelled', answers: {} });
	};
	$: askUser = {
		id: interaction?.id,
		show: interaction?.type === 'request:user_input',
		questions: interaction?.data?.questions ?? [],
		allowOther: interaction?.data?.allow_other ?? true,
		timeoutMs: interaction?.data?.timeout_ms ?? null,
		onConfirm: (value: any) => resolveInteraction(interaction, value),
		onCancel: () => resolveInteraction(interaction, { status: 'cancelled', answers: {} })
	};
	const resolvePendingAskUser = async (
		{ message, call }: NonNullable<typeof pendingAskUser>,
		action: 'answer' | 'reject',
		answers?: unknown
	) => {
		error = '';
		try {
			await resolveChatMessageToolCall(
				localStorage.token,
				id,
				message.id,
				call.call_id || call.id,
				action,
				{ answers }
			);
			// reconnect reads done to tell whether the resumed reply is still running
			message.done = false;
		} catch (err) {
			error = getErrorMessage(err);
		}
		void reconnect();
	};
	$: pendingAskUser =
		[...messages]
			.reverse()
			.filter((message) => (message.user_id ? message.user_id === $user?.id : !isShared))
			.map(getPendingAskUserFromMessage)
			.find(Boolean) ?? null;
	$: savedAskUserPrompt = ((pending) =>
		pending
			? {
					...pending.prompt,
					onConfirm: (value: AskUserResult) =>
						void resolvePendingAskUser(pending, 'answer', value.answers),
					onCancel: () => void resolvePendingAskUser(pending, 'reject')
				}
			: null)(pendingAskUser);
	let system = [
		systemPrompt,
		'This is a separate side conversation. Earlier messages are reference context only. Answer the side question; do not continue the parent task.'
	]
		.filter(Boolean)
		.join('\n\n');
	let id = chatId;
	const selectModels = (ids: string[]) => {
		if (!ids[0] || originalActor !== $user?.id) return;
		selectedModels = [ids[0]];
		atSelectedModel = undefined;
		if (id && !temporary && !isShared)
			void updateChatById(localStorage.token, id, { models: [...selectedModels] }).catch(
				(reason) => {
					if (!disposed) error = getErrorMessage(reason);
				}
			);
	};
	const temporaryController = new TemporaryContextController({
		actor: () => ({ id: $user?.id ?? '', token: localStorage.token ?? '' }),
		session: () => $socket?.id ?? '',
		cancelInteractions,
		onError: (reason) => {
			if (!disposed) error = getErrorMessage(reason);
		},
		applyEvent: (event, callback) => receive(event, callback),
		applyState: (state) => {
			if (disposed || id !== temporaryController.chatId || originalActor !== $user?.id) return;
			messages = messages.map((message) => state.history?.messages?.[message.id] ?? message);
			busy = state.uncertain || (state.tasks ?? []).some((task: any) => task.status !== 'done');
		}
	});
	let temporaryTerminalCredential: TerminalCredential | null = null;
	setContext('temporary-context', {
		controller: temporaryController,
		get identity() {
			return temporaryController.identity;
		},
		get chatId() {
			return temporaryController.chatId;
		},
		terminalCredential: (token: string, chatId: string) => {
			const identity = temporaryController.identity;
			if (!identity || temporaryController.chatId !== chatId) return token;
			if (
				!temporaryTerminalCredential ||
				typeof temporaryTerminalCredential === 'string' ||
				temporaryTerminalCredential.token !== token ||
				temporaryTerminalCredential.temporaryContext.id !== identity.id ||
				temporaryTerminalCredential.temporaryContext.epoch !== identity.epoch
			) {
				const actor = $user?.id;
				const captured = { id: identity.id, epoch: identity.epoch };
				temporaryTerminalCredential = {
					token,
					temporaryContext: captured,
					isCurrent: () =>
						$user?.id === actor &&
						localStorage.token === token &&
						temporaryController.identity?.id === captured.id &&
						temporaryController.identity?.epoch === captured.epoch
				};
			}
			return temporaryTerminalCredential;
		}
	});
	let messages: any[] = [];
	let draft = initialView?.draft ?? '';
	let error = '';
	let busy = false;
	let sending = false;
	let recovering = false;
	let receivedDuringRecovery = new Set<string>();
	let disposed = false;
	let closed = false;
	let request: Promise<any> | null = null;
	let scroller: HTMLDivElement;
	let autoScroll = true;
	let input: MessageInput;
	const inputId = `side-chat-input-${uuidv4()}`;
	const messagesContainerId = `${inputId}-messages`;
	$: sideHistory = {
		currentId: messages.at(-1)?.id ?? null,
		messages: Object.fromEntries(
			messages.map((message, index) => [
				message.id,
				// The copied context belongs to saved history, outside this panel's visible branch.
				index === 0 ? { ...message, parentId: null } : message
			])
		)
	};
	const scroll = async () => {
		await tick();
		if (autoScroll) scroller?.scrollTo({ top: scroller.scrollHeight });
	};

	const receiveScoped = (event: any, callback?: (value: any) => void) => {
		if (!id || event?.chat_id !== id || originalActor !== $user?.id || isTemporaryContextRpc(event))
			return;
		if (event.temporary_context || temporaryController.identity) {
			void temporaryController.receive(event, callback).catch((reason) => {
				if (!disposed) error = getErrorMessage(reason);
			});
			return;
		}
		receive(event, callback);
	};

	const receive = (event: any, callback?: (value: any) => void) => {
		if (disposed || !id || event.chat_id !== id || originalActor !== $user?.id) return;
		const { type, data } = event.data ?? {};
		if (recovering && event.message_id) receivedDuringRecovery.add(event.message_id);
		if (type === 'chat:title') {
			onChatUpdate({ id, title: data });
			void refreshChatList(localStorage.token);
			return;
		}
		if (type === 'chat:tags') {
			void getAllTags(localStorage.token).then((value) => tags.set(value));
			return;
		}
		if (type === 'context_compaction') {
			const checkpoint = [...context, ...messages].find(
				(message) => message.id === data.checkpoint_message_id
			);
			if (checkpoint && data.context_summary) checkpoint.contextSummary = data.context_summary;
			return;
		}
		if (type === 'request:interaction:done') {
			interactions = interactions.filter((item) => item.id !== data.interaction_id);
			return;
		}
		if (
			callback &&
			['request:user_input', 'request:elicitation', 'confirmation', 'input'].includes(type)
		) {
			if (!canSend) callback({ status: 'cancelled', answers: {} });
			else if (data?.tool_call && toolApprovalMode === 'full') callback(true);
			else
				interactions = [
					...interactions,
					{ id: data.interaction_id ?? uuidv4(), type, data, callback }
				];
			return;
		}
		const message = messages.find((m) => m.id === event.message_id);
		if (!message) return;
		if (type === 'response:completion')
			message.output = applyResponseStreamEvent(message.output, data);
		else if (type === 'chat:completion') {
			if (data.output) message.output = data.output;
			else if (data.content !== undefined) message.content = data.content;
			if (data.done) {
				message.done = true;
				busy = false;
				if (!temporary)
					$socket?.emit('events:chat', { chat_id: id, data: { type: 'last_read_at' } });
			}
		} else if (type === 'chat:message:error') {
			error = getErrorMessage(data.error);
			message.done = true;
			busy = false;
		} else if (type === 'status') {
			message.statusHistory = [...(message.statusHistory ?? []), data];
		} else if (type === 'source' || type === 'citation') {
			message.sources = [...(message.sources ?? []), data];
		} else if (type === 'chat:message:files' || type === 'files') {
			message.files = [...(message.files ?? []), ...data.files];
		} else if (type === 'chat:message' || type === 'replace') {
			message.content = data.content;
		} else if (type === 'chat:message:delta' || type === 'message') {
			message.content += data.content;
		} else if (type === 'chat:tasks:cancel') {
			cancelInteractions();
			if (data.output) message.output = data.output;
			message.done = true;
			busy = false;
		}
		messages = messages;
		void scroll();
	};

	const reconnect = async () => {
		if (!sideChatAllowed || (!temporary && !canSave) || !id || !$socket?.id || recovering) return;
		recovering = true;
		receivedDuringRecovery = new Set();
		try {
			if (!temporary) {
				const tasks = await getTaskIdsByChatId(localStorage.token, id);
				const saved = await getChatById(localStorage.token, id);
				if (disposed || !sideChatAllowed) return;
				isShared = saved.user_id !== $user?.id;
				readOnly = isShared && saved.chat.share_mode !== 'continue';
				if (!loaded) {
					selectedModels = [saved.chat.models?.[0] ?? modelId];
					const preferences = actorChatPreferences(saved, $user?.id);
					params = preferences.params;
					legacyReasoning = preferences.legacyReasoning;
					system =
						params.system ?? (!isShared ? saved.chat.system : undefined) ?? $settings?.system ?? '';
				}
				const savedHistory = saved.chat.history ?? convertMessagesToHistory(saved.chat.messages);
				messages = createMessagesList(
					savedHistory,
					saved.current_message_id ?? savedHistory.currentId
				)
					.filter((message) => !contextIds.has(message.id))
					.map((message) =>
						receivedDuringRecovery.has(message.id)
							? (messages.find((local) => local.id === message.id) ?? message)
							: message
					);
				busy = !!tasks.task_ids.length && messages.at(-1)?.done === false;
				if (!busy) messages = messages.map((message) => ({ ...message, done: true }));
				loaded = true;
				onChatUpdate({ id, title: saved.title });
				void scroll();
				return;
			}
			if (temporaryController.identity) {
				await temporaryController.rebind();
				return;
			}
			const result = await resumeTemporaryChat(localStorage.token, id, $socket.id);
			if (disposed || !canSend) return;
			for (const stream of result.streams) {
				const message = messages.find((m) => m.id === stream.message_id);
				if (message && !message.done) message.output = stream.output;
			}

			if (busy && !result.task_ids.length) {
				busy = false;
				error = $i18n.t('Connection lost. Please retry.');
				messages.forEach((m) => {
					m.done = true;
				});
			}
			messages = messages;
		} catch (err) {
			if (!disposed && originalActor === $user?.id) error = getErrorMessage(err);
		} finally {
			recovering = false;
		}
	};

	const send = async () => {
		if (!canSend || disposed) return;
		const question = draft.trim();
		if ((!question && !files.length) || busy || sending || recovering || pendingAskUser) return;
		if (!atSelectedModel && selectedModels.length !== 1) {
			error = $i18n.t('Select only one model');
			return;
		}
		const selectedModel = $models.find((model) => model.id === sideModelId);
		if (sideModelId?.startsWith('agent:') && (temporary || !selectedModel)) {
			error = selectedModel
				? $i18n.t('Agents require a saved chat with one model.')
				: $i18n.t('Agent is offline. Reconnect its terminal or choose another model.');
			return;
		}
		if (files.some((file) => file.status === 'uploading' || file.status === 'error')) {
			error = $i18n.t('Please wait for files to finish uploading.');
			return;
		}
		if (!$socket?.connected || !sideModelId) {
			error = $i18n.t('Connect and select a model first.');
			return;
		}
		const available = getContextToolAvailability([selectedModel], $config?.features, $user);
		const allows = (feature: 'web_search' | 'image_generation' | 'code_interpreter') =>
			!!selectedModel &&
			!!$config?.features?.[`enable_${feature}`] &&
			(selectedModel.info?.meta?.capabilities?.[feature] ?? true) &&
			($user?.role === 'admin' || $user?.permissions?.features?.[feature]);
		const search =
			!!allows('web_search') && (webSearchEnabled || $settings?.webSearch === 'always');
		const token = localStorage.token;
		const stillCurrent = () =>
			!disposed && originalActor === $user?.id && localStorage.token === token;
		if (
			search &&
			$config?.features?.enable_web_search_confirmation &&
			confirmedSearchModel !== sideModelId
		) {
			const capturedModel = sideModelId;
			const capturedFiles = files;
			confirmSearch = () => {
				confirmSearch = null;
				if (
					!stillCurrent() ||
					sideModelId !== capturedModel ||
					draft.trim() !== question ||
					files !== capturedFiles
				)
					return;
				confirmedSearchModel = capturedModel;
				void send();
			};
			return;
		}
		error = '';
		sending = true;
		try {
			if (!id) {
				if (temporary) {
					id =
						contextCompactionMode === 'v2'
							? `temporary:${(await temporaryController.ensure()).id}`
							: createTemporaryChatId($socket.id);
				} else {
					const created = await createNewChat(
						localStorage.token,
						{
							title: question.slice(0, 100) || $i18n.t('New Chat'),
							...(originalChatId && {
								originalChatId,
								branchPointMessageId: context.at(-1)?.id ?? null
							}),
							models: [sideModelId],
							params: { ...params, system },
							history: convertMessagesToHistory(context),
							messages: context,
							files: context.flatMap((message) => message.files ?? []),
							tags: [],
							timestamp: Date.now()
						},
						parentFolderId
					);
					if (!created?.id) throw new Error($i18n.t('Failed to create chat'));
					id = created.id;
					onChatUpdate({ id, title: created.title });
					void refreshChatList(localStorage.token);
					if (created.folder_id) void refreshFolderChatLists(created.folder_id, created);
				}
				if (!stillCurrent()) return;
				openSideChatIds.update((ids) => new Set([...ids, id]));
			} else if (!temporary && !isShared) {
				await updateChatById(localStorage.token, id, { params: { ...params, system } });
			}
			await tick();
			if (!canSend || !stillCurrent()) return;
			if (!(await input?.uploadPendingFiles(id)) || busy || !canSend || !stillCurrent()) return;
			const userMessage = {
				id: uuidv4(),
				parentId:
					messages.at(-1)?.id ??
					(temporary && !temporaryController.identity ? null : (context.at(-1)?.id ?? null)),
				childrenIds: [] as string[],
				role: 'user',
				content: question,
				files: [...files],
				done: true,
				timestamp: Math.floor(Date.now() / 1000),
				models: [sideModelId]
			};
			const answer = {
				id: uuidv4(),
				parentId: userMessage.id,
				childrenIds: [] as string[],
				role: 'assistant',
				user_id: $user?.id,
				content: '',
				output: [],
				done: false,
				timestamp: Math.floor(Date.now() / 1000),
				model: sideModelId
			};
			userMessage.childrenIds.push(answer.id);
			if (messages.length) messages.at(-1).childrenIds.push(userMessage.id);
			const conversation = [...context, ...messages, userMessage].map((message) => {
				if (message.role === 'assistant' && message.output?.length)
					return {
						id: message.id,
						role: message.role,
						model: message.model,
						output: message.output,
						contextSummary: message.contextSummary
					};
				const images = (message.files ?? []).filter(
					(file: any) => file.type === 'image' || isRasterImageContentType(file.content_type)
				);
				return {
					id: message.id,
					role: message.role,
					contextSummary: message.contextSummary,
					content: images.length
						? [
								{ type: 'text', text: message.content ?? '' },
								...images.map((file: any) => ({ type: 'image_url', image_url: { url: file.url } }))
							]
						: message.content
				};
			});
			const requestFiles = [...context, ...messages, userMessage].flatMap(
				(message) => message.files ?? []
			);
			let temporaryIdentity: TemporaryRequestIdentity | undefined;
			if (temporaryController.identity) {
				const raw = convertMessagesToHistory([...context, ...messages, userMessage, answer]);
				temporaryIdentity = await temporaryController.prepare(
					raw,
					userMessage.id,
					{ ...params, system },
					requestFiles
				);
				if (!stillCurrent()) return;
			}
			const directServers = selectedToolIds
				.filter((id) => id.startsWith('direct_server:'))
				.map((id) => id.slice('direct_server:'.length));
			messages = [...messages, userMessage, answer];
			draft = '';
			files = [];
			autoScroll = true;
			input?.setText('');
			busy = true;
			void scroll();
			request = generateOpenAIChatCompletion(token, {
				model: sideModelId,
				model_item: $models.find((model) => model.id === sideModelId),
				stream: true,
				chat_id: id,
				...(temporaryIdentity ? { temporary_context: temporaryIdentity } : {}),
				session_id: $socket.id,
				id: answer.id,
				user_message: userMessage,
				...(!temporary &&
					!isShared && {
						parent_id: userMessage.parentId,
						background_tasks:
							messages.length === 2
								? {
										title_generation: $settings?.title?.auto ?? true,
										tags_generation: $settings?.autoTags ?? true
									}
								: {}
					}),
				params: {
					...modelControlAdvancedParams,
					model_controls: modelControlSelections,
					...(!supportsModelControls(selectedModel) && selectedModel
						? {
								reasoning_effort:
									resolveReasoningEffortOverride(
										reasoningEffortByModel[sideModelId],
										getAvailableReasoningEffort(selectedModel)
									) ?? modelControlAdvancedParams.reasoning_effort
							}
						: {}),
					tool_approval_mode: toolApprovalMode
				},
				files: requestFiles,
				tool_ids: selectedToolIds.filter((id) => !id.startsWith('direct_server:')),
				skill_ids: selectedSkillIds,
				filter_ids: selectedFilterIds,
				terminal_id:
					$config?.features?.enable_tool_servers &&
					(($terminalServers ?? []).some((server) => server.id === terminalId) ||
						$config?.features?.enable_direct_integrations)
						? terminalId
						: undefined,
				tool_servers:
					$config?.features?.enable_tool_servers && $config?.features?.enable_direct_integrations
						? [
								...($toolServers ?? []).filter(
									(server: any, index) =>
										directServers.includes(String(index)) || directServers.includes(server.id)
								),
								...($terminalServers ?? []).filter((server) => !server.id)
							]
						: [],
				features: {
					web_search: search,
					image_generation: !!allows('image_generation') && imageGenerationEnabled,
					code_interpreter: !!allows('code_interpreter') && codeInterpreterEnabled,
					knowledge: available.knowledge && knowledgeEnabled,
					knowledge_write:
						available.knowledge &&
						knowledgeEnabled &&
						available.knowledge_write &&
						knowledgeWriteEnabled,
					memory:
						available.memory &&
						memoryEnabled &&
						($settings?.memory ?? $config?.features?.enable_memories ?? false),
					notes: available.notes && notesEnabled
				},
				messages: [{ role: 'system', content: system }, ...(temporary ? conversation : [])]
			});
			const result = await request;
			if (result?.error) throw result.error;
		} catch (err) {
			if (!stillCurrent()) return;
			error = getErrorMessage(err);
			if (temporaryController.identity) {
				await temporaryController.handleError(err).catch(() => {});
				return;
			}
			if (busy && messages.at(-1)) messages.at(-1).done = true;
			busy = false;
			messages = messages;
		} finally {
			request = null;
			sending = false;
		}
	};

	export const isTemporary = () => temporary && !closed && !!id;

	export const requestClose = async () => {
		if (!isTemporary()) return true;
		try {
			closed = temporaryController.identity ? await temporaryController.close() : await stop();
			return closed;
		} catch (reason) {
			if (!disposed) error = getErrorMessage(reason);
			return false;
		}
	};

	const stop = async () => {
		cancelInteractions();
		await request?.catch(() => {});
		if (!id || originalActor !== $user?.id) return false;
		const target = id;
		const token = localStorage.token;
		const leaf = messages.at(-1)?.id;
		try {
			const result = await stopTasksByChatId(
				token,
				target,
				temporaryController.identity ?? undefined
			);
			if (result?.status === false) throw new Error(result.message ?? 'Stop unavailable');
			if (
				id !== target ||
				messages.at(-1)?.id !== leaf ||
				originalActor !== $user?.id ||
				localStorage.token !== token ||
				disposed
			)
				return false;
			if (temporaryController.identity && !(await temporaryController.waitForSettled()))
				return false;
			if (
				id !== target ||
				messages.at(-1)?.id !== leaf ||
				originalActor !== $user?.id ||
				localStorage.token !== token ||
				disposed
			)
				return false;
			busy = false;
			messages.forEach((m) => {
				m.done = true;
			});
			messages = messages;
			return true;
		} catch (err) {
			if (!disposed) error = getErrorMessage(err);
			return false;
		}
	};

	$: if (!canSend) {
		cancelInteractions();
		if (busy && !readOnly) void stop();
	}

	onMount(() => {
		const connection = $socket;
		const temporaryRpc = (message: CustomEvent) => {
			const { event, callback, run } = message.detail;
			void temporaryController
				.receive(event, callback, (_event, _callback, current) => run(current))
				.catch((reason) => {
					if (!disposed) error = getErrorMessage(reason);
				});
		};
		const disconnected = () => {
			temporaryController.disconnect();
			cancelInteractions();
		};
		window.addEventListener('temporary-context:rpc', temporaryRpc as EventListener);
		connection?.on('events', receiveScoped);
		connection?.on('connect', reconnect);
		connection?.on('disconnect', disconnected);
		if (id) {
			openSideChatIds.update((ids) => new Set([...ids, id]));
			void reconnect();
		}
		void tick().then(() => {
			if (disposed) return;
			if (initialView) input?.setText(draft);
			if (canSend && initialPrompt && !initialView) {
				draft = initialView?.draft ?? initialPrompt;
				input?.setText(draft);
				void send();
			}
			input?.focus();
		});
		return () => {
			disposed = true;
			window.removeEventListener('temporary-context:rpc', temporaryRpc as EventListener);
			connection?.off('events', receiveScoped);
			connection?.off('connect', reconnect);
			connection?.off('disconnect', disconnected);
			const scoped = !!temporaryController.identity;
			temporaryController.detach(true);
			cancelInteractions();
			if (id) {
				if (temporary && !scoped && !closed) void stop();
				openSideChatIds.update((ids) => {
					const next = new Set(ids);
					next.delete(id);
					return next;
				});
			}
		};
	});
</script>

<div
	id={`${inputId}-pane`}
	class="relative flex h-full min-h-0 min-w-0 w-full flex-col @container overflow-hidden"
>
	<FilesOverlay show={dragged} />
	<div
		id={messagesContainerId}
		bind:this={scroller}
		class="flex-1 min-h-0 overflow-y-auto pb-2.5 scrollbar-hidden"
		on:scroll={() => {
			autoScroll = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 50;
		}}
	>
		{#if messages.length}
			<Messages
				className="flex pt-4"
				chatId={id}
				history={sideHistory}
				selectedModels={[sideModelId]}
				prompt={draft}
				atSelectedModel={undefined}
				readOnly
				forkHandler={null}
				shareMode={null}
				sendMessage={() => {}}
				continueResponse={() => {}}
				regenerateResponse={() => {}}
				mergeResponses={() => {}}
				chatActionHandler={() => {}}
				allowDelete={false}
				allowToolApproval={canSend}
				onToolCallResolved={(res: { message_id: string }) => {
					// reconnect reads done to tell whether the resumed reply is still running
					const message = messages.find((m) => m.id === res.message_id);
					if (message) message.done = false;
					void reconnect();
				}}
				editCodeBlock={false}
				{autoScroll}
				{messagesContainerId}
			/>
		{:else if loaded}
			<div class="flex h-full items-center justify-center px-6">
				<div class="max-w-56 text-center">
					<p class="text-sm font-normal text-gray-600 dark:text-gray-300">
						{temporary ? $i18n.t('Temporary chat') : $i18n.t('New Chat')}
					</p>
					<p class="mt-1.5 text-xs leading-relaxed text-gray-400 dark:text-gray-500">
						{temporary
							? $i18n.t('Messages disappear when you close this tab.')
							: $i18n.t('Messages are saved to your chats.')}
					</p>
				</div>
			</div>
		{/if}
	</div>
	<div class="shrink-0 pb-2 z-10">
		{#if error}<p role="alert" class="px-4 py-2 text-xs text-gray-500 dark:text-gray-400">
				{error}
			</p>{/if}
		{#if canSend}
			<MessageInput
				bind:this={input}
				bind:dragged
				bind:prompt={draft}
				bind:autoScroll
				chatId={id}
				history={sideHistory}
				bind:selectedModels
				multipleModels={false}
				bind:atSelectedModel
				bind:files
				bind:params
				bind:selectedToolIds
				bind:selectedSkillIds
				bind:selectedFilterIds
				bind:webSearchEnabled
				bind:imageGenerationEnabled
				bind:codeInterpreterEnabled
				bind:knowledgeEnabled
				bind:knowledgeWriteEnabled
				bind:memoryEnabled
				bind:notesEnabled
				onContextToolToggle={setContextFeature}
				{modelControlSelections}
				{modelControlAdvancedParams}
				onModelControlChange={setModelControl}
				{reasoningEffortByModel}
				onReasoningEffortChange={setReasoningEffort}
				{toolApprovalMode}
				onToolApprovalModeChange={(mode: string) => {
					params = { ...params, tool_approval_mode: mode };
				}}
				askUser={savedAskUserPrompt ?? askUser}
				dropzoneId={`${inputId}-pane`}
				{inputId}
				{messagesContainerId}
				{temporary}
				{contextCompactionMode}
				onModelMention={(model) => selectModels([model.id])}
				onModelSelect={selectModels}
				onTemporaryChange={(value) => {
					if (!id && !sending) temporary = value;
				}}
				embedded
				callEnabled={false}
				terminalSelectionEnabled={false}
				generating={busy}
				stopResponse={stop}
				createMessagePair={() => {}}
				on:submit={() => void send()}
			/>
		{:else if loaded}
			<p role="status" class="px-4 py-2 text-xs text-gray-500">
				{$i18n.t('You do not have permission to use side chat.')}
			</p>
		{/if}
	</div>
</div>

{#if confirmSearch}
	<ConfirmDialog
		show={true}
		title={$i18n.t('Web Search')}
		message={$i18n.t('Do you want to enable web search for this chat?')}
		on:confirm={() => confirmSearch?.()}
		on:cancel={() => (confirmSearch = null)}
	/>
{/if}

{#if interaction}
	{#key interaction.id}
		{@const pending = interaction}
		{#if pending.type === 'request:elicitation'}
			<ElicitationDialog
				data={pending.data}
				onResponse={(value) => resolveInteraction(pending, value)}
			/>
		{:else if pending.type === 'confirmation' || pending.type === 'input'}
			<ConfirmDialog
				show={true}
				title={pending.data.title ?? $i18n.t('Tool Permissions')}
				message={pending.data.message ?? ''}
				input={pending.type === 'input'}
				inputPlaceholder={pending.data.placeholder ?? ''}
				inputValue={pending.data.value ?? ''}
				inputType={pending.data.input?.type ?? ''}
				inputOptions={pending.data.input?.options ?? []}
				on:confirm={(event) =>
					resolveInteraction(pending, pending.type === 'input' ? event.detail : true)}
				on:cancel={() => resolveInteraction(pending, false)}
			>
				{#if pending.data.tool_call}
					<div class="text-sm text-gray-500">
						<div class="font-medium break-all">{pending.data.tool_call.name}</div>
						<pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(
								pending.data.tool_call.arguments,
								null,
								2
							)}</pre>
					</div>
				{/if}
			</ConfirmDialog>
		{/if}
	{/key}
{/if}
