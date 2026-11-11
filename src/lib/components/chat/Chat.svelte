<script lang="ts">
	import {
		TemporaryContextController,
		isTemporaryContextRpc,
		reconcileTemporaryHistory
	} from '$lib/utils/temporary-context';
	import type { TemporaryIdentity, TemporaryRequestIdentity } from '$lib/apis/temporary-contexts';
	import type { TerminalCredential } from '$lib/apis/terminal';
	import { projectRawChat } from '$lib/utils/chat-context-copy';
	import { isVisibleToolResultStatus, mergeCompactionStatus } from '$lib/utils/context-compaction';
	import { resolveToolApprovalMode } from '$lib/utils/toolPermissions';
	import type { TerminalContext } from '$lib/types';
	import {
		estimateTokens,
		estimateMessagesTokens,
		estimateUsageGrowth,
		captureUsageCheckpoint
	} from '$lib/utils/contextUsage';
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';

	import { getContext, setContext, onDestroy, onMount, tick } from 'svelte';
	import { fade } from 'svelte/transition';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { goto, beforeNavigate, afterNavigate } from '$app/navigation';
	import { page } from '$app/stores';

	import { get, writable, type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';
	import { getModels } from '$lib/apis';
	import equal from 'fast-deep-equal';

	import {
		chatId,
		config,
		type Model,
		models,
		tags as allTags,
		settings,
		showSidebar,
		WEBUI_NAME,
		banners,
		user,
		socket,
		audioQueue,
		showTabs,
		sideChatRequest,
		showCallOverlay,
		temporaryChatEnabled,
		temporaryChatAllowed,
		mobile,
		chatTitle,
		showArtifacts,
		artifactContents,
		tools,
		skills,
		terminalSkills,
		toolServers,
		terminalServers,
		functions,
		selectedFolder,
		showEmbeds,
		selectedTerminalId,
		showFileNavPath,
		showFileNavDir,
		chatRequestQueues,
		desktopEvent
	} from '$lib/stores';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import {
		resolveDefaultModelIds,
		convertMessagesToHistory,
		copyToClipboard,
		getMessageContentParts,
		createMessagesList,
		getDeepestChildId,
		sanitizeHistory,
		getPromptVariables,
		processDetails,
		removeAllDetails,
		getCodeBlockContents,
		displayFileHandler,
		getUsageTokenCount,
		isRasterImageContentType
	} from '$lib/utils';
	import {
		actorChatPreferences,
		controlsForRequest,
		mergeAdvancedParams,
		migrateControlSelections,
		supportsModelControls
	} from '$lib/utils/model-controls';
	import { getCompactionMode, selectContextUsage } from '$lib/utils/context-compaction';
	import { AudioQueue } from '$lib/utils/audio';
	import { RealtimeCall, getBridgeTurnState, type BridgeSubmission } from '$lib/utils/realtime';
	import { acquireWakeLock } from '$lib/utils/wakeLock';
	import { routeAvatarAnimation } from '$lib/utils/voice-avatar';
	import { createTemporaryChatId, isTemporaryChatId } from '$lib/utils/chatId';
	import { getTemporaryConnectionSessionId } from '$lib/utils/connection-headers';
	import {
		fillReasoningEffortFromLastUsed,
		getAvailableReasoningEffort,
		resolveReasoningEffortOverride,
		sanitizeReasoningEffortByModel
	} from '$lib/utils/reasoning-effort';
	import {
		enqueueChatSettingsSave,
		getContextToolAvailability,
		getToolFeatureModelId,
		resolveToolFeatureState,
		updateToolFeaturePreference,
		type ToolFeature,
		type ContextToolFeature,
		type ToolFeaturePreferences
	} from '$lib/utils/tool-feature-preferences';
	import { resumeTemporaryChat } from '$lib/apis';
	import {
		applyResponseStreamEvent,
		getOutputText,
		getPendingAskUserFromMessage,
		setOutputText
	} from './Messages/structuredOutput';

	import {
		addChatVoiceMessage,
		updateChatVoiceMessage,
		addTagById,
		archiveChatById,
		compactChatById,
		createNewChat,
		deleteChatById,
		forkChatById,
		getAllTags,
		getChatById,
		getChatContextUsage,
		getTagsById,
		importChats,
		resolveChatMessageToolCall,
		updateChatById,
		updateChatParams,
		updateChatFolderIdById
	} from '$lib/apis/chats';
	import { resolveTerminalConnection, setCwd, type FilesystemFile } from '$lib/apis/terminal';
	import { generateOpenAIChatCompletion, getErrorMessage } from '$lib/apis/openai';
	import { processUrl, processWebSearch } from '$lib/apis/retrieval';
	import {
		getAndUpdateUserLocation,
		getUserInfoById,
		getUserSettings,
		updateUserSettings
	} from '$lib/apis/users';
	import {
		generateTitle,
		generateTags,
		generateQueries,
		chatAction,
		generateMoACompletion,
		stopTask,
		stopTasksByChatId,
		getTaskIdsByChatId
	} from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { getSkills } from '$lib/apis/skills';
	import { getImageGenerationEngines, type ImageGenerationEngineOption } from '$lib/apis/images';
	import { resolveImageGenerationEngineId } from '$lib/utils/image-generation';
	import { uploadFile } from '$lib/apis/files';
	import { createOpenAITextStream } from '$lib/apis/streaming';
	import { getFunctions } from '$lib/apis/functions';
	import { initiateOAuthRedirect } from '$lib/apis/configs';

	import Banner from '../common/Banner.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from '$lib/components/chat/Navbar.svelte';
	import ChatTabs from './ChatTabs.svelte';
	import EventConfirmDialog from '../common/ConfirmDialog.svelte';
	import ElicitationDialog from './ElicitationDialog.svelte';
	import DeleteConfirmDialog from '../common/ConfirmDialog.svelte';
	import WebSearchConfirmDialog from '../common/ConfirmDialog.svelte';
	import Placeholder from './Placeholder.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import NotificationToast from '../NotificationToast.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Modal from '../common/Modal.svelte';
	import { isEmbedWindow } from '../common/FullHeightIframe.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Sidebar from '../icons/Sidebar.svelte';
	import Image from '../common/Image.svelte';
	import XMark from '../icons/XMark.svelte';
	import EmbeddedChatHistoryDropdown from './EmbeddedChatHistoryDropdown.svelte';
	import InputVariablesModal from './MessageInput/InputVariablesModal.svelte';

	export let chatIdProp = '';
	export let embedded = false;
	export let embeddedTitle = '';
	export let embeddedChats = [];
	export let embeddedDraftKey = '';
	export let suggestedPrompts = [];
	export let selectedText = '';
	export let notePageId = '';
	export let noteFiles = [];
	export let onBeforeNoteSubmit: (() => Promise<void>) | null = null;
	export let onInsertToNote: ((content: string) => void) | null = null;
	export let onCloseEmbedded: (() => void) | null = null;
	export let onNewEmbeddedChat: (() => void | Promise<void>) | null = null;
	export let onCreateEmbeddedChat: (() => any | Promise<any>) | null = null;
	export let onSelectEmbeddedChat: ((chatId: string) => void | Promise<void>) | null = null;
	export let onDeleteEmbeddedChat: ((chatId: string) => void | Promise<void>) | null = null;
	export let onEmbeddedChatTitle: ((chatId: string, title: string) => void | Promise<void>) | null =
		null;

	let loading = true;
	$: chatContainerId = embedded ? 'note-chat-container' : 'chat-container';
	$: messageInputDropzoneId = embedded ? 'note-chat-input-dropzone' : 'chat-pane';

	const eventTarget = new EventTarget();

	let messageInput: MessageInput | undefined;
	let messagesRef: Messages | undefined;

	let autoScroll = true;
	let lastScrollTop = 0;
	let isNearTop = true;
	let processing = '';
	let messagesContainerElement: HTMLDivElement;

	let navbarElement;

	let showEventConfirmation = false;
	let pendingUrlActions: { urls: string[]; call: boolean } | null = null;
	let eventConfirmationTitle = '';
	let eventConfirmationMessage = '';
	let eventConfirmationInput = false;
	let eventConfirmationInputPlaceholder = '';
	let eventConfirmationInputValue = '';
	let eventConfirmationInputType = '';
	let eventConfirmationInputOptions: ({ label?: string; value: string } | string)[] = [];
	let eventCallback: (value: any) => void = () => {};
	let eventInteractionId: string | null = null;
	type BrowserInteraction = {
		id: string;
		messageId: string;
		type: string;
		data: any;
		callback: (value: any) => void;
	};
	let browserInteractions: BrowserInteraction[] = [];
	let browserApprovalMode: string | null = null;
	$: browserInteraction = browserInteractions[0];

	const resolveBrowserInteraction = (interaction: BrowserInteraction, value: any) => {
		if (!browserInteractions.includes(interaction)) return;
		browserInteractions = browserInteractions.filter((item) => item !== interaction);
		interaction.callback(value);
	};

	const cancelBrowserInteractions = () => {
		browserApprovalMode = null;
		for (const interaction of browserInteractions) {
			resolveBrowserInteraction(interaction, { status: 'cancelled', answers: {} });
		}
	};

	const handleInteractionDisconnect = () => {
		if (isTemporaryChatId($chatId)) {
			temporaryController.disconnect();
			cancelBrowserInteractions();
		} else {
			// Saved interactions are replayed by the server after reconnect. Discard only stale callbacks.
			browserInteractions = [];
			browserApprovalMode = null;
		}
		showEventConfirmation = false;
		eventInteractionId = null;
		eventCallback = () => {};
	};

	let selectedModels = [''];
	let selectedModelIdx = 0;
	$: selectedModelIdx = Math.max(0, selectedModels.length - 1);
	$: backgroundImage = embedded
		? null
		: ($selectedFolder as { meta?: { background_image_url?: string } } | null)?.meta
				?.background_image_url ||
			atSelectedModel?.info?.meta?.background_image_url ||
			$models.find((model) => model.id === selectedModels[selectedModelIdx])?.info?.meta
				?.background_image_url ||
			($settings?.backgroundImageUrl ?? $config?.license_metadata?.background_image_url);

	let atSelectedModel: Model | undefined;
	let selectedModelIds: string[] = [];
	$: if (atSelectedModel !== undefined) {
		selectedModelIds = [atSelectedModel.id];
	} else {
		selectedModelIds = selectedModels;
	}
	let serverContextUsage = null;
	let contextUsage = null;
	let temporaryScope: TemporaryIdentity | null = null;
	let temporarySaveId: string | null = null;
	let temporaryTerminalCredential: TerminalCredential | null = null;
	const temporaryController = new TemporaryContextController({
		actor: () => ({ id: $user?.id ?? '', token: localStorage.token ?? '' }),
		session: () => $socket?.id ?? '',
		cancelInteractions: () => cancelBrowserInteractions(),
		onError: (error) =>
			toast.error($i18n.t('Server connection failed') + ': ' + String(error?.message ?? error)),
		applyEvent: (event, callback) => applyChatEvent(event, callback),
		applyState: (state) => {
			history = reconcileTemporaryHistory(history, state.history);
			temporaryScope = temporaryController.identity;
			taskIds = (state.tasks ?? [])
				.filter((task: any) => task.status !== 'done')
				.map((task: any) => (typeof task === 'string' ? task : (task.id ?? task.task_id)));
			if (!taskIds?.length) taskIds = null;
			if (
				$chatId === temporaryController.chatId &&
				history.currentId &&
				contextUsageModelId &&
				contextCompactionMode === 'v2'
			)
				void refreshContextUsage($chatId, contextUsageModelId, history.currentId);
		}
	});
	const resetTemporaryScope = (keepalive = false) => {
		temporaryController.detach(keepalive);
		temporaryScope = null;
		temporarySaveId = null;
		temporaryTerminalCredential = null;
	};
	const reportTemporaryError = (error: any) => {
		const current = temporaryController.capture();
		toast.error(
			$i18n.t('Server connection failed') +
				': ' +
				String(error?.message ?? error?.detail?.code ?? error),
			{
				...(error?.status === 410 && error?.detail?.rebuild_allowed === true
					? {
							action: {
								label: $i18n.t('Retry'),
								onClick: async () => {
									if (!current()) return;
									try {
										await temporaryController.rebuild();
										temporaryScope = temporaryController.identity;
										const rebuiltId = temporaryController.chatId;
										const branchId = history.currentId;
										if (!rebuiltId || !branchId) return;
										const rebuiltCurrent = temporaryController.capture();
										await chatId.set(rebuiltId);
										if (!rebuiltCurrent()) return;
										await temporaryController.prepare(history, branchId, params, chatFiles);
										temporaryScope = temporaryController.identity;
									} catch (next) {
										reportTemporaryError(next);
									}
								}
							}
						}
					: {})
			}
		);
	};
	const ensureTemporaryScope = async () => {
		const scope = await temporaryController.ensure();
		temporaryScope = scope;
		return `temporary:${scope.id}`;
	};
	const prepareTemporaryCopy = async () => {
		const current = temporaryController.capture();
		const id = $chatId;
		let state = temporaryController.identity ? await temporaryController.reconcile() : null;
		if (!current() || id !== $chatId) throw new Error($i18n.t('Chat not found'));
		if (state?.uncertain) throw new Error($i18n.t('Server connection failed'));
		if (generating || taskIds?.length || hasPendingAssistantLeaf()) {
			if (!(await stopResponse(false)))
				throw new Error($i18n.t('Wait for the current response to finish before compacting.'));
			if (temporaryController.identity) state = await temporaryController.reconcile();
		}
		if (!current() || id !== $chatId) throw new Error($i18n.t('Chat not found'));
		if (state?.uncertain || state?.tasks?.some((task: any) => task.status !== 'done'))
			throw new Error($i18n.t('Wait for the current response to finish before compacting.'));
		return projectRawChat({ history, params, files: chatFiles });
	};

	const temporaryContext = {
		controller: temporaryController,
		get identity() {
			return temporaryController.identity;
		},
		get chatId() {
			return temporaryController.chatId;
		},
		prepareCopy: prepareTemporaryCopy,
		terminalCredential: (token: string, id: string) => {
			const identity = temporaryController.identity;
			if (!identity || temporaryController.chatId !== id) return token;
			if (
				!temporaryTerminalCredential ||
				typeof temporaryTerminalCredential === 'string' ||
				temporaryTerminalCredential.token !== token ||
				temporaryTerminalCredential.temporaryContext.epoch !== identity.epoch ||
				temporaryTerminalCredential.temporaryContext.id !== identity.id
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
	};
	setContext('temporary-context', temporaryContext);
	let temporaryActorId: string | undefined;
	$: if ($user?.id !== temporaryActorId) {
		if (temporaryActorId !== undefined) {
			const wasTemporary = !!temporaryScope || $temporaryChatEnabled;
			resetTemporaryScope();
			if (wasTemporary) {
				history = { messages: {}, currentId: null };
				chatId.set('');
				taskIds = null;
				chatFiles = [];
				files = [];
				prompt = '';
			}
		}
		temporaryActorId = $user?.id;
	}
	$: if (!$temporaryChatEnabled && temporaryScope) resetTemporaryScope();

	const normalizeSelectedModels = (modelIds: string[] = []) => {
		if (
			modelIds.some(
				(id) =>
					id.startsWith('agent:') && decodeURIComponent(id.split(':')[1]) === $selectedTerminalId
			)
		)
			return modelIds;
		const selected = resolveDefaultModelIds(
			$models,
			modelIds,
			$settings?.models,
			$config?.default_models?.split(',')
		);
		return selected.length ? selected : [''];
	};

	$: {
		const modelSearchParam =
			$page.url.searchParams.get('models') || $page.url.searchParams.get('model');

		if (
			chatIdProp === '' &&
			$models.length > 0 &&
			!selectedModels?.some((modelId) => modelId) &&
			!modelSearchParam
		) {
			const fallbackModels = normalizeSelectedModels(selectedModels);
			if (!equal(fallbackModels, selectedModels)) {
				selectedModels = fallbackModels;
				if (!loading && !modelDefaults) setDefaults();
			}
		}
	}

	$: agentModelId = selectedModelIds.find((id) => id.startsWith('agent:'));
	$: offlineAgent = !!agentModelId && !$models.some((model) => model.id === agentModelId);
	$: contextCompactionMode = agentModelId ? 'off' : getCompactionMode($config?.features);
	$: contextCompactionEnabled = contextCompactionMode !== 'off';
	$: contextUsageModelId = atSelectedModel?.id ?? selectedModels.find((id) => id) ?? '';
	let contextUsageRequestKey = '';
	let contextUsageRequestVersion = 0;
	const refreshContextUsage = async (id: string, model: string, branchId: string) => {
		const version = ++contextUsageRequestVersion;
		serverContextUsage = null;
		try {
			const usage =
				temporaryController.chatId === id
					? await temporaryController.usage(
							branchId,
							model,
							$models.find((item) => item.id === model)
						)
					: await getChatContextUsage(
							localStorage.token,
							id,
							model,
							branchId,
							$models.find((item) => item.id === model)
						);
			if (
				version === contextUsageRequestVersion &&
				id === $chatId &&
				branchId === history.currentId
			) {
				serverContextUsage =
					temporaryController.chatId === id && usage ? { ...usage, branch_id: branchId } : usage;
			}
		} catch (error) {
			console.debug('Unable to load context usage', error);
		}
	};
	$: {
		const key = `${$chatId}:${contextUsageModelId}:${contextCompactionMode}:${history.currentId ?? ''}`;
		if (key !== contextUsageRequestKey) {
			contextUsageRequestKey = key;
			if (
				$chatId &&
				history.currentId &&
				contextUsageModelId &&
				contextCompactionEnabled &&
				(!$temporaryChatEnabled || temporaryScope)
			) {
				void refreshContextUsage($chatId, contextUsageModelId, history.currentId);
			} else {
				contextUsageRequestVersion += 1;
				serverContextUsage = null;
			}
		}
	}

	const getContextThreshold = (
		modelId: string | undefined,
		models: Model[],
		params: { compact_token_threshold?: unknown }
	) => {
		const model = models.find((item) => item.id === modelId);
		const threshold = Number(
			model?.info?.params?.compact_token_threshold ?? params?.compact_token_threshold
		);
		return Number.isFinite(threshold) && threshold > 0 ? threshold : null;
	};

	const getContextUsage = (
		chatHistory: typeof history,
		system: string,
		threshold: number | null
	) => {
		if (!chatHistory?.currentId || contextCompactionMode !== 'legacy') {
			return null;
		}

		const messages = createMessagesList(chatHistory, chatHistory.currentId);
		const systemTokens = estimateTokens(system);
		let estimatedTokens = systemTokens;
		let hasUsageCheckpoint = false;
		let summary = '';
		let startIdx = 0;

		for (let idx = 0; idx < messages.length; idx += 1) {
			const value = messages[idx]?.contextSummary ?? messages[idx]?.context_summary;
			if (typeof value === 'string' && value.trim()) {
				summary = value;
				startIdx = idx;
			}
		}

		const activeMessages = messages.slice(startIdx);

		for (let idx = activeMessages.length - 1; idx >= 0; idx -= 1) {
			const usage = activeMessages[idx]?.usage ?? activeMessages[idx]?.info?.usage;
			const usageTokens = getUsageTokenCount(usage);
			if (usageTokens) {
				hasUsageCheckpoint = true;
				estimatedTokens =
					usageTokens +
					estimateUsageGrowth(activeMessages[idx]) +
					estimateMessagesTokens(activeMessages.slice(idx + 1));
				break;
			}
		}

		if (!hasUsageCheckpoint) {
			estimatedTokens += estimateTokens(summary) + estimateMessagesTokens(activeMessages);
		}

		return {
			tokens: estimatedTokens,
			estimated_tokens: estimatedTokens,
			threshold,
			percent: threshold > 0 ? Math.max(0, Math.round((estimatedTokens / threshold) * 100)) : null,
			source: 'estimated'
		};
	};

	$: contextThreshold = contextCompactionEnabled
		? (getContextThreshold(
				atSelectedModel?.id ?? selectedModels.find((id) => id),
				$models,
				params
			) ??
			serverContextUsage?.threshold ??
			null)
		: null;
	$: contextUsage = selectContextUsage(
		contextCompactionMode,
		contextUsageModelId,
		serverContextUsage,
		getContextUsage(history, $settings?.system ?? '', contextThreshold),
		history.currentId
	);
	$: embeddedHeaderTitle = embeddedTitle || $chatTitle || $i18n.t('Chat');

	let selectedToolIds: string[] = [];
	let selectedSkillIds: string[] = [];
	let selectedFilterIds: string[] = [];
	let pendingOAuthTools: { id: string; name: string; serverId: string; authType: string | null }[] =
		[];

	let imageGenerationEnabled = false;
	let imageGenerationEngines: ImageGenerationEngineOption[] | null = null;
	let imageGenerationEnginesLoading = false;
	let imageGenerationEngineId = '';
	let imageEngineAccountId: string | null = null;
	let imageEngineRequestVersion = 0;

	const refreshImageGenerationEngines = async (userId: string | undefined, enabled: boolean) => {
		const version = ++imageEngineRequestVersion;
		if (!userId || imageEngineAccountId !== userId) {
			imageGenerationEngines = null;
			imageEngineAccountId = userId ?? null;
		}
		if (!userId || !enabled) {
			imageGenerationEnginesLoading = false;
			return;
		}
		imageGenerationEnginesLoading = true;
		const token = localStorage.token;
		const isCurrent = () =>
			version === imageEngineRequestVersion &&
			get(user)?.id === userId &&
			localStorage.token === token;
		try {
			const engines = await getImageGenerationEngines(token);
			if (isCurrent()) imageGenerationEngines = engines;
		} catch (error) {
			if (isCurrent()) {
				toast.error($i18n.t('Failed to load image generation engines'));
			}
		} finally {
			if (isCurrent()) imageGenerationEnginesLoading = false;
		}
	};

	$: refreshImageGenerationEngines(
		$user?.id,
		Boolean(
			imageGenerationEnabled &&
			$config?.features?.enable_image_generation &&
			($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
		)
	);
	$: imageGenerationEngineId = resolveImageGenerationEngineId(
		$settings?.imageGenerationEngineId,
		imageGenerationEngines
	);

	let chatSubmissionAlive = true;
	onDestroy(() => {
		chatSubmissionAlive = false;
		settleQueuedSubmission(pendingWebSearchSubmission?.queue ?? null, false);
		imageEngineRequestVersion += 1;
	});

	let webSearchEnabled = false;
	let codeInterpreterEnabled = false;
	let knowledgeEnabled = false;
	let knowledgeWriteEnabled = false;
	let memoryEnabled = false;
	let notesEnabled = false;
	let webSearchActive = false;
	let showWebSearchConfirm = false;
	let pendingWebSearchPrompt: string | null = null;
	type QueuedSubmission = { chatId: string; messageIds: string[] };
	let pendingWebSearchSubmission: {
		files: any[];
		isCurrent: () => boolean;
		queue: QueuedSubmission | null;
	} | null = null;
	const queuedSubmissionGuards = new Map<string, () => boolean>();
	let webSearchConfirmed = false;
	// undefined means no pending toggle; null is a multi-model toggle (never remembered).
	let pendingWebSearchToggleModelId: string | null | undefined;
	let webSearchConfirmTimeout: ReturnType<typeof setTimeout> | undefined;
	let toolFeatureUserId: string | null = $user?.id ?? null;

	const syncToolFeatureAccount = () => {
		const userId = $user?.id ?? null;
		if (userId !== toolFeatureUserId) {
			toolFeatureUserId = userId;
			// Never retain an explicit write opt-in from the previous account.
			knowledgeWriteEnabled = false;
		}
	};
	$: {
		$user?.id;
		syncToolFeatureAccount();
	}

	const getToolFeatureContext = () => {
		syncToolFeatureAccount();
		const modelId = getToolFeatureModelId(selectedModels, atSelectedModel?.id);
		const currentModels = atSelectedModel
			? [atSelectedModel]
			: selectedModels.filter(Boolean).map((id) => $models.find((model) => model.id === id));
		const model = modelId ? (atSelectedModel ?? $models.find((m) => m.id === modelId)) : null;
		const meta = model?.info?.meta as
			| { capabilities?: ToolFeaturePreferences; defaultFeatureIds?: string[] }
			| undefined;
		const isAvailable = (feature: 'web_search' | 'image_generation') =>
			Boolean(
				currentModels.length > 0 &&
				currentModels.every(
					(model) =>
						model &&
						((model.info?.meta?.capabilities as ToolFeaturePreferences | undefined)?.[feature] ??
							true)
				) &&
				$config?.features?.[`enable_${feature}`] &&
				($user?.role === 'admin' || $user?.permissions?.features?.[feature])
			);
		const isDefault = (feature: ToolFeature) =>
			Boolean(meta?.capabilities?.[feature] && meta.defaultFeatureIds?.includes(feature));
		return {
			modelId,
			preferences: $settings?.toolFeaturesByModel,
			defaults: {
				web_search: isDefault('web_search'),
				image_generation: isDefault('image_generation'),
				knowledge: true,
				knowledge_write: false,
				memory: true,
				notes: true
			},
			available: {
				...getContextToolAvailability(currentModels, $config?.features, $user),
				web_search: isAvailable('web_search'),
				image_generation: isAvailable('image_generation')
			}
		};
	};

	const applyToolFeatureDefaults = (overrides?: ToolFeaturePreferences) => {
		const context = getToolFeatureContext();
		const features = resolveToolFeatureState({
			...context,
			// Keep the requested selection while permissions or the master switch mask it.
			available: {
				...context.available,
				knowledge: true,
				knowledge_write: true,
				memory: true,
				notes: true
			},
			overrides
		});
		webSearchEnabled = features.web_search;
		imageGenerationEnabled = features.image_generation;
		knowledgeEnabled = features.knowledge;
		knowledgeWriteEnabled = features.knowledge_write;
		memoryEnabled = features.memory;
		notesEnabled = features.notes;
	};

	const persistChatUserSettings = async (
		fields: (
			| 'toolFeaturesByModel'
			| 'imageGenerationEngineId'
			| 'reasoningEffortByModel'
			| 'params'
		)[],
		errorScope: string,
		notifyOnError = false
	) => {
		if (!$user?.id) return;
		const userId = $user.id;
		const token = localStorage.token;
		await enqueueChatSettingsSave(
			async () => {
				// Read only changed fields, including newer edits to those fields while a save was pending.
				// Queued work must never write one account's preferences into another account.
				if (get(user)?.id !== userId || localStorage.token !== token) return;
				const current = get(settings);
				const ui = Object.fromEntries(fields.map((field) => [field, current[field] ?? null]));
				const saved = await updateUserSettings(token, { ui });
				if (!saved) throw new Error('Failed to update settings');
			},
			(error) => {
				console.error(`[${errorScope}]`, error);
				if (notifyOnError && get(user)?.id === userId) {
					toast.error($i18n.t('Failed to update settings'));
				}
			}
		);
	};

	const persistToolFeaturePreference = async (
		modelId: string | null,
		feature: ToolFeature,
		enabled: boolean
	) => {
		if (!modelId || !$user?.id) return;
		settings.update((current) => ({
			...current,
			toolFeaturesByModel: updateToolFeaturePreference(
				current.toolFeaturesByModel,
				modelId,
				feature,
				enabled
			)
		}));
		await persistChatUserSettings(['toolFeaturesByModel'], 'tool feature settings', true);
	};

	// Before webSearchActive, so it sees the new defaults in the same update
	$: if (!loading && !equal(selectedModelIds, defaultsModelIds)) {
		resetWebSearchConfirmation();
		seedReasoningEffortFromLastUsed(selectedModelIds, false);
		void setDefaults(!!modelDefaults);
	}

	$: {
		const currentModels = atSelectedModel?.id ? [atSelectedModel.id] : selectedModels;
		const allModelsSupportWebSearch =
			currentModels.filter(
				(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.web_search ?? true
			).length === currentModels.length;

		webSearchActive = Boolean(
			$config?.features?.enable_web_search &&
			($user?.role === 'admin' || $user?.permissions?.features?.web_search) &&
			allModelsSupportWebSearch &&
			(webSearchEnabled || ($settings?.webSearch ?? false) === 'always')
		);
	}

	const openWebSearchConfirm = () => {
		clearTimeout(webSearchConfirmTimeout);
		webSearchConfirmTimeout = setTimeout(() => {
			showWebSearchConfirm = true;
		}, 0);
	};

	const handleWebSearchToggle = (enabled: boolean) => {
		const { modelId, available } = getToolFeatureContext();
		if (enabled && !available.web_search) {
			webSearchEnabled = false;
			return;
		}
		if (enabled && $config?.features?.enable_web_search_confirmation && !webSearchConfirmed) {
			webSearchEnabled = false;
			pendingWebSearchPrompt = null;
			pendingWebSearchToggleModelId = modelId;
			openWebSearchConfirm();
			return;
		}
		webSearchEnabled = enabled;
		if (!enabled) resetWebSearchConfirmation();
		return persistToolFeaturePreference(modelId, 'web_search', enabled);
	};

	const handleImageGenerationToggle = (enabled: boolean) => {
		const { modelId, available } = getToolFeatureContext();
		if (enabled && !available.image_generation) {
			imageGenerationEnabled = false;
			return;
		}
		imageGenerationEnabled = enabled;
		return persistToolFeaturePreference(modelId, 'image_generation', enabled);
	};

	const handleImageGenerationEngineChange = async (id: string) => {
		if (!$user?.id || imageGenerationEngines === null) return;
		settings.update((current) => ({
			...current,
			imageGenerationEngineId: resolveImageGenerationEngineId(id, imageGenerationEngines)
		}));
		await persistChatUserSettings(
			['imageGenerationEngineId'],
			'image generation engine settings',
			true
		);
	};

	const handleContextToolToggle = (feature: ContextToolFeature, enabled: boolean) => {
		const { modelId, available } = getToolFeatureContext();
		if (
			!available[feature] ||
			(feature === 'memory' && !($settings?.memory ?? $config?.features?.enable_memories ?? false))
		)
			return;
		if (feature === 'knowledge') knowledgeEnabled = enabled;
		if (feature === 'knowledge_write') knowledgeWriteEnabled = enabled;
		if (feature === 'memory') memoryEnabled = enabled;
		if (feature === 'notes') notesEnabled = enabled;
		return persistToolFeaturePreference(modelId, feature, enabled);
	};

	const resetWebSearchConfirmation = () => {
		settleQueuedSubmission(pendingWebSearchSubmission?.queue ?? null, false);
		clearTimeout(webSearchConfirmTimeout);
		webSearchConfirmed = false;
		pendingWebSearchPrompt = null;
		pendingWebSearchSubmission = null;
		pendingWebSearchToggleModelId = undefined;
		showWebSearchConfirm = false;
	};

	$: if (
		!webSearchActive &&
		pendingWebSearchToggleModelId === undefined &&
		pendingWebSearchPrompt === null
	) {
		resetWebSearchConfirmation();
	}

	let showCommands = false;

	let generating = false;
	let dragged = false;
	let generationController = null;
	const contextCompactions = writable<Record<string, 'in_progress' | { error: string }>>({});
	setContext('contextCompactions', contextCompactions);
	let compactionChatId = '';
	$: if (compactionChatId !== $chatId) {
		contextCompactions.set({});
		compactionChatId = $chatId;
	}

	let chat: any = null;
	let tags = [];

	// Non-owners may reply when the sharing mode allows it, but cannot save chat edits.
	$: isShared = chat != null && chat.user_id !== $user?.id;
	$: if (isShared) {
		if ($showCallOverlay) showCallOverlay.set(false);
		if (bridge?.connected || bridge?.connecting) bridge.end();
	}
	$: canClone = isShared && ($user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true));
	$: sideChatAllowed =
		$user?.role === 'admin' ||
		($user?.role === 'user' && ($user.permissions?.chat?.side_chat ?? false));
	let cloning = false;

	const cloneSharedChat = async () => {
		if (!canClone || cloning) return;
		cloning = true;
		try {
			const [copy] = await importChats(localStorage.token, [
				{
					chat: {
						...projectRawChat(chat.chat),
						title: $i18n.t('Clone of {{TITLE}}', { TITLE: chat.title }),
						originalChatId: chat.id,
						branchPointMessageId: history.currentId
					}
				}
			]);
			if (copy) {
				await goto(`/c/${copy.id}`);
				await refreshChatList(localStorage.token);
			}
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			cloning = false;
		}
	};

	let chatOwner: import('$lib/stores').ChatUserIdentity | null = null;

	const resolveChatOwner = async (userId) => {
		if (chatOwner?.id === userId) {
			return;
		}

		chatOwner = await getUserInfoById(localStorage.token, userId).catch((error) => {
			console.error(error);
			return null;
		});
	};

	$: if (isShared && chat?.user_id) {
		void resolveChatOwner(chat.user_id);
	} else {
		chatOwner = null;
	}

	let chatTasks = [];
	let temporaryConnectionSessionId: string | undefined;

	let history: { currentId: string | null; messages: Record<string, any> } = {
		messages: {},
		currentId: null
	};

	let taskIds: string[] | null = null;
	const stoppingChats = new Map<string, number>();
	let liveStatus = '';

	$: if (loading || !history.currentId) {
		liveStatus = '';
	} else if (
		Object.values(history.messages).some(
			(message) => message.role === 'assistant' && message.done === false
		)
	) {
		liveStatus = $i18n.t('Generating...');
	} else if (liveStatus) {
		liveStatus = $i18n.t('Response complete');
	}

	// Chat Input
	let prompt = '';
	let chatFiles: any[] = [];
	let files: any[] = [];
	let params: Record<string, any> = {};
	let reasoningEffortByModel: Record<string, string> = {};
	let terminalContext: TerminalContext | null = null;
	const captureSubmissionGuard = () => {
		const id = $chatId,
			sourceHistory = history,
			actor = $user?.id,
			token = localStorage.token,
			models = JSON.stringify(selectedModelIds);
		return (expectedChatId = id) =>
			chatSubmissionAlive &&
			$chatId === expectedChatId &&
			history === sourceHistory &&
			$user?.id === actor &&
			localStorage.token === token &&
			JSON.stringify(selectedModelIds) === models;
	};
	const askFile = async (instruction: string, file: FilesystemFile) => {
		if (fullWidthTabs) {
			terminalTabs?.focusMainChat();
			await tick();
			if (!$showCallOverlay) messageInput?.focus({ preventScroll: true });
		}
		await submitHandler(instruction, { attachments: [structuredClone(file)] });
	};
	let chatVariables = {};
	let showChatVariablesModal = false;
	let localParamsUserId: string | null = null;
	$: if ($user?.id && $user.id !== localParamsUserId) {
		if (localParamsUserId !== null) {
			params = {};
			reasoningEffortByModel = {};
			chatVariables = {};
		}
		localParamsUserId = $user.id;
	}
	let loadedChatIdProp = '';
	let currentDraftKey = '';

	// Chat parameters own the approval mode; chats without an override use the user default.
	$: toolApprovalMode = resolveToolApprovalMode(
		params?.tool_approval_mode ?? $settings?.params?.tool_approval_mode,
		$config?.features?.enable_tool_permissions_auto ?? false
	);

	$: controlSelections = controlsForRequest(
		params,
		$settings?.params ?? {},
		$models,
		reasoningEffortByModel,
		$settings?.reasoningEffortByModel
	);
	$: controlAdvancedParams = mergeAdvancedParams($settings?.params ?? {}, params);

	const setModelControl = async (modelId: string, controlId: string, value: string) => {
		const actorId = $user?.id;
		const actorToken = localStorage.token;
		const targetChatId = $chatId;
		const currentChat = migrateControlSelections(
			params?.model_controls,
			reasoningEffortByModel,
			$models
		);
		const choices = { ...(controlSelections[modelId] ?? {}) };
		if (value) choices[controlId] = value;
		else delete choices[controlId];
		params = { ...params, model_controls: { ...currentChat, [modelId]: choices } };
		const remembered = migrateControlSelections(
			$settings?.params?.model_controls,
			$settings?.reasoningEffortByModel,
			$models
		);
		// Consume old memory, including reset, before saving either format.
		const legacy = { ...($settings?.reasoningEffortByModel ?? {}) };
		delete legacy[modelId];
		const chatLegacy = { ...reasoningEffortByModel };
		delete chatLegacy[modelId];
		reasoningEffortByModel = chatLegacy;
		settings.set({
			...$settings,
			reasoningEffortByModel: legacy,
			params: { ...$settings?.params, model_controls: { ...remembered, [modelId]: choices } }
		});
		await persistChatUserSettings(
			['params', 'reasoningEffortByModel'],
			'model control settings',
			true
		);
		if (get(user)?.id === actorId && localStorage.token === actorToken && $chatId === targetChatId)
			await saveParam('model_controls', structuredClone(params.model_controls));
	};

	const getAvailableReasoningEffortByModel = () =>
		Object.fromEntries(
			($models ?? []).map((model) => [model.id, getAvailableReasoningEffort(model)])
		);

	const getReasoningEffortByModelForSave = () =>
		sanitizeReasoningEffortByModel(
			Object.fromEntries(
				Object.entries(reasoningEffortByModel).filter(
					([id]) => !supportsModelControls($models.find((model) => model.id === id))
				)
			),
			getAvailableReasoningEffortByModel()
		);

	const persistReasoningEffortByModel = async () => {
		if (isShared || !$chatId || $temporaryChatEnabled) return;
		await updateChatById(localStorage.token, $chatId, {
			reasoning_effort_by_model: getReasoningEffortByModelForSave()
		}).catch((err) => {
			console.error('[reasoning effort autosave]', err);
		});
	};

	const persistLastUsedReasoningEffort = async (modelId: string, value: string) => {
		const next = { ...($settings?.reasoningEffortByModel ?? {}) };
		if (value) {
			next[modelId] = value;
		} else {
			delete next[modelId];
		}
		settings.set({
			...$settings,
			reasoningEffortByModel: next
		});
		await persistChatUserSettings(['reasoningEffortByModel'], 'reasoning effort settings');
	};

	const seedReasoningEffortFromLastUsed = (modelIds = selectedModelIds, persist = true) => {
		const legacyModelIds = modelIds.filter(
			(id) => !supportsModelControls($models.find((model) => model.id === id))
		);
		const next = fillReasoningEffortFromLastUsed(
			reasoningEffortByModel,
			$settings?.reasoningEffortByModel,
			legacyModelIds,
			getAvailableReasoningEffortByModel()
		);
		if (equal(next, reasoningEffortByModel)) {
			return;
		}
		reasoningEffortByModel = next;
		if (persist) {
			persistReasoningEffortByModel();
		}
	};

	const setReasoningEffortOverride = (modelId: string, value: string) => {
		if (value) {
			reasoningEffortByModel = { ...reasoningEffortByModel, [modelId]: value };
		} else {
			const next = { ...reasoningEffortByModel };
			delete next[modelId];
			reasoningEffortByModel = next;
		}
		persistReasoningEffortByModel();
		persistLastUsedReasoningEffort(modelId, value);
	};

	const handleToolApprovalModeChange = async (mode: string) => {
		const tool_approval_mode = resolveToolApprovalMode(
			mode,
			$config?.features?.enable_tool_permissions_auto ?? false
		);
		browserApprovalMode = tool_approval_mode;
		params = {
			...params,
			tool_approval_mode
		};

		settings.set({
			...$settings,
			params: {
				...($settings?.params ?? {}),
				tool_approval_mode
			}
		});
		await saveParam('tool_approval_mode', tool_approval_mode);
		await persistChatUserSettings(['params'], 'tool permissions settings');

		if (tool_approval_mode === 'full') {
			if (isTemporaryChatId($chatId)) {
				if (browserApprovalMode !== 'full') return;
				for (const interaction of browserInteractions) {
					if (interaction.data?.tool_call) resolveBrowserInteraction(interaction, true);
				}
				return;
			}
			const messages = [...Object.values(history?.messages ?? {})].reverse() as any[];
			for (const message of messages) {
				if ((message.user_id ?? chat?.user_id ?? $user?.id) !== $user?.id) continue;
				const output = (Array.isArray(message?.output) ? message.output : []) as any[];
				const resultCallIds = new Set(
					output
						.filter((item: any) => item?.type === 'function_call_output' && item?.call_id)
						.map((item: any) => item.call_id)
				);
				const pendingCall = output.find((item: any) => {
					const callId = item?.call_id ?? item?.id;
					return (
						item?.type === 'function_call' &&
						item?.name !== 'ask_user' &&
						(item?.status === 'pending' || item?.status === 'requires_approval') &&
						callId &&
						!resultCallIds.has(callId)
					);
				});
				const callId = pendingCall?.call_id ?? pendingCall?.id;
				if (!message?.id || !callId) {
					continue;
				}

				const res = await resolveChatMessageToolCall(
					localStorage.token,
					$chatId,
					message.id,
					callId,
					'approve'
				).catch(async (error) => {
					toast.error(`${error}`);
					await loadChat();
					return null;
				});
				if (res) onToolCallResolved(res);
				break;
			}
		}
	};

	const findPendingAskUser = (chatHistory) => {
		if (!chatHistory?.messages) {
			return null;
		}
		const messages = chatHistory.currentId
			? createMessagesList(chatHistory, chatHistory.currentId)
			: Object.values(chatHistory.messages);
		for (const message of [...messages].reverse()) {
			if ((message.user_id ?? chat?.user_id ?? $user?.id) !== $user?.id) continue;
			const pending = getPendingAskUserFromMessage(message);
			if (pending) return pending;
		}
		return null;
	};

	const messageHasPendingAskUser = (message) => {
		return !!getPendingAskUserFromMessage(message);
	};

	const answerPendingAskUser = async (messageId, callId, answers, timedOut = false) => {
		if (!$chatId || !messageId || !callId) {
			return;
		}

		const res = await resolveChatMessageToolCall(
			localStorage.token,
			$chatId,
			messageId,
			callId,
			'answer',
			{
				answers,
				timed_out: timedOut
			}
		).catch(async (error) => {
			toast.error(`${error}`);
			await loadChat();
		});
		onToolCallResolved(res);
	};

	const rejectPendingAskUser = async (messageId, callId) => {
		if (!$chatId || !messageId || !callId) {
			return;
		}

		const res = await resolveChatMessageToolCall(
			localStorage.token,
			$chatId,
			messageId,
			callId,
			'reject'
		).catch(async (error) => {
			toast.error(`${error}`);
			await loadChat();
		});
		onToolCallResolved(res);
	};

	$: pendingAskUser = $temporaryChatEnabled ? null : findPendingAskUser(history);
	$: savedAskUserPrompt = pendingAskUser
		? {
				show: true,
				questions: Array.isArray(pendingAskUser.args?.questions)
					? pendingAskUser.args.questions
					: [],
				allowOther: pendingAskUser.args?.allow_other !== false,
				timeoutMs: null,
				onConfirm: (value) => {
					void answerPendingAskUser(
						pendingAskUser.message.id,
						pendingAskUser.call.call_id || pendingAskUser.call.id,
						value?.answers ?? {},
						false
					);
				},
				onCancel: () => {
					void rejectPendingAskUser(
						pendingAskUser.message.id,
						pendingAskUser.call.call_id || pendingAskUser.call.id
					);
				}
			}
		: null;

	$: socketAskUserPrompt = ((interaction: BrowserInteraction | undefined) => ({
		id: interaction?.id,
		show: interaction?.type === 'request:user_input',
		questions: interaction?.data?.questions ?? [],
		allowOther: interaction?.data?.allow_other ?? true,
		timeoutMs: interaction?.data?.timeout_ms ?? null,
		onConfirm: (value: any) => interaction && resolveBrowserInteraction(interaction, value),
		onCancel: () =>
			interaction && resolveBrowserInteraction(interaction, { status: 'cancelled', answers: {} })
	}))(browserInteraction);

	const mergeChatVariableSchemas = (modelIds: string[] = [], availableModels: Model[] = []) => {
		const byKey: Record<string, any> = {};
		const conflicts: any[] = [];

		for (const modelId of modelIds.filter(Boolean)) {
			const fields =
				availableModels.find((model) => model.id === modelId)?.info?.meta?.chat_variables_schema
					?.fields ?? [];
			for (const rawField of fields) {
				const field = {
					...rawField,
					type: rawField?.type ?? 'text',
					required: Boolean(rawField?.required)
				};
				if (!field?.key) continue;
				const { required, ...shape } = field;

				const existing = byKey[field.key];
				if (!existing) {
					byKey[field.key] = {
						field,
						modelIds: [modelId],
						shape
					};
					continue;
				}

				if (!equal(existing.shape, shape)) {
					conflicts.push({
						key: field.key,
						modelIds: [...existing.modelIds, modelId]
					});
					continue;
				}

				existing.field = {
					...existing.field,
					required: existing.field.required || field.required
				};
				existing.modelIds.push(modelId);
			}
		}

		return {
			fields: Object.values(byKey).map((item: any) => item.field),
			conflicts
		};
	};

	const hasValue = (value) => value !== undefined && value !== null && value !== '';

	const getChatVariablesForm = (
		modelIds: string[] = [],
		values: Record<string, unknown> = {},
		availableModels: Model[] = []
	) => {
		const { fields, conflicts } = mergeChatVariableSchemas(modelIds, availableModels);
		const empty =
			fields.length > 0 &&
			fields.every((field) => !hasValue(values?.[field.key]) && !hasValue(field.default));
		const missing = fields.some(
			(field) => field.required && !hasValue(values?.[field.key]) && !hasValue(field.default)
		);
		const variables = fields.reduce(
			(acc, field) => {
				const { key, ...inputField } = field;
				acc[key] = {
					...inputField,
					default: hasValue(values?.[key]) ? values[key] : inputField.default
				};
				return acc;
			},
			{} as Record<string, any>
		);

		return { conflicts, empty, missing, variables };
	};

	const saveChatVariables = async (values) => {
		chatVariables = { ...chatVariables, ...values };
		if (isShared) return;

		if ($chatId && !$temporaryChatEnabled && !isTemporaryChatId($chatId)) {
			const res = await updateChatById(localStorage.token, $chatId, {}, chatVariables).catch(
				(err) => {
					console.error('[chat variables save]', err);
					toast.error($i18n.t('Failed to save chat variables'));
					return null;
				}
			);
			if (res) chat = res;
		}
	};

	const onToolCallResolved = (res) => {
		const newTaskIds = res?.task_ids ?? (res?.task_id ? [res.task_id] : []);
		if (newTaskIds.length > 0) {
			taskIds = [...(taskIds ?? []), ...newTaskIds];
		}
	};

	const mergeFiles = (current, incoming) => {
		const seen = new Set();
		return [...(incoming ?? []), ...(current ?? [])].filter((file) => {
			const key = `${file?.type ?? ''}:${file?.id ?? file?.url ?? file?.name ?? ''}`;
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	};

	const restoreChatInput = async (storageChatInput: string | null) => {
		if (!storageChatInput || $temporaryChatEnabled) {
			return false;
		}

		try {
			const input = JSON.parse(storageChatInput);
			prompt = input.prompt ?? '';
			messageInput?.setText(prompt);
			files = input.files ?? [];
			selectedToolIds = input.selectedToolIds ?? [];
			selectedSkillIds = input.selectedSkillIds ?? [];
			selectedFilterIds = input.selectedFilterIds ?? [];
			// Existing chats keep their own draft; a new chat starts from the model preference.
			applyToolFeatureDefaults(
				chatIdProp
					? {
							web_search: input.webSearchEnabled,
							image_generation: input.imageGenerationEnabled,
							knowledge: input.knowledgeEnabled,
							knowledge_write: input.knowledgeWriteEnabled,
							memory: input.memoryEnabled,
							notes: input.notesEnabled
						}
					: undefined
			);
			codeInterpreterEnabled = input.codeInterpreterEnabled ?? false;
			// Ignore approval modes in older drafts. Restoring input must not overwrite chat
			// parameters or invoke the change handler, which can save settings and approve tools.
			return true;
		} catch (e) {
			return false;
		}
	};

	const withSelectedText = (text: string) => {
		if (!embedded) return text;
		return (
			text +
			(notePageId ? `\n\nActive note page_id: ${notePageId}` : '') +
			(selectedText?.trim()
				? `\n\nSelected note text for replace_note_content operations:\n${selectedText.trim()}`
				: '')
		);
	};
	const noteChatDebug = (message: string, data: Record<string, unknown> = {}) => {
		if (!embedded) return;
		console.info('[note-chat]', message, {
			chatIdProp,
			activeChatId: $chatId,
			loading,
			...data
		});
	};

	$: if (chatIdProp && chatIdProp !== loadedChatIdProp) {
		noteChatDebug('chatIdProp changed; loading linked chat', {
			previousChatIdProp: loadedChatIdProp
		});
		loadedChatIdProp = chatIdProp;
		navigateHandler();
	}

	$: if (embedded && embeddedDraftKey && embeddedDraftKey !== currentDraftKey) {
		noteChatDebug('embedded draft requested', { embeddedDraftKey });
		currentDraftKey = embeddedDraftKey;
		initEmbeddedDraft();
	}

	let saveControlsTimer: ReturnType<typeof setTimeout> | undefined;
	$: if (!loading && !$temporaryChatEnabled && $chatId && chatFiles) {
		clearTimeout(saveControlsTimer);
		saveControlsTimer = setTimeout(saveControls, 400);
	}

	let chatLoadController = new AbortController();
	const startChatLoad = () => {
		chatLoadController.abort();
		chatLoadController = new AbortController();
		return chatLoadController.signal;
	};
	onDestroy(() => chatLoadController.abort());
	const navigateHandler = async () => {
		loading = true;
		terminalTabs?.prepareChatSwitch();
		const signal = startChatLoad();
		modelDefaults = null;
		cancelBrowserInteractions();
		bridge?.end();
		noteChatDebug('navigateHandler start');
		resetWebSearchConfirmation();
		// Mark the outgoing chat as read before loading the new one.
		// $chatId still holds the previous chat here — loadChat() updates it.
		if ($chatId && $chatId !== chatIdProp && !$temporaryChatEnabled) {
			noteChatDebug('marking outgoing chat read', { outgoingChatId: $chatId });
			updateLastReadAt($chatId);
		}

		clearTimeout(saveControlsTimer);
		await saveControls();
		if (signal.aborted) return;
		loading = true;

		prompt = '';
		messageInput?.setText('');

		files = [];
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		knowledgeEnabled = false;
		knowledgeWriteEnabled = false;
		memoryEnabled = false;
		notesEnabled = false;
		codeInterpreterEnabled = false;

		if (chatIdProp) {
			await loadChat(signal);
		} else if (!embedded) {
			await goto('/');
		} else {
			loading = false;
		}
	};

	const initEmbeddedDraft = async () => {
		resetTemporaryScope();
		resetWebSearchConfirmation();
		startChatLoad();
		modelDefaults = null;
		terminalContext = null;
		clearTimeout(saveControlsTimer);
		await saveControls();

		if ($chatId && !$temporaryChatEnabled) {
			updateLastReadAt($chatId);
		}

		loading = true;
		loadedChatIdProp = '';
		chat = null;
		tags = [];
		taskIds = null;
		chatTasks = [];
		serverContextUsage = null;
		temporaryConnectionSessionId = undefined;
		history = {
			messages: {},
			currentId: null
		};
		params = {};
		reasoningEffortByModel = {};
		seedReasoningEffortFromLastUsed(selectedModelIds, false);
		chatVariables = {};
		chatFiles = [];
		files = [];
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		knowledgeEnabled = false;
		knowledgeWriteEnabled = false;
		memoryEnabled = false;
		notesEnabled = false;
		codeInterpreterEnabled = false;
		prompt = '';
		messageInput?.setText('');
		await chatId.set('');
		await chatTitle.set('');

		await setDefaults();
		loading = false;
		await tick();
		messageInput?.focus({ preventScroll: true });
	};

	const onSelect = async (e) => {
		const { type, data } = e;

		if (type === 'prompt') {
			// Handle prompt selection
			messageInput?.setTextHandler(data, async () => {
				if (!($settings?.insertSuggestionPrompt ?? false)) {
					await tick();
					submitHandler(prompt);
				}
			});
		}
	};

	$: if (selectedModels && chatIdProp !== '') {
		saveSessionSelectedModels();
	}

	const saveSessionSelectedModels = () => {
		const selectedModelsString = JSON.stringify(selectedModels);
		if (
			selectedModels.length === 0 ||
			(selectedModels.length === 1 && selectedModels[0] === '') ||
			sessionStorage.selectedModels === selectedModelsString
		) {
			return;
		}
		sessionStorage.selectedModels = selectedModelsString;
		console.log('saveSessionSelectedModels', selectedModels, sessionStorage.selectedModels);
	};

	const continueOAuthRedirect = async () => {
		if (pendingOAuthTools.length === 0) {
			sessionStorage.removeItem('oauthRedirectInProgressToolId');
			return;
		}

		if (chatIdProp) {
			return;
		}

		const nextTool = pendingOAuthTools[0];
		if (sessionStorage.getItem('oauthRedirectInProgressToolId') === nextTool.id) {
			sessionStorage.removeItem('oauthRedirectInProgressToolId');
			return;
		}

		await oauthRedirectHandler(nextTool);
	};

	const resetInput = async () => {
		resetWebSearchConfirmation();
		// Let the $: update finish first, or webSearchActive misses the default-feature writes
		await tick();
		selectedToolIds = [];
		selectedSkillIds = [];
		selectedFilterIds = [];
		pendingOAuthTools = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		knowledgeEnabled = false;
		knowledgeWriteEnabled = false;
		memoryEnabled = false;
		notesEnabled = false;
		codeInterpreterEnabled = false;

		if (selectedModelIds.filter((id) => id).length > 0) {
			await setDefaults();
		}
	};

	/** Check whether a terminal ID references an available system or direct terminal. */
	const isTerminalAvailable = (tid: string): boolean => {
		return (
			$config?.features?.enable_tool_servers === true &&
			(!tid.startsWith('tether:') || $config?.features?.enable_tether === true) &&
			(($terminalServers ?? []).some((t) => t.id && t.id === tid) ||
				($config?.features?.enable_direct_integrations === true &&
					($settings?.terminalServers ?? []).some((s) => s.url === tid && s.enabled !== false)))
		);
	};

	$: if (
		$terminalServers !== null &&
		$selectedTerminalId &&
		!isTerminalAvailable($selectedTerminalId)
	) {
		selectedTerminalId.set(null);
	}

	$: if (
		$temporaryChatEnabled &&
		contextCompactionMode !== 'v2' &&
		($terminalServers ?? []).some(
			(t) => t.id && t.id === $selectedTerminalId && t.contexts?.chat?.context_id === 'chat_id'
		)
	) {
		selectedTerminalId.set(null);
	}

	let lastTerminalSkillSelector: string | null = null;
	let agentDiscovery = 0;
	const refreshAgentModels = async () => {
		const generation = ++agentDiscovery;
		const token = localStorage.token;
		const actor = $user?.id;
		models.update((available) =>
			available.filter(
				(model) => model.owned_by !== 'terminal' || model.agent.terminal_id === $selectedTerminalId
			)
		);
		try {
			const available = await getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections ? ($settings?.directConnections ?? null) : null
			);
			if (generation === agentDiscovery && localStorage.token === token && $user?.id === actor)
				models.set(available);
		} catch (error) {
			console.error('Agent model discovery failed', error);
		}
	};
	$: if ($selectedTerminalId !== lastTerminalSkillSelector) {
		if (terminalContext?.terminalId !== $selectedTerminalId) terminalContext = null;
		selectedSkillIds = selectedSkillIds.filter((id) => !id.startsWith('terminal:'));
		terminalSkills.set([]);
		lastTerminalSkillSelector = $selectedTerminalId;
		void refreshAgentModels();
	}

	const applyFolderTerminal = (folder: any) => {
		if (!folder) return;
		const terminalId = folder.data?.terminal_id || null;
		selectedTerminalId.set(terminalId);
		terminalContext = terminalId
			? { terminalId, currentDirectory: folder.data?.working_directory ?? '' }
			: null;
	};

	let defaultsModelIds: string[] = [];
	let modelDefaults: Record<string, any> | null = null;
	let settingDefaults: Promise<void> | null = null;

	const setDefaults = async (preserveSelections = false) => {
		const modelIds = atSelectedModel ? [atSelectedModel.id] : [...selectedModels];
		const id = $chatId;
		const actor = $user?.id;
		const token = localStorage.token;
		const changedModel = !equal(modelIds, defaultsModelIds);
		if (changedModel || !preserveSelections) applyToolFeatureDefaults();
		const initialWebSearch = webSearchEnabled;
		const initialImageGeneration = imageGenerationEnabled;
		while (settingDefaults) await settingDefaults;
		let resolveDefaults = () => {};
		settingDefaults = new Promise<void>((resolve) => (resolveDefaults = resolve));

		try {
			const [loadedTools, loadedFunctions, loadedSkills] = await Promise.all([
				$tools ?? getTools(token),
				$functions ?? getFunctions(token),
				$skills ?? getSkills(token)
			]);
			if (actor !== $user?.id || token !== localStorage.token) return;
			if (!$tools) tools.set(loadedTools);
			if (!$functions) functions.set(loadedFunctions);
			if (!$skills) skills.set(loadedSkills);
			if ($tools === null || $functions === null || $skills === null) return;
			if (
				id !== $chatId ||
				!equal(modelIds, atSelectedModel ? [atSelectedModel.id] : selectedModels)
			)
				return;

			const currentModels: any[] = modelIds.map((id) => $models.find((model) => model.id === id));
			if (!currentModels.length || currentModels.some((model) => !model)) return;
			const supports = (feature: string) =>
				currentModels.every((model) => model.info?.meta?.capabilities?.[feature] ?? true);
			const featureAvailable = (feature: 'web_search' | 'image_generation' | 'code_interpreter') =>
				supports(feature) &&
				$config?.features?.[`enable_${feature}`] &&
				($user?.role === 'admin' || $user?.permissions?.features?.[feature]);
			const defaultFeature = (feature: 'web_search' | 'image_generation' | 'code_interpreter') =>
				featureAvailable(feature) &&
				currentModels.every((model) => model.info?.meta?.defaultFeatureIds?.includes(feature));
			const folderTerminalId = !history.currentId ? $selectedFolder?.data?.terminal_id : null;
			const terminalId =
				!history.currentId && $selectedFolder
					? folderTerminalId
					: currentModels[0]?.info?.meta?.terminalId;
			const defaults: Record<string, any> = {
				selectedToolIds: [
					...new Set(
						currentModels.flatMap(
							(model) =>
								model.info?.meta?.toolIds?.filter(
									(id: string) =>
										selectedToolIds.includes(id) || ($tools as any[]).some((tool) => tool.id === id)
								) ??
								($settings as any)?.tools ??
								[]
						)
					)
				],
				selectedSkillIds: [
					...new Set(currentModels.flatMap((model) => model.info?.meta?.skillIds ?? []))
				].filter(
					(id) =>
						selectedSkillIds.includes(id) ||
						$skills.some((skill) => skill.id === id && skill.is_active)
				),
				selectedFilterIds: [
					...new Set(currentModels.flatMap((model) => model.info?.meta?.defaultFilterIds ?? []))
				],
				webSearchEnabled: !!defaultFeature('web_search'),
				imageGenerationEnabled: !!defaultFeature('image_generation'),
				codeInterpreterEnabled: !!defaultFeature('code_interpreter'),
				terminalId:
					terminalId &&
					isTerminalAvailable(terminalId) &&
					(folderTerminalId ||
						currentModels.every((model) => model.info?.meta?.terminalId === terminalId))
						? terminalId
						: $selectedTerminalId
			};

			if (preserveSelections) {
				const current: Record<string, any> = {
					selectedToolIds: [...selectedToolIds, ...pendingOAuthTools.map((tool) => tool.id)],
					selectedSkillIds,
					selectedFilterIds,
					webSearchEnabled,
					imageGenerationEnabled,
					codeInterpreterEnabled,
					terminalId: $selectedTerminalId
				};
				if (!modelDefaults) {
					modelDefaults = structuredClone(defaults);
					defaultsModelIds = modelIds;
				}
				for (const key of Object.keys(defaults)) {
					const previous = modelDefaults[key];
					if (Array.isArray(previous)) {
						const added = current[key].filter((id: string) => !previous.includes(id));
						const removed = previous.filter((id: string) => !current[key].includes(id));
						// Keep deselected defaults in the baseline so later presets cannot re-enable them.
						modelDefaults[key] = [
							...new Set([...removed, ...defaults[key].filter((id: string) => !added.includes(id))])
						];
						defaults[key] = [
							...new Set([...defaults[key].filter((id: string) => !removed.includes(id)), ...added])
						];
					} else if (
						(key !== 'terminalId' || equal(modelIds, defaultsModelIds)) &&
						current[key] !== previous
					) {
						defaults[key] = current[key];
					} else {
						modelDefaults[key] = defaults[key];
					}
				}
			} else {
				modelDefaults = structuredClone(defaults);
			}

			defaults.selectedFilterIds = defaults.selectedFilterIds.filter((id: string) =>
				currentModels.every((model) =>
					model.filters?.some((filter: { id: string }) => filter.id === id)
				)
			);
			defaults.webSearchEnabled &&= !!featureAvailable('web_search');
			defaults.imageGenerationEnabled &&= !!featureAvailable('image_generation');
			defaults.codeInterpreterEnabled &&= !!featureAvailable('code_interpreter');
			if (!currentModels.some((model) => model.info?.meta?.capabilities?.terminal ?? true)) {
				defaults.terminalId = null;
			}
			if (defaults.terminalId) defaults.codeInterpreterEnabled = false;
			defaultsModelIds = modelIds;
			({ selectedToolIds, selectedSkillIds, selectedFilterIds, codeInterpreterEnabled } = defaults);
			selectedTerminalId.set(defaults.terminalId);
			if (modelIds.length > 1 && (changedModel || !preserveSelections)) {
				if (webSearchEnabled === initialWebSearch) webSearchEnabled = defaults.webSearchEnabled;
				if (imageGenerationEnabled === initialImageGeneration)
					imageGenerationEnabled = defaults.imageGenerationEnabled;
			}

			pendingOAuthTools = [];
			selectedToolIds = selectedToolIds.filter((id) => {
				const tool = ($tools as any[]).find((tool) => tool.id === id);
				if (tool?.authenticated !== false) return true;
				const parts = id.split(':');
				pendingOAuthTools.push({
					id,
					name: tool.name ?? id,
					serverId: parts.at(-1) ?? id,
					authType: parts.length > 1 ? (parts[0] === 'server' ? parts[1] : parts[0]) : null
				});
				return false;
			});
			await continueOAuthRedirect();
		} finally {
			settingDefaults = null;
			resolveDefaults();
		}
	};

	let revealedSearchMessage = '';
	$: if (
		!embedded &&
		!loading &&
		messagesRef &&
		chat &&
		chat.id === $page.params.id &&
		(!fullWidthTabs || mainChatActive)
	) {
		const target = $page.url.searchParams.get('message');
		const key = `${chat.id}:${target}`;
		if (target && history.messages[target] && key !== revealedSearchMessage) {
			revealedSearchMessage = key;
			autoScroll = false;
			history.currentId = getDeepestChildId(history, target);
			tick().then(() => messagesRef?.scrollToMessage(target));
		} else if (!target) revealedSearchMessage = '';
	}

	const showMessage = async (message, scroll = true, save = true) => {
		const _chatId = JSON.parse(JSON.stringify($chatId));
		let _messageId = JSON.parse(JSON.stringify(message.id));

		history.currentId = getDeepestChildId(history, _messageId);

		await tick();

		if (($settings?.scrollOnBranchChange ?? true) && scroll) {
			const messageElement = document.getElementById(`message-${message.id}`);
			if (messageElement) {
				messageElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
			}
		}

		await tick();
		await tick();
		await tick();

		if (save) {
			await saveChatHandler(_chatId, history);
		}
	};

	const updateLastReadAt = (id) => {
		$socket?.emit('events:chat', {
			chat_id: id,
			data: { type: 'last_read_at' }
		});
	};

	const terminalEventHandler = (type: string, data: any, message: any) => {
		const attachment = history.messages[message?.parentId]?.files?.find(
			(file: any) => file.type === 'filesystem'
		);
		if (attachment) {
			if (attachment.terminal_id && attachment.terminal_id !== $selectedTerminalId) return;
			if (type === 'terminal:display_file' && terminalContext?.file?.path === data.path)
				showFileNavDir.set(data.path);
			return;
		}
		if (type === 'terminal:display_file') {
			if (!data?.path) return;
			if ($settings?.terminalFileDisplay === 'inline') return;
			displayFileHandler(data.path, { showTabs, showFileNavPath }, { page: data?.page });
		} else if (type === 'terminal:write_file' || type === 'terminal:replace_file_content') {
			if (!data?.path) return;
			showFileNavDir.set(data.path);
		} else if (type === 'terminal:run_command' && !terminalContext?.file) {
			showFileNavDir.set('/');
		}
	};

	const clearContextCompaction = (messageId: string) => {
		const next = { ...$contextCompactions };
		delete next[messageId];
		contextCompactions.set(next);
	};

	const handleContextCompactionStatus = (status, messageId: string) => {
		if (status?.action !== 'context_compaction') return;
		if (status.done && !status.error) {
			clearContextCompaction(messageId);
		} else {
			$contextCompactions = {
				...$contextCompactions,
				[messageId]: status.error ? { error: $i18n.t('Context compaction failed') } : 'in_progress'
			};
		}
	};

	let joinedChatId = '';
	let liveParticipants: { id: string; name: string }[] = [];
	const clearPresence = () => (liveParticipants = []);
	let chatRefresh: { id: string; promise: Promise<void> } | null = null;
	let typingUsers: { id: string; name: string }[] = [];
	let typingUsersTimeout: Record<string, ReturnType<typeof setTimeout>> = {};
	let lastTypingEmit = 0;

	const emitTyping = (typing: boolean) => {
		if (!$socket?.connected || !joinedChatId) return;
		if (typing && (joinedChatId !== $chatId || (isShared && chat?.chat?.share_mode !== 'continue')))
			return;
		if (typing ? Date.now() - lastTypingEmit < 2000 : !lastTypingEmit) return;
		lastTypingEmit = typing ? Date.now() : 0;
		$socket.emit('events:chat', {
			chat_id: joinedChatId,
			data: { type: 'typing', data: { typing } }
		});
	};

	const removeTypingUser = (id: string) => {
		clearTimeout(typingUsersTimeout[id]);
		delete typingUsersTimeout[id];
		typingUsers = typingUsers.filter((user) => user.id !== id);
	};

	const clearTyping = () => {
		emitTyping(false);
		Object.values(typingUsersTimeout).forEach(clearTimeout);
		typingUsersTimeout = {};
		typingUsers = [];
		lastTypingEmit = 0;
	};
	$: if (!prompt.trim()) emitTyping(false);

	const mergeChatMessages = (
		incoming: typeof history & { replaceLinks?: boolean },
		baseline: Record<string, any> | null = null,
		linksBaseline: Record<string, { parentId?: string | null; childrenIds: string[] }> | null = null
	) => {
		const currentId = history.currentId;
		const next = incoming?.messages ?? {};
		// Only follow descendants of this viewer's selected leaf. A known sibling's
		// later continuation is still another branch, including reconnect snapshots.
		const continuesSelectedBranch = () => {
			let id = incoming.currentId;
			const seen = new Set<string>();
			while (id && !seen.has(id)) {
				if (id === currentId) return true;
				seen.add(id);
				const message = history.messages[id] ?? next[id];
				if (!message) return false;
				id = message.parentId ?? null;
			}
			return false;
		};
		const follow =
			continuesSelectedBranch() &&
			!generating &&
			!(
				taskIds?.length &&
				Object.values(history.messages).some(
					(message: any) =>
						message.role === 'assistant' &&
						message.done === false &&
						(message.user_id ?? chat?.user_id) === $user?.id
				)
			) &&
			Object.keys(next).some((id) => !history.messages[id]);
		for (const [id, message] of Object.entries(next) as [string, any][]) {
			const local = history.messages[id];
			const ownActive =
				local?.done === false &&
				(local.user_id ?? chat?.user_id) === $user?.id &&
				(taskIds?.length || generating);
			const previousLinks = linksBaseline?.[id];
			const childrenIds = local?.childrenIds ?? [];
			const linksUnchanged =
				!local ||
				(linksBaseline
					? previousLinks &&
						previousLinks.parentId === local.parentId &&
						previousLinks.childrenIds.length === childrenIds.length &&
						previousLinks.childrenIds.every((childId, index) => childId === childrenIds[index])
					: !baseline || baseline[id] === local);
			if (!local || (!ownActive && (!baseline || baseline[id] === local))) {
				history.messages[id] = {
					...local,
					...message,
					...(incoming.replaceLinks && !linksUnchanged
						? { parentId: local.parentId, childrenIds: local.childrenIds }
						: {})
				};
				// Keep recovered replies finished unless their task is active or has been replaced.
				if (
					local?.done === true &&
					message.done === false &&
					local.meta?.task_id &&
					local.meta.task_id === message.meta?.task_id &&
					!taskIds?.includes(local.meta.task_id)
				) {
					history.messages[id].done = true;
				}
			}
			if (local) {
				if (incoming.replaceLinks) {
					if (linksUnchanged) {
						for (const key of ['parentId', 'childrenIds']) {
							if (key in message) history.messages[id][key] = message[key];
						}
					}
				} else {
					history.messages[id].childrenIds = [
						...new Set([...(local.childrenIds ?? []), ...(message.childrenIds ?? [])])
					];
				}
			}
		}
		if ((!currentId || follow) && incoming.currentId && next[incoming.currentId]) {
			history.currentId = incoming.currentId;
			autoScrollToBottom();
		}
		history = history;
	};

	const refreshChat = () => {
		const id = $chatId;
		if (!id || $temporaryChatEnabled) return Promise.resolve();
		if (chatRefresh?.id === id) return chatRefresh.promise;
		const baseline = { ...history.messages };
		const signal = chatLoadController.signal;
		const promise = (async () => {
			try {
				const fresh = await getChatById(localStorage.token, id, signal);
				if ($chatId !== id || joinedChatId !== id || signal.aborted) return;
				chat = fresh;
				mergeChatMessages(fresh.chat.history, baseline);
			} catch (error) {
				if ($chatId === id && joinedChatId === id && !signal.aborted) {
					clearPresence();
					chat = null;
					history = { messages: {}, currentId: null };
					if (!embedded) await goto('/');
				}
			} finally {
				if (chatRefresh?.id === id) chatRefresh = null;
			}
		})();
		chatRefresh = { id, promise };
		return promise;
	};

	const syncChatRoom = (socket: typeof $socket, id: string, temporary: boolean) => {
		const next = socket?.connected && id && !temporary && !isTemporaryChatId(id) ? id : '';
		if (next === joinedChatId) return;
		clearTyping();
		clearPresence();
		if (joinedChatId && socket?.connected)
			socket.emit('events:chat', { chat_id: joinedChatId, data: { type: 'leave' } });
		joinedChatId = next;
		const connectionId = socket?.id;
		if (next && socket)
			socket.emit('events:chat', { chat_id: next, data: { type: 'join' } }, (accepted: boolean) => {
				if (!socket.connected || socket.id !== connectionId || $chatId !== next) return;
				if (!accepted) clearPresence();
				// A refused join can mean access was lost; the refresh then sends the reader home.
				void refreshChat();
			});
	};
	$: syncChatRoom($socket, $chatId, $temporaryChatEnabled);

	const chatEventHandler = async (event: any, cb: any) => {
		// The root layout delegates browser RPCs through the page-local listener below.
		if (isTemporaryContextRpc(event)) return;
		if (
			temporaryController.identity &&
			(event?.temporary_context || isTemporaryChatId(event?.chat_id))
		) {
			await temporaryController
				.receive(event, cb)
				.catch((error) => toast.error(String(error?.message ?? error)));
			temporaryScope = temporaryController.identity;
			return;
		}
		if (event?.temporary_context) return;
		await applyChatEvent(event, cb);
	};
	const applyChatEvent = async (event: any, cb: any) => {
		const eventCurrent = temporaryController.capture();
		if (isTemporaryChatId(event.chat_id) && event.chat_id !== $chatId) return;
		if (event?.data?.type === 'chat:presence') {
			if (!$socket?.connected || event.chat_id !== $chatId || event.chat_id !== joinedChatId)
				return;
			liveParticipants = (event.data.data?.users ?? []).filter(
				(person: { id: string; name: string }) => person.id !== $user?.id
			);
			return;
		}
		if (event?.data?.type === 'voice:play_animation') {
			cb?.(
				routeAvatarAnimation(eventTarget, event, {
					active: $showCallOverlay && callMode === 'current' && selectedModelIds.length === 1,
					chatId: $chatId,
					modelId: selectedModelIds[0],
					messageId: history.currentId,
					message: history.messages[event.message_id]
				})
			);
			return;
		}
		if (event.shared && event.user_id === $user?.id && event.data?.type !== 'chat:messages') return;
		// The first event can identify a new chat before the HTTP response arrives.
		if (!$chatId && event.chat_id && history.messages[event.message_id]) {
			chatId.set(event.chat_id);
		}
		const interactionType = event?.data?.type;
		const interactionData = event?.data?.data;
		if (interactionType === 'request:interaction:done') {
			if (eventInteractionId && eventInteractionId === interactionData?.interaction_id) {
				showEventConfirmation = false;
				eventInteractionId = null;
			}
			browserInteractions = browserInteractions.filter(
				(item) => item.id !== interactionData?.interaction_id
			);
			return;
		}
		if (
			interactionType === 'request:user_input' ||
			interactionType === 'request:elicitation' ||
			(interactionType === 'confirmation' && interactionData?.tool_call)
		) {
			if (!cb) return;
			const authorId = history.messages[event.message_id]?.user_id;
			if (authorId && authorId !== $user?.id) return;
			if (event.chat_id !== $chatId) {
				cb({ status: 'cancelled', answers: {} });
				return;
			}
			if (
				interactionData?.tool_call &&
				isTemporaryChatId(event.chat_id) &&
				browserApprovalMode === 'full'
			) {
				cb(true);
				return;
			}
			const interaction: BrowserInteraction = {
				id: interactionData?.interaction_id ?? uuidv4(),
				messageId: event.message_id,
				type: interactionType,
				data: interactionData,
				callback: cb
			};
			const existing = browserInteractions.findIndex((item) => item.id === interaction.id);
			browserInteractions =
				existing < 0
					? [...browserInteractions, interaction]
					: browserInteractions.map((item, index) => (index === existing ? interaction : item));
			return;
		}

		if (event.chat_id === $chatId) {
			await tick();
			if (event.temporary_context && (!eventCurrent() || event.chat_id !== $chatId)) return;
			const type = event?.data?.type ?? null;
			if (type === 'temporary:task-done') {
				await temporaryController.reconcile();
				if (eventCurrent() && event.chat_id === $chatId) await processNextInQueue(event.chat_id);
				return;
			}
			if (type === 'typing') {
				const sender = event.user;
				if (
					!sender?.id ||
					sender.id === $user?.id ||
					(isShared && chat?.chat?.share_mode !== 'continue')
				)
					return;
				if (event.data.data?.typing) {
					if (!typingUsers.some((entry) => entry.id === sender.id))
						typingUsers = [...typingUsers, { id: sender.id, name: sender.name }];
					clearTimeout(typingUsersTimeout[sender.id]);
					typingUsersTimeout[sender.id] = setTimeout(() => removeTypingUser(sender.id), 5000);
				} else {
					removeTypingUser(sender.id);
				}
				return;
			}
			if (event.shared) {
				if (type === 'chat:messages') {
					removeTypingUser(event.user_id);
					mergeChatMessages(event.data.data);
					return;
				}
				if (type === 'chat:access' || type === 'chat:active') {
					if (type === 'chat:access') clearTyping();
					if (type === 'chat:access' || event.data.data?.active === false) await refreshChat();
					return;
				}
				if (!history.messages[event.message_id]) await refreshChat();
				if ($chatId !== event.chat_id) return;
			}
			if (type === 'context_usage' || type === 'context_compaction') {
				const usage = event?.data?.data;
				if (
					usage?.model === contextUsageModelId &&
					usage?.mode === contextCompactionMode &&
					event.message_id === history.currentId &&
					typeof usage?.tokens === 'number'
				) {
					contextUsageRequestVersion += 1;
					serverContextUsage = { ...usage, branch_id: event.message_id };
				}
			}
			if (type === 'chat:reload') {
				if (temporaryController.chatId === $chatId) await temporaryController.reconcile();
				else await loadChat();
				return;
			}
			if (type === 'chat:list') {
				return;
			}
			let message = history.messages[event.message_id];
			if (message) message = { ...message };

			if (message || type === 'confirmation' || type === 'input') {
				const data = event?.data?.data ?? null;

				queueMicrotask(() => bridge?.update());
				if (
					type === 'status' ||
					type === 'context_compaction' ||
					type === 'tool_result_compaction'
				) {
					if (
						type !== 'tool_result_compaction' ||
						isVisibleToolResultStatus(data, event.message_id, history.messages)
					) {
						message.statusHistory = mergeCompactionStatus(message.statusHistory ?? [], data);
					}
					if (type === 'context_compaction') {
						if (data?.checkpoint_message_id && history.messages[data.checkpoint_message_id]) {
							history.messages[data.checkpoint_message_id].contextSummary = data.context_summary;
						}
						handleContextCompactionStatus(data, event.message_id);
					}
				} else if (type === 'chat:active') {
					if (!data?.active) {
						if (event.temporary_context) {
							await temporaryController.reconcile();
							if (eventCurrent() && event.chat_id === $chatId)
								await processNextInQueue(event.chat_id);
						} else taskIds = null;
						if (
							$chatId &&
							!$temporaryChatEnabled &&
							hasPendingAssistantLeaf(event?.message_id ?? null)
						) {
							await loadChat();
						}
						if ($chatId && !$temporaryChatEnabled) {
							updateLastReadAt($chatId);
						}
					}
				} else if (type === 'response:completion' || type === 'chat:completion') {
					if (type === 'response:completion') {
						responseCompletionEventHandler(data, message);
					} else {
						await chatCompletionEventHandler(data, message, event.chat_id, event.shared);
					}
					autoScrollToBottom();
				} else if (type === 'chat:tasks:cancel') {
					for (const interaction of browserInteractions) {
						if (interaction.messageId === event.message_id) {
							resolveBrowserInteraction(interaction, { status: 'cancelled', answers: {} });
						}
					}
					message.bridgeCancelled = true;
					bridgeCancellations.get(event.message_id)?.();
					clearContextCompaction(event.message_id);
					if (data?.output) {
						message.output = data.output;
					}
					if (event.temporary_context) {
						message.done = true;
						history.messages[message.id] = message;
						await temporaryController.reconcile();
						if (eventCurrent() && event.chat_id === $chatId)
							await processNextInQueue(event.chat_id);
					} else if (!event.shared && event.message_id === history.currentId) {
						taskIds = null;
						// Set all response messages to done
						for (const messageId of history.messages[message.parentId].childrenIds) {
							history.messages[messageId].done = true;
							clearContextCompaction(messageId);
						}
						message.done = true;
						await processNextInQueue($chatId);
					} else {
						message.done = true;
					}
				} else if (type === 'chat:message:delta' || type === 'message') {
					message.content += data.content;
				} else if (type === 'chat:message' || type === 'replace') {
					message.content = data.content;
				} else if (type === 'chat:message:voice') {
					message.meta = { ...message.meta, voice: data.voice };
				} else if (type === 'chat:message:files' || type === 'files') {
					message.files = isTemporaryChatId(event.chat_id)
						? [...(message.files ?? []), ...data.files]
						: data.files;
				} else if (type === 'chat:message:tasks') {
					chatTasks = data.tasks;
				} else if (type === 'chat:message:embeds' || type === 'embeds') {
					message.embeds = data.embeds;

					// Auto-scroll to the embed once it's rendered in the DOM
					await tick();
					setTimeout(() => {
						const embedEl = document.getElementById(`${event.message_id}-embeds-container`);
						if (embedEl) {
							embedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
						}
					}, 100);
				} else if (type === 'chat:message:error') {
					const responseCompleted = message.done;
					if (event.shared) {
						message.error = data.error;
						message.done = true;
					} else handleOpenAIError(data.error, message);
					if (!event.shared && !responseCompleted) {
						clearContextCompaction(message.id);
						if (event.message_id === history.currentId) {
							await processNextInQueue(event.chat_id);
						}
					}
				} else if (type === 'chat:message:follow_ups') {
					message.followUps = data.follow_ups;

					if (shouldAutoScrollResponse()) {
						scrollToBottom('smooth');
					}
				} else if (type === 'chat:outlet') {
					// Outlet filter ran on backend — sync in-memory state
					const outletMessages = data.messages ?? [];
					for (const msg of outletMessages) {
						if (msg?.id && history.messages[msg.id]) {
							const existing = history.messages[msg.id];
							if (existing.content !== msg.content) {
								history.messages[msg.id] = {
									...existing,
									originalContent: existing.content,
									...msg
								};
							}
						}
					}
					history = history;
					return; // Patches history.messages directly; skip the trailing write-back.
				} else if (type === 'chat:message:favorite') {
					// Update message favorite status
					message.favorite = data.favorite;
				} else if (type === 'chat:title') {
					chatTitle.set(data);
					if (embedded && $chatId) {
						await onEmbeddedChatTitle?.($chatId, data);
					}
					await refreshChatList(localStorage.token);
					return; // The message copy is stale after the await; skip the trailing write-back.
				} else if (type === 'chat:tags') {
					chat = await getChatById(localStorage.token, $chatId);
					allTags.set(await getAllTags(localStorage.token));
					return;
				} else if (type === 'source' || type === 'citation') {
					if (data?.type === 'code_execution') {
						// Code execution; update existing code execution by ID, or add new one.
						if (!message?.code_executions) {
							message.code_executions = [];
						}

						const existingCodeExecutionIndex = message.code_executions.findIndex(
							(execution) => execution.id === data.id
						);

						if (existingCodeExecutionIndex !== -1) {
							message.code_executions[existingCodeExecutionIndex] = data;
						} else {
							message.code_executions.push(data);
						}

						message.code_executions = message.code_executions;
					} else {
						// Regular source.
						if (message?.sources) {
							message.sources.push(data);
						} else {
							message.sources = [data];
						}
					}
				} else if (type === 'notification') {
					const toastType = data?.type ?? 'info';
					const toastContent = data?.content ?? '';

					if (toastType === 'success') {
						toast.success(toastContent);
					} else if (toastType === 'error') {
						toast.error(toastContent);
					} else if (toastType === 'warning') {
						toast.warning(toastContent);
					} else {
						toast.info(toastContent);
					}
				} else if (type === 'confirmation') {
					eventCallback = cb;
					eventInteractionId = data.interaction_id ?? null;

					eventConfirmationInput = false;
					showEventConfirmation = true;
					eventConfirmationInputOptions = [];

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
				} else if (type === 'execute') {
					try {
						// Use Function constructor to evaluate code in a safer way
						const asyncFunction = new Function(`return (async () => { ${data.code} })()`);
						const result = await asyncFunction(); // Await the result of the async function

						if (cb) {
							cb(result);
						}
					} catch (error) {
						console.error('Error executing code:', error);
					}
				} else if (type === 'input') {
					eventCallback = cb;
					eventInteractionId = data.interaction_id ?? null;

					eventConfirmationInput = true;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
					eventConfirmationInputPlaceholder = data.placeholder;
					eventConfirmationInputValue = data?.value ?? '';
					eventConfirmationInputType = data?.input?.type ?? data?.type ?? '';
					eventConfirmationInputOptions = data?.input?.options ?? data?.options ?? [];
				} else if (type.startsWith('terminal:')) {
					terminalEventHandler(type, data, message);
				} else {
					console.log('Unknown message type', data);
				}

				if (message) history.messages[event.message_id] = message;
			}
		} else {
			// Non-active chat completion: queue stays in the global store.
			// navigateHandler will process it when the user returns to that chat.
		}
	};

	const onMessageHandler = async (event: {
		origin: string;
		source: unknown;
		data: { type: string; text?: string; toolId?: string; error?: string | null };
	}) => {
		const isSameOrigin = event.origin === window.origin;
		const type = event.data?.type;

		// Prompt-driving types are trusted only same-origin, from our own embed iframes
		// (opaque srcdoc origin, submission still confirmed below) or via explicit opt-in.
		const promptTypes = ['input:prompt', 'input:prompt:submit', 'action:submit'];
		const isOwnEmbed = isEmbedWindow(event.source);
		const isTrusted =
			isSameOrigin || isOwnEmbed || ($settings?.iframeSandboxAllowSameOrigin ?? false);

		// Non-prompt message types are always restricted to same-origin only.
		if (!isSameOrigin && !promptTypes.includes(type)) {
			return;
		}

		// Prompt types from an untrusted cross-origin source are silently dropped.
		if (promptTypes.includes(type) && !isTrusted) {
			return;
		}

		if (type === 'action:submit') {
			console.debug(event.data.text);

			if (prompt !== '') {
				if (isSameOrigin) {
					await tick();
					submitHandler(prompt);
				} else {
					eventConfirmationInput = false;
					eventConfirmationTitle = $i18n.t('Confirm Prompt from Embed');
					eventConfirmationMessage = prompt;
					eventInteractionId = null;
					eventCallback = async (confirmed: boolean) => {
						if (confirmed) {
							await tick();
							submitHandler(prompt);
						}
					};
					showEventConfirmation = true;
				}
			}
		}

		if (type === 'input:prompt') {
			console.debug(event.data.text);

			const inputElement = document.getElementById('chat-input');

			if (inputElement) {
				messageInput?.setTextHandler(event.data.text);
				messageInput?.focus({ preventScroll: true });
			}
		}

		if (type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				if (isSameOrigin) {
					await tick();
					submitHandler(event.data.text!);
				} else {
					eventConfirmationInput = false;
					eventConfirmationTitle = $i18n.t('Confirm Prompt from Embed');
					eventConfirmationMessage = event.data.text;
					eventInteractionId = null;
					eventCallback = async (confirmed: boolean) => {
						if (confirmed) {
							await tick();
							submitHandler(event.data.text!);
						}
					};
					showEventConfirmation = true;
				}
			}
		}
	};

	const stopAudio = () => {
		try {
			speechSynthesis.cancel();
			$audioQueue?.stop();
		} catch {}
	};

	const hasPendingAssistantLeaf = (messageId: string | null = null) =>
		(messageId ? [history.messages[messageId]] : Object.values(history.messages)).some(
			(message: any) =>
				message?.role === 'assistant' && !message.done && (message.childrenIds?.length ?? 0) === 0
		);

	const handleSocketConnect = async () => {
		clearTyping();
		if (temporaryController.identity) {
			await temporaryController
				.rebind()
				.catch((error) => toast.error(String(error?.message ?? error)));
			temporaryScope = temporaryController.identity;
			return;
		}
		if (!$chatId || !$socket?.id) return;
		if (isTemporaryChatId($chatId)) {
			const id = $chatId;
			try {
				const result = await resumeTemporaryChat(localStorage.token, id, $socket.id);
				if ($chatId !== id) return;
				taskIds = result.task_ids.length ? result.task_ids : null;
				for (const stream of result.streams) {
					const message = history.messages[stream.message_id];
					if (message && !message.done) message.output = stream.output;
				}

				if (!taskIds) {
					for (const message of Object.values(history.messages) as any[]) {
						if (message.role === 'assistant' && !message.done) {
							message.done = true;
							message.error = { content: $i18n.t('Connection lost. Please retry.') };
						}
					}
				}
				history = history;
			} catch (error) {
				toast.error(getErrorMessage(error));
			}
			return;
		}
		joinedChatId = '';
		syncChatRoom($socket, $chatId, $temporaryChatEnabled);
		const id = $chatId;
		const tasks = await getTaskIdsByChatId(localStorage.token, id).catch(() => null);
		if ($chatId !== id || !tasks) return;
		taskIds = tasks.task_ids?.length ? tasks.task_ids : null;
		if (!taskIds && hasPendingAssistantLeaf()) {
			// A server restart can leave unfinished messages without a completion event.
			const loaded = await loadChat();
			if (loaded && $chatId === id) await processNextInQueue(id);
		}
	};

	onMount(() => {
		const temporaryRpc = (message: CustomEvent) => {
			const { event, callback, run } = message.detail;
			void temporaryController
				.receive(event, callback, (_event, _callback, current) => run(current))
				.catch(reportTemporaryError);
		};
		window.addEventListener('temporary-context:rpc', temporaryRpc as EventListener);
		const unloadTemporary = () => resetTemporaryScope(true);
		window.addEventListener('pagehide', unloadTemporary);
		loading = true;
		console.log('mounted');
		window.addEventListener('message', onMessageHandler);
		$socket?.on('events', chatEventHandler);
		$socket?.on('connect', handleSocketConnect);
		$socket?.on('disconnect', clearTyping);
		$socket?.on('disconnect', clearPresence);
		$socket?.on('disconnect', handleInteractionDisconnect);

		$audioQueue?.destroy();

		const audioQueueInstance = new AudioQueue(
			document.getElementById('audioElement'),
			() => $settings.responseAutoPlayback ?? false
		);
		audioQueue.set(audioQueueInstance);

		// Restore direct terminal enabled states based on persisted selectedTerminalId
		if ($config?.features?.enable_direct_integrations && $settings?.terminalServers?.length) {
			settings.set({
				...$settings,
				terminalServers: ($settings.terminalServers ?? []).map((s) => ({
					...s,
					enabled: $selectedTerminalId !== null && s.url === $selectedTerminalId
				}))
			});
		}

		if ($page.url.pathname === '/' || $page.url.pathname.startsWith('/folders/')) {
			tick().then(initNewChat);
		}

		// the artifacts pane reads this list when it opens, which can be before the debounced rebuild
		const showArtifactsSubscribe = showArtifacts.subscribe((value) => {
			if (value) getContents();
		});

		const selectedFolderSubscribe = selectedFolder.subscribe(async (folder) => {
			await tick();
			// Folder defaults apply to new chats only.
			if (!history.currentId && folder) {
				applyFolderTerminal(folder);
				if (folder.data?.terminal_id) {
					await refreshAgentModels();
					if (history.currentId || $selectedFolder?.id !== folder.id) return;
				}
				const folderModels = normalizeSelectedModels(folder.data?.model_ids ?? []);
				if (!equal(selectedModels, folderModels)) {
					selectedModels = folderModels;
				}
			}
		});

		const storageChatInput = sessionStorage.getItem(
			`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`
		);

		const init = async () => {
			if (!chatIdProp) {
				loading = false;
				await tick();
			}

			if (storageChatInput) {
				prompt = '';
				messageInput?.setText('');

				files = [];
				selectedToolIds = [];
				selectedSkillIds = [];
				selectedFilterIds = [];
				webSearchEnabled = false;
				imageGenerationEnabled = false;
				knowledgeEnabled = false;
				knowledgeWriteEnabled = false;
				memoryEnabled = false;
				notesEnabled = false;
				codeInterpreterEnabled = false;

				await restoreChatInput(storageChatInput);
			}

			messageInput?.focus({ preventScroll: true });
		};
		init();

		return () => {
			try {
				clearTimeout(webSearchConfirmTimeout);
				cancelBrowserInteractions();
				agentDiscovery++;
				clearTimeout(saveControlsTimer);
				saveControls();
				if (chatIdProp && !$temporaryChatEnabled) {
					updateLastReadAt(chatIdProp);
				}
				showArtifactsSubscribe();
				selectedFolderSubscribe();

				// Clear the selected chat when leaving the chat surface (e.g. navigating
				// to the admin panel), otherwise the previously-viewed chat stays selected
				// in the sidebar and deleting/archiving it wrongly navigates away.
				clearTyping();
				clearPresence();
				if (joinedChatId && $socket?.connected)
					$socket.emit('events:chat', { chat_id: joinedChatId, data: { type: 'leave' } });
				joinedChatId = '';
				chatId.set('');
				chatTitle.set('');

				window.removeEventListener('message', onMessageHandler);
				window.removeEventListener('pagehide', unloadTemporary);
				window.removeEventListener('temporary-context:rpc', temporaryRpc as EventListener);
				resetTemporaryScope(true);
				$socket?.off('events', chatEventHandler);
				$socket?.off('connect', handleSocketConnect);
				$socket?.off('disconnect', clearTyping);
				$socket?.off('disconnect', clearPresence);
				$socket?.off('disconnect', handleInteractionDisconnect);
				contextCompactions.set({});
				audioQueueInstance?.destroy();
				audioQueue.set(null);
			} catch (e) {
				console.error(e);
			}
		};
	});

	// File upload functions

	const uploadGoogleDriveFile = async (fileData) => {
		// Validate input
		if (!fileData?.id || !fileData?.name || !fileData?.url || !fileData?.headers?.Authorization) {
			throw new Error('Invalid file data provided');
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: fileData.url,
			name: fileData.name,
			collection_name: '',
			status: 'uploading',
			error: '',
			itemId: tempItemId,
			size: 0
		};

		try {
			files = [...files, fileItem];
			console.log('Processing web file with URL:', fileData.url);

			// Configure fetch options with proper headers
			const fetchOptions = {
				headers: {
					Authorization: fileData.headers.Authorization,
					Accept: '*/*'
				},
				method: 'GET'
			};

			// Attempt to fetch the file
			console.log('Fetching file content from Google Drive...');
			const fileResponse = await fetch(fileData.url, fetchOptions);

			if (!fileResponse.ok) {
				const errorText = await fileResponse.text();
				throw new Error(`Failed to fetch file (${fileResponse.status}): ${errorText}`);
			}

			// Get content type from response
			const contentType = fileResponse.headers.get('content-type') || 'application/octet-stream';
			console.log('Response received with content-type:', contentType);

			// Convert response to blob
			console.log('Converting response to blob...');
			const fileBlob = await fileResponse.blob();

			if (fileBlob.size === 0) {
				throw new Error('Retrieved file is empty');
			}

			console.log('Blob created:', {
				size: fileBlob.size,
				type: fileBlob.type || contentType
			});

			// Create File object with proper MIME type
			const file = new File([fileBlob], fileData.name, {
				type: fileBlob.type || contentType
			});

			console.log('File object created:', {
				name: file.name,
				size: file.size,
				type: file.type
			});

			if (file.size === 0) {
				throw new Error('Created file is empty');
			}

			// If the file is an audio file, provide the language for STT.
			let metadata = null;
			if (
				(file.type.startsWith('audio/') || file.type.startsWith('video/')) &&
				$settings?.audio?.stt?.language
			) {
				metadata = {
					language: $settings?.audio?.stt?.language
				};
			}

			// Upload file to server
			console.log('Uploading file to server...');
			const uploadedFile = await uploadFile(localStorage.token, file, metadata);

			if (!uploadedFile) {
				throw new Error('Server returned null response for file upload');
			}

			console.log('File uploaded successfully:', uploadedFile);

			// Update file item with upload results
			fileItem.status = 'uploaded';
			fileItem.file = uploadedFile;
			fileItem.id = uploadedFile.id;
			fileItem.size = file.size;
			fileItem.collection_name = uploadedFile?.meta?.collection_name;
			fileItem.url = `${uploadedFile.id}`;

			files = files;
			toast.success($i18n.t('File uploaded successfully'));
		} catch (e) {
			console.error('Error uploading file:', e);
			fileItem.status = 'error';
			fileItem.error = e.message || `${e}`;
			files = files.filter((f) => f.itemId !== tempItemId);
			toast.error(
				$i18n.t('Error uploading file: {{error}}', {
					error: e.message || 'Unknown error'
				})
			);
		}
	};

	const uploadWeb = async (urls) => {
		if ($user?.role !== 'admin' && !($user?.permissions?.chat?.web_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload web content.'));
			return;
		}

		if (!Array.isArray(urls)) {
			urls = [urls];
		}

		// Create file items first
		const fileItems = urls.map((url) => ({
			type: 'text',
			name: url,
			collection_name: '',
			status: 'uploading',
			context: 'full',
			url,
			error: ''
		}));

		// Display all items at once
		files = [...files, ...fileItems];

		for (const fileItem of fileItems) {
			try {
				const res = await processUrl(localStorage.token, fileItem.url);

				if (res) {
					const uploadedFile = res.file;
					fileItem.status = 'uploaded';
					fileItem.name = res.name ?? fileItem.name;
					fileItem.collection_name = res.collection_name;

					if (res.type === 'image' && uploadedFile) {
						fileItem.type = 'image';
						fileItem.file = uploadedFile;
						fileItem.id = uploadedFile.id;
						fileItem.url = `${uploadedFile.id}`;
						fileItem.content_type = uploadedFile.meta?.content_type;
						fileItem.size = uploadedFile.meta?.size;
					} else if (res.type === 'file' && uploadedFile) {
						fileItem.type = 'file';
						fileItem.file = uploadedFile;
						fileItem.id = uploadedFile.id;
						fileItem.url = `${uploadedFile.id}`;
						fileItem.content_type = uploadedFile.meta?.content_type;
						fileItem.size = uploadedFile.meta?.size;
						fileItem.collection_name =
							res.collection_name ??
							uploadedFile.meta?.collection_name ??
							uploadedFile.collection_name;
					} else {
						fileItem.type = 'text';
						fileItem.file = {
							data: {
								content: res.content
							},
							meta: {
								name: res.name ?? fileItem.name,
								source: res.url ?? fileItem.url
							}
						};
					}
				}

				files = [...files];
			} catch (e) {
				fileItem.status = 'error';
				fileItem.error = `${e}`;
				files = files.filter((f) => f.name !== fileItem.name);
				toast.error(`${e}`);
			}
		}

		await onUpdate();
	};

	const onUpdate = async ({ file }: { file?: any } = {}) => {
		if (file?.itemId) {
			chatRequestQueues.update((q) => ({
				...q,
				[$chatId]: (q[$chatId] ?? []).map((message) =>
					(message.files ?? []).some((item) => item.itemId === file.itemId)
						? {
								...message,
								files: message.files.map((item) => (item.itemId === file.itemId ? file : item))
							}
						: message
				)
			}));
		}

		await processNextInQueue($chatId);
	};

	const onUpload = async (event: any) => {
		const { type, data } = event;

		if (type === 'google-drive') {
			await uploadGoogleDriveFile(data);
		} else if (type === 'web') {
			await uploadWeb(data);
		}
	};

	const onHistoryChange = (history) => {
		if (history) {
			bridge?.update();
			clearTimeout(contentsRAF);
			contentsRAF = setTimeout(() => {
				getContents();
				contentsRAF = undefined;
			}, 0);
		} else {
			artifactContents.set([]);
		}
	};

	$: onHistoryChange(history);

	let callMode = 'current';
	let bridge: RealtimeCall;
	let releaseCallWakeLock = () => {};
	const updateCallWakeLock = (active: boolean) => {
		releaseCallWakeLock();
		releaseCallWakeLock = active ? acquireWakeLock() : () => {};
	};
	$: callInProgress =
		($showCallOverlay && callMode === 'current') || !!(bridge?.connected || bridge?.connecting);
	$: updateCallWakeLock(callInProgress);
	onDestroy(() => releaseCallWakeLock());

	const bridgeCancellations = new Map<string, () => void>();
	const visibleMessageText = (message: any) =>
		getOutputText(message?.output) || removeAllDetails(message?.content ?? '');

	const generateVoiceChatMetadata = async (
		_chatId: string,
		modelId: string,
		content: string,
		initialTitle: string
	) => {
		const token = localStorage.token;
		const messages = [{ role: 'user', content }];
		const autoTitle = $settings?.title?.auto ?? true;
		const autoTags = $settings?.autoTags ?? true;
		const title = autoTitle
			? await generateTitle(token, modelId, messages, _chatId).catch((error) => {
					console.error('Could not generate voice chat title', error);
					return null;
				})
			: null;
		const savedChat = await getChatById(token, _chatId);
		if (savedChat.title === initialTitle) {
			const updated = await updateChatById(token, _chatId, {
				title: title || content.slice(0, 100)
			});
			if ($chatId === _chatId) {
				chatTitle.set(updated.title);
				if (embedded) await onEmbeddedChatTitle?.(_chatId, updated.title);
			}
			await refreshChatList(token);
		}
		if (autoTags) {
			const tags = await generateTags(token, modelId, messages, _chatId);
			for (const tag of tags) await addTagById(token, _chatId, tag);
			allTags.set(await getAllTags(token));
		}
	};

	const addVoiceMessage = async (
		role: string,
		content: string,
		voice: any,
		parentId: string | null = history.currentId
	) => {
		const id = uuidv4();
		if ($chatId && !$temporaryChatEnabled && !isTemporaryChatId($chatId)) {
			const savedChatId = $chatId;
			const currentId = history.currentId;
			const baseline = { ...history.messages };
			// Socket updates can mutate active messages in place, so copy links before awaiting HTTP.
			const linksBaseline = Object.fromEntries(
				Object.entries(baseline).map(([id, message]: [string, any]) => [
					id,
					{ parentId: message.parentId, childrenIds: [...(message.childrenIds ?? [])] }
				])
			);
			const turn = await addChatVoiceMessage(localStorage.token, savedChatId, {
				id,
				parent_id: parentId,
				branch_id: currentId,
				role: role as 'user' | 'assistant',
				content,
				voice
			});
			if ($chatId !== savedChatId) return id;
			mergeChatMessages(turn, baseline, linksBaseline);
			const currentMessage = history.messages[id];
			const savedMessage = turn.messages[id];
			if (currentMessage && savedMessage?.meta) {
				// The public socket frame omits author-private metadata; keep any newer local metadata.
				history.messages[id] = {
					...currentMessage,
					meta: { ...savedMessage.meta, ...currentMessage.meta }
				};
				history = history;
			}
			if (history.currentId === currentId && currentId === turn.messages[id]?.parentId) {
				history.currentId = id;
				history = history;
			}
			return id;
		}
		const branch = createMessagesList(history, history.currentId);
		let parentIndex = branch.findIndex((entry) => entry.id === parentId);
		// A chat answer can have been inserted while this spoken reply was being generated.
		// Keep the reply on that branch, before the next user turn, rather than losing it as a sibling.
		if (role === 'assistant' && parentIndex >= 0) {
			while (branch[parentIndex + 1]?.role === 'assistant') parentIndex++;
			parentId = branch[parentIndex].id;
		}
		const nextMessage = parentIndex >= 0 ? branch[parentIndex + 1] : null;
		const message = {
			id,
			parentId,
			childrenIds: [] as string[],
			role,
			content,
			done: true,
			timestamp: Math.floor(Date.now() / 1000),
			meta: { voice },
			user_id: $user?.id,
			voice_provenance: { source: 'browser', user: { id: $user?.id, name: $user?.name } },
			...(role === 'assistant' ? { model: voice.model, modelName: voice.model, modelIdx: 0 } : {})
		};
		history.messages[id] = message;
		if (parentId && history.messages[parentId]) {
			history.messages[parentId].childrenIds.push(id);
			// A second transcript can arrive before the first spoken reply finishes.
			// Insert that reply before the next user message without switching branches.
			if (role === 'assistant' && nextMessage?.role === 'user') {
				nextMessage.parentId = id;
				message.childrenIds.push(nextMessage.id);
				history.messages[parentId].childrenIds = history.messages[parentId].childrenIds.filter(
					(childId: string) => childId !== nextMessage.id
				);
			}
		}
		if (history.currentId === parentId) history.currentId = id;
		history = history;
		if (!$chatId) {
			const modelId = selectedModelIds[0];
			const _chatId = await initChatHandler(history);
			if (_chatId && !$temporaryChatEnabled) {
				// Realtime replies need no chat completion; start metadata work at chat creation.
				void generateVoiceChatMetadata(_chatId, modelId, content, chat.title).catch((error) => {
					console.error('Could not save voice chat metadata', error);
				});
			}
		}
		return id;
	};

	const saveVoice = async (id: string, voice: any) => {
		const message = history.messages[id];
		if (!message) return;
		const previous = message.meta?.voice ?? {};
		const speech = new Map((previous.speech ?? []).map((item: any) => [item.item_id, item]));
		for (const item of voice.speech ?? []) speech.set(item.item_id, item);
		const merged = { ...previous, ...voice, speech: [...speech.values()] };
		message.meta = { ...message.meta, voice: merged };
		history = history;
		if ($chatId && !$temporaryChatEnabled && !isTemporaryChatId($chatId)) {
			const savedChatId = $chatId;
			const saved = await updateChatVoiceMessage(localStorage.token, savedChatId, id, merged);
			if ($chatId !== savedChatId) return;
			const savedVoice = saved?.chat?.history?.messages?.[id]?.meta?.voice;
			if (savedVoice && history.messages[id]) {
				history.messages[id].meta = { ...history.messages[id].meta, voice: savedVoice };
				history = history;
			}
		}
	};

	onMount(() => {
		bridge = new RealtimeCall({
			context: () => {
				const model =
					selectedModelIds.length === 1
						? $models.find((model) => model.id === selectedModelIds[0])
						: null;
				return {
					chatId: isTemporaryChatId($chatId) ? undefined : $chatId || undefined,
					modelId: model && !('direct' in model && model.direct) ? model.id : '',
					terminalId: $selectedTerminalId || undefined,
					voiceModel: $config?.audio?.realtime?.model,
					voice: model?.info?.meta?.voice?.voice || $config?.audio?.realtime?.voice,
					messages: createMessagesList(history, history.currentId).map((message) => {
						const voice = message.meta?.voice;
						const spoken = voice && message.model === voice.model && !message.output?.length;
						const state = getBridgeTurnState(message);
						const speech = !spoken
							? (voice?.speech ?? [])
									.map((item: any) => item.transcript)
									.filter(Boolean)
									.join('\n')
							: '';
						let content = visibleMessageText(message);
						if (message.role === 'assistant') {
							const source = spoken
								? 'Historical voice transcript, not current task status'
								: 'Chat model';
							content = `[${source}; message_id=${message.id}; state=${state}]\n${state === 'completed' ? content : ''}`;
							if (speech)
								content += `\n[Historical voice transcript, not current task status]\n${speech}`;
						}
						return { role: message.role, content };
					})
				};
			},
			addMessage: addVoiceMessage,
			submit: submitHandler,
			message: (id) => history.messages[id],
			visibleText: visibleMessageText,
			saveVoice,
			stop: async (id) => {
				const message = history.messages[id];
				if (!message || message.bridgeCancelled) return;
				let timer: ReturnType<typeof setTimeout> | undefined;
				const settled = new Promise<void>((resolve, reject) => {
					bridgeCancellations.set(id, resolve);
					timer = setTimeout(
						() => reject(new Error('Backend cancellation was not confirmed')),
						15000
					);
				});
				try {
					const [stopped] = await Promise.all([stopResponse(false, id), settled]);
					if (!stopped) throw new Error('Backend cancellation was not confirmed');
				} finally {
					clearTimeout(timer);
					bridgeCancellations.delete(id);
				}
			},
			change: () => {
				bridge = bridge;
			},
			error: (message) => toast.error(message)
		});
		return () => bridge?.end();
	});
	// Only real navigation resets the chat; shallow sidebar state changes must preserve it.
	afterNavigate(async ({ from, to }) => {
		// A navigation that mounts this page starts its new chat in onMount.
		if (
			from?.route.id === to?.route.id &&
			(to?.url.pathname === '/' || to?.url.pathname.startsWith('/folders/'))
		) {
			await tick();
			initNewChat();
		}
		stopAudio();
	});
	let closingTemporary: Promise<boolean> | null = null;
	const closeTemporaryBeforeSwitch = () => {
		if (closingTemporary) return closingTemporary;
		closingTemporary = (async () => {
			try {
				if (terminalTabs && !(await terminalTabs.closeTemporarySideChats()))
					throw new Error($i18n.t('A temporary side chat is still stopping. Try again.'));
				if (temporaryController.identity && !(await temporaryController.close()))
					throw new Error($i18n.t('The temporary chat is still stopping. Try again.'));
				resetTemporaryScope();
				return true;
			} catch (reason) {
				toast.error(getErrorMessage(reason));
				return false;
			}
		})().finally(() => {
			closingTemporary = null;
		});
		return closingTemporary;
	};
	beforeNavigate((navigation) => {
		const { from, to } = navigation;
		if (!to || from?.url.pathname === to.url.pathname) return;
		if (
			!embedded &&
			!navigation.willUnload &&
			to.route.id &&
			(temporaryController.identity || terminalTabs?.hasTemporarySideChats())
		) {
			navigation.cancel();
			if (closingTemporary) return;
			void closeTemporaryBeforeSwitch().then((closed) => {
				if (!closed) return;
				if (navigation.type === 'popstate') window.history.go(navigation.delta);
				else void goto(to.url);
			});
			return;
		}
		if (!embedded) loading = true;
		bridge?.end();
		pendingUrlActions = null;
	});
	$: if (!$user && (bridge?.connected || bridge?.connecting)) bridge.end();
	$: if (selectedModelIds && $models && $config) bridge?.syncModel();

	const dispatchCallOverlayAudio = (message, final = false) => {
		if (message.error || !$showCallOverlay || callMode !== 'current') {
			return;
		}

		const ttsSplitOn = $config?.audio?.tts?.split_on ?? 'punctuation';
		const messageContentParts = getMessageContentParts(
			getOutputText(message?.output) || removeAllDetails(message?.content ?? ''),
			ttsSplitOn
		);

		const sentContentPartCount = message.ttsSentContentPartCount ?? 0;
		const nextContentParts = (final ? messageContentParts : messageContentParts.slice(0, -1)).slice(
			sentContentPartCount
		);
		const pendingContentPartIndex = nextContentParts.findIndex(
			(content) =>
				!final &&
				ttsSplitOn === 'punctuation' &&
				(content.split(/\s+/).length < 4 || content.length < 50)
		);
		const dispatchContentParts =
			pendingContentPartIndex === -1
				? nextContentParts
				: nextContentParts.slice(0, pendingContentPartIndex);

		dispatchContentParts.forEach((content) => {
			eventTarget.dispatchEvent(
				new CustomEvent('chat', {
					detail: {
						id: message.id,
						content
					}
				})
			);
		});
		message.ttsSentContentPartCount = sentContentPartCount + dispatchContentParts.length;
	};

	const getContents = () => {
		const messages = history ? createMessagesList(history, history.currentId) : [];
		let contents = [];
		messages.forEach((message) => {
			if (message?.role !== 'user') {
				const messageContent =
					getOutputText(message?.output) || removeAllDetails(message?.content ?? '');
				if (!messageContent.trim()) {
					return;
				}

				const { codeBlocks: codeBlocks, htmlGroups: htmlGroups } =
					getCodeBlockContents(messageContent);

				if (htmlGroups && htmlGroups.length > 0) {
					htmlGroups.forEach((group) => {
						const renderedContent = `
                        <!DOCTYPE html>
                        <html lang="en">
                        <head>
                            <meta charset="UTF-8">
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
							<${''}style>
								body {
									background-color: white; /* Ensure the iframe has a white background */
								}

								${group.css}
							</${''}style>
                        </head>
                        <body>
                            ${group.html}

							<${''}script>
                            	${group.js}
							</${''}script>
                        </body>
                        </html>
                    `;
						contents = [...contents, { type: 'iframe', content: renderedContent }];
					});
				} else {
					// Check for SVG content
					for (const block of codeBlocks) {
						if (block.lang === 'svg' || (block.lang === 'xml' && block.code.includes('<svg'))) {
							contents = [...contents, { type: 'svg', content: block.code }];
						}
					}
				}
			}
		});

		artifactContents.set(contents);
	};

	// Web functions

	const openCallOverlay = () => {
		if (isShared) return;

		if (!($user?.role === 'admin' || ($user?.permissions?.chat?.call ?? true))) {
			return;
		}

		if (
			$user?.role !== 'admin' &&
			!($user?.permissions?.chat?.stt ?? true) &&
			!$config?.audio?.realtime?.enabled
		) {
			return;
		}

		if (selectedModels.length > 1) {
			toast.error($i18n.t('Select only one model to call'));
			return;
		}

		if (
			!bridge?.connected &&
			!bridge?.connecting &&
			!$config?.audio?.realtime?.enabled &&
			$config?.audio?.stt?.engine === 'web'
		) {
			toast.error($i18n.t('Call feature is not supported when using Web STT engine'));
			return;
		}

		showCallOverlay.set(true);
		showTabs.set(true);
	};

	const initNewChat = async () => {
		if (!(await closeTemporaryBeforeSwitch())) return;
		terminalTabs?.prepareChatSwitch();
		loading = true;
		if (
			!embedded &&
			($showTabs === 'comments' || $showTabs === 'side' || typeof $showTabs === 'object')
		)
			showTabs.set(false);
		const signal = startChatLoad();
		modelDefaults = null;
		clearTimeout(saveControlsTimer);
		await saveControls();
		if (signal.aborted) return;
		if ($chatId) terminalContext = null;
		cancelBrowserInteractions();
		bridge?.end();
		console.log('initNewChat');
		resetWebSearchConfirmation();

		// Mark the outgoing chat as read before resetting; in-place created chats
		// keep chatIdProp undefined, so navigateHandler never marks them read.
		if ($chatId && !$temporaryChatEnabled) {
			updateLastReadAt($chatId);
		}

		if (
			$temporaryChatAllowed &&
			$user?.role !== 'admin' &&
			$user?.permissions?.chat?.temporary_enforced
		) {
			await temporaryChatEnabled.set(true);
		}

		if ($temporaryChatAllowed && ($settings?.temporaryChatByDefault ?? false)) {
			if ($temporaryChatEnabled === false) {
				await temporaryChatEnabled.set(true);
			} else if ($temporaryChatEnabled === null) {
				// if set to null set to false; refer to temp chat toggle click handler
				await temporaryChatEnabled.set(false);
			}
		}

		if ($page.url.searchParams.get('temporary-chat') === 'true' && $temporaryChatAllowed) {
			temporaryChatEnabled.set(true);
		}

		if (!$temporaryChatAllowed) {
			await temporaryChatEnabled.set(false);
		}

		const availableModels = $models
			.filter((m) => !(m?.info?.meta?.hidden ?? false))
			.map((m) => m.id);

		const defaultModels = normalizeSelectedModels();

		let modelSearch = '';
		const openModelSelectorWithSearch = async (modelId: string) => {
			const modelSelectorButton = document.getElementById('model-selector-model-button');
			modelSelectorButton?.click();

			await tick();

			const modelSelectorInput = document.getElementById(
				'model-search-input'
			) as HTMLInputElement | null;
			if (modelSelectorInput) {
				modelSelectorInput.focus();
				modelSelectorInput.value = modelId;
				modelSelectorInput.dispatchEvent(new Event('input', { bubbles: true }));
			}
		};

		if ($page.url.searchParams.get('models') || $page.url.searchParams.get('model')) {
			const urlModels = (
				$page.url.searchParams.get('models') ||
				$page.url.searchParams.get('model') ||
				''
			)?.split(',');

			if (urlModels.length === 1) {
				if (!$models.find((m) => m.id === urlModels[0])) {
					// Model not found; open model selector and prefill
					modelSearch = urlModels[0];
				} else {
					// Model found; set it as selected
					selectedModels = urlModels;
				}
			} else {
				// Multiple models; set as selected
				selectedModels = urlModels;
			}

			// Unavailable models filtering
			selectedModels = selectedModels.filter(
				(modelId) => modelId.startsWith('agent:') || $models.map((m) => m.id).includes(modelId)
			);
		} else {
			if ($selectedFolder) {
				// Folder defaults are explicit; never inherit a previous chat's selection.
				applyFolderTerminal($selectedFolder);
				if ($selectedFolder.data?.terminal_id) {
					await refreshAgentModels();
				}
				selectedModels = normalizeSelectedModels($selectedFolder.data?.model_ids ?? []);
			} else {
				if (sessionStorage.selectedModels) {
					// Set from session storage (temporary selection)
					selectedModels = JSON.parse(sessionStorage.selectedModels);
					sessionStorage.removeItem('selectedModels');
				} else {
					if ($settings?.models) {
						// Set from user settings
						selectedModels = $settings?.models;
					} else if (defaultModels && defaultModels.length > 0) {
						// Set from default models
						selectedModels = defaultModels;
					}
				}
			}

			// Unavailable & hidden models filtering
			selectedModels = normalizeSelectedModels(selectedModels);
		}

		// Ensure at least one model is selected
		if (selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === '')) {
			if (availableModels.length > 0) {
				if (defaultModels && defaultModels.length > 0) {
					selectedModels = defaultModels.filter((modelId) => availableModels.includes(modelId));
				}

				if (
					selectedModels.length === 0 ||
					(selectedModels.length === 1 && selectedModels[0] === '')
				) {
					// Only fall back to first available model if default models didn't resolve
					selectedModels = [availableModels?.at(0) ?? ''];
				}
			} else {
				selectedModels = [''];
			}
		}

		if (!($user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true))) {
			selectedModels = selectedModels.length > 0 ? [selectedModels[0]] : [''];
		}

		if ($mobile) {
			await showTabs.set(false);
		}
		await showCallOverlay.set(false);
		await showArtifacts.set(false);

		if (!embedded && $page.url.pathname.includes('/c/')) {
			window.history.replaceState(window.history.state, '', `/`);
		}

		autoScroll = true;

		// resetInput() must stay last: the selected model's defaults override the draft's selection.
		const chatInputPrompt = (await restoreChatInput(sessionStorage.getItem('chat-input')))
			? prompt
			: null;
		const previousChatId = $chatId;
		const previousMessageId = history.currentId;
		await resetInput();
		if (signal.aborted) return;
		// A chat opened or a message sent in the meantime keeps its id and history
		if (($chatId && $chatId !== previousChatId) || history.currentId !== previousMessageId) {
			loading = false;
			return;
		}
		await chatId.set('');
		await chatTitle.set('');

		temporaryConnectionSessionId = undefined;
		history = {
			messages: {},
			currentId: null
		};

		// Apply folder defaults after restoring the draft and model defaults.
		applyFolderTerminal($selectedFolder);

		chatFiles = [];
		params = {};
		reasoningEffortByModel = {};
		seedReasoningEffortFromLastUsed(selectedModelIds, false);
		chatVariables = {};
		taskIds = null;
		chatTasks = [];

		const youtube = $page.url.searchParams.get('youtube');
		const loadUrl = $page.url.searchParams.get('load-url');
		const urls = [
			...(youtube ? [`https://www.youtube.com/watch?v=${youtube}`] : []),
			...(loadUrl ? [loadUrl] : [])
		];
		const call =
			$page.url.searchParams.get('call') === 'true' ||
			$page.url.searchParams.get('voice') === 'true';
		pendingUrlActions = urls.length || call ? { urls, call } : null;

		if ($page.url.searchParams.get('web-search') === 'true') {
			webSearchEnabled = true;
		}

		if ($page.url.searchParams.get('image-generation') === 'true') {
			imageGenerationEnabled = true;
		}

		if ($page.url.searchParams.get('code-interpreter') === 'true') {
			codeInterpreterEnabled = true;
		}

		if ($page.url.searchParams.get('tools')) {
			selectedToolIds = $page.url.searchParams
				.get('tools')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id && ($tools ?? []).find((t) => t.id === id));
		} else if ($page.url.searchParams.get('tool-ids')) {
			selectedToolIds = $page.url.searchParams
				.get('tool-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id && ($tools ?? []).find((t) => t.id === id));
		}

		// Restore tool selection after OAuth redirect
		const pendingToolId = sessionStorage.getItem('pendingOAuthToolId');
		if (pendingToolId) {
			sessionStorage.removeItem('pendingOAuthToolId');
			if (!selectedToolIds.includes(pendingToolId)) {
				selectedToolIds = [...selectedToolIds, pendingToolId];
			}
		}

		loading = false;
		await tick();
		// Mounting the editor clears prompt, so apply the saved text afterward.
		if (chatInputPrompt !== null) messageInput?.setText(chatInputPrompt);
		if (modelSearch) await openModelSelectorWithSearch(modelSearch);

		// Consume call requests or the acknowledged Tether handoff.
		if ($desktopEvent && (!$desktopEvent.handoff || !embedded)) {
			const event = $desktopEvent;
			desktopEvent.set(null);

			if (event.handoff && !event.handoff.isCurrent()) {
				event.handoff.complete('The signed-in account changed. Try again from Tether.');
			} else if (event.type === 'call' || event.type === 'voice') {
				openCallOverlay();
				event.handoff?.complete(
					$showCallOverlay
						? undefined
						: 'Voice mode is unavailable. Check your model, permissions and voice settings in WebUI.'
				);
			} else if (event.type === 'query') {
				if (!messageInput) {
					event.handoff.complete('The WebUI composer is not ready. Send again from Spotlight.');
				} else {
					// Replace the restored new-chat draft; do not mix in its old attachments.
					files = event.data.files.map((file) => ({
						type: 'image',
						url: file.dataUrl,
						name: file.name
					}));
					messageInput.setText(event.data.query ?? '');
					await tick();
					if (event.handoff.isCurrent()) {
						event.handoff.complete();
						void submitHandler(event.data.query ?? '');
					} else {
						event.handoff.complete('The handoff expired. Send again from Spotlight.');
					}
				}
			}
		} else if ($page.url.searchParams.get('q')) {
			const q = $page.url.searchParams.get('q') ?? '';

			messageInput?.setTextHandler(q);
		}

		selectedModels = selectedModels.map((modelId) =>
			modelId.startsWith('agent:') || $models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		await tick();
		messageInput?.focus({ preventScroll: true });
	};

	const loadChat = async (signal = startChatLoad()) => {
		if (chatIdProp && chatIdProp !== $chatId) terminalTabs?.prepareChatSwitch();
		if (chatIdProp && chatIdProp !== $chatId) {
			terminalContext = null;
			history = { messages: {}, currentId: null };
		}
		const initialHistory = { ...history, messages: { ...history.messages } };
		noteChatDebug('loadChat start');
		// chatIdProp is empty for chats started from the home page (URL set via replaceState)
		chatId.set(chatIdProp || $chatId);
		noteChatDebug('loadChat set active chat id');

		if ($temporaryChatEnabled) {
			noteChatDebug('loadChat disabling temporary chat');
			temporaryChatEnabled.set(false);
		}

		const id = $chatId;
		const loadedChat = await getChatById(localStorage.token, id, signal).catch(async (error) => {
			console.error('[note-chat] getChatById failed', {
				chatIdProp,
				activeChatId: $chatId,
				error
			});
			return null;
		});
		if (signal.aborted) return;
		noteChatDebug('getChatById completed', {
			found: !!loadedChat,
			chatId: loadedChat?.id,
			hasChatPayload: !!loadedChat?.chat,
			title: loadedChat?.title
		});

		if (loadedChat) {
			const loadedTags = await getTagsById(localStorage.token, id, signal).catch(async (error) => {
				console.warn('[note-chat] getTagsById failed; continuing without tags', {
					chatIdProp,
					activeChatId: $chatId,
					error
				});
				return [];
			});
			if (signal.aborted) return;
			chat = loadedChat;
			tags = loadedTags;
			noteChatDebug('getTagsById completed', { tagCount: tags?.length ?? 0 });

			const chatContent = chat.chat;
			chatVariables = actorChatPreferences(chat, $user?.id).variables;

			if (chatContent) {
				noteChatDebug('chat payload found', {
					models: chatContent?.models,
					hasHistory: !!chatContent?.history,
					messageCount: Object.keys(chatContent?.history?.messages ?? {}).length
				});

				selectedModels =
					(chatContent?.models ?? undefined) !== undefined
						? chatContent.models
						: [chatContent.models ?? ''];
				if (selectedModels[0]?.startsWith('agent:')) {
					const terminalId = decodeURIComponent(selectedModels[0].split(':')[1]);
					if (isTerminalAvailable(terminalId)) {
						selectedTerminalId.set(terminalId);
						await refreshAgentModels();
					}
					if (signal.aborted) return false;
				}

				// An empty model list is not evidence that the chat's models are gone.
				if ($models.length > 0) {
					selectedModels = selectedModels.filter(
						(modelId) => modelId.startsWith('agent:') || $models.map((m) => m.id).includes(modelId)
					);
				}

				if (!($user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true))) {
					selectedModels = selectedModels.length > 0 ? [selectedModels[0]] : [''];
				}
				if (
					selectedModels.length === 0 ||
					(selectedModels.length === 1 && selectedModels[0] === '')
				) {
					selectedModels = normalizeSelectedModels(selectedModels);
					noteChatDebug('normalized empty selected models after load', { selectedModels });
				}

				chatContent.history ??= convertMessagesToHistory(chatContent.messages);
				// Keep live updates received while the chat and tags were loading.
				for (const [id, message] of Object.entries(history.messages)) {
					if (initialHistory.messages[id] !== message)
						chatContent.history.messages[id] = { ...chatContent.history.messages[id], ...message };
				}
				if (history.currentId !== initialHistory.currentId)
					chat.current_message_id = history.currentId;
				history = chatContent.history;
				if (chat?.current_message_id && history?.messages?.[chat.current_message_id]) {
					history.currentId = chat.current_message_id;
				}

				// Sanitize history: repair orphaned references and structurally-malformed
				// nodes from failed regenerations (#24424, #24157, #20474)
				sanitizeHistory(history);

				chatTitle.set(chatContent.title);

				params = actorChatPreferences(chat, $user?.id).params;
				delete params.note_id;
				reasoningEffortByModel = actorChatPreferences(chat, $user?.id).legacyReasoning;
				if (
					chat.user_id === $user?.id &&
					params.model_controls === undefined &&
					Object.keys(reasoningEffortByModel).length
				) {
					params = {
						...params,
						model_controls: migrateControlSelections(undefined, reasoningEffortByModel, $models)
					};
				}
				seedReasoningEffortFromLastUsed(selectedModels, false);
				chatFiles = structuredClone(chatContent?.files ?? []);

				// Load tasks from chat-level DB field
				chatTasks = chat?.tasks ?? [];
				contextUsageRequestVersion += 1;
				serverContextUsage = contextCompactionMode === 'v2' ? null : (chat?.context_usage ?? null);

				autoScroll = true;
				await tick();
				if (signal.aborted) return;

				// Mark all non-current assistant messages as done
				if (history.currentId) {
					for (const message of Object.values(history.messages)) {
						if (
							message &&
							message.role === 'assistant' &&
							message.id !== history.currentId &&
							message.done !== false
						) {
							message.done = true;
						}
					}
				}

				// Reconcile active tasks with message state:
				// If the response is already done, remaining tasks are just background
				// work (follow-ups, title gen) that shouldn't block the input.
				const activeTaskIds = taskIds;
				const currentMessage = history.currentId ? history.messages[history.currentId] : null;
				const pendingTaskIds = await getTaskIdsByChatId(localStorage.token, id, signal)
					.then((res) => res?.task_ids ?? [])
					.catch((error) => {
						console.warn('[note-chat] getTaskIdsByChatId failed; continuing without tasks', {
							chatIdProp,
							activeChatId: $chatId,
							error
						});
						return [];
					});
				if (signal.aborted) return;
				noteChatDebug('task reconciliation completed', {
					pendingTaskCount: pendingTaskIds.length,
					hasCurrentMessage: !!currentMessage
				});
				const responseComplete = currentMessage?.role === 'assistant' && currentMessage?.done;

				if (taskIds === activeTaskIds) {
					if (pendingTaskIds.length > 0 && !responseComplete) {
						taskIds = pendingTaskIds;
					} else {
						taskIds = null;
						// No active tasks and message incomplete → generation was interrupted
						if (pendingTaskIds.length === 0) {
							for (const message of Object.values(history.messages)) {
								if (
									message?.role === 'assistant' &&
									(message.user_id ?? chat?.user_id) === $user?.id &&
									!message.done &&
									!messageHasPendingAskUser(message)
								) {
									message.done = true;
									history = history;
								}
							}
						}
					}
				}

				await tick();
				// Reloads also follow manual compaction. Query with the current
				// browser descriptor so generic embedded usage cannot replace it.
				if (
					$chatId === chat?.id &&
					history.currentId &&
					contextUsageModelId &&
					contextCompactionEnabled
				) {
					await refreshContextUsage($chatId, contextUsageModelId, history.currentId);
				}

				if (signal.aborted) return;
				if (loading) {
					if (chatIdProp && !$temporaryChatEnabled) updateLastReadAt(chatIdProp);

					const lastMessage = history.currentId ? history.messages[history.currentId] : null;
					if (!lastMessage || lastMessage.role !== 'assistant' || lastMessage.done) {
						await processNextInQueue(chatIdProp);
					}
					if (signal.aborted) return;
					const restored = await restoreChatInput(
						sessionStorage.getItem(`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`)
					);
					if (signal.aborted) return;
					await setDefaults(restored);
					if (signal.aborted) return;
					loading = false;
					await tick();
					if (signal.aborted) return;
					messageInput?.focus({ preventScroll: true });
				}
				return true;
			} else {
				console.warn('[note-chat] chat response missing chat payload', {
					chatIdProp,
					activeChatId: $chatId,
					chat
				});
			}
		}
		console.warn('[note-chat] no chat returned from getChatById', {
			chatIdProp,
			activeChatId: $chatId
		});
		chat = null;
		if (!embedded) await goto('/');
		else loading = false;
		return false;
	};

	const scrollToBottom = async (behavior = 'auto') => {
		await tick();
		if (messagesContainerElement) {
			messagesContainerElement.scrollTo({
				top: messagesContainerElement.scrollHeight,
				behavior
			});
			lastScrollTop = messagesContainerElement.scrollTop;

			// Re-scroll over two frames as content-visibility resolves estimated message heights.
			requestAnimationFrame(() => {
				if (messagesContainerElement && autoScroll) {
					messagesContainerElement.scrollTo({
						top: messagesContainerElement.scrollHeight,
						behavior
					});
					lastScrollTop = messagesContainerElement.scrollTop;
					requestAnimationFrame(() => {
						if (messagesContainerElement && autoScroll) {
							messagesContainerElement.scrollTo({
								top: messagesContainerElement.scrollHeight,
								behavior
							});
							lastScrollTop = messagesContainerElement.scrollTop;
						}
					});
				}
			});
		}
	};

	$: if (
		!loading &&
		(!fullWidthTabs || mainChatActive) &&
		autoScroll &&
		!$page.url.searchParams.has('message')
	) {
		scrollToBottom();
	}

	const scrollToTop = async () => {
		await messagesRef?.scrollToTop();
	};

	const shouldAutoScrollResponse = () =>
		autoScroll && ($settings?.scrollOnResponseGeneration ?? true);

	let scrollRAF = null;
	let contentsRAF: ReturnType<typeof setTimeout> | undefined;
	const autoScrollToBottom = () => {
		if (!shouldAutoScrollResponse()) return;

		if (!scrollRAF) {
			scrollRAF = requestAnimationFrame(async () => {
				scrollRAF = null;
				if (shouldAutoScrollResponse()) await scrollToBottom();
			});
		}
	};

	const hasPendingQueuedSubmission = (queue: QueuedSubmission | null) =>
		!!queue && pendingWebSearchSubmission?.queue === queue;
	const settleQueuedSubmission = (
		queue: QueuedSubmission | null,
		submitted: boolean,
		error?: unknown
	) => {
		if (!queue) return;
		const ids = new Set(queue.messageIds);
		chatRequestQueues.update((queues) => ({
			...queues,
			[queue.chatId]: submitted
				? (queues[queue.chatId] ?? []).filter((entry) => !ids.has(entry.id))
				: (queues[queue.chatId] ?? []).map((entry) =>
						ids.has(entry.id)
							? {
									...entry,
									error: error
										? String(error)
										: $i18n.t('Message could not be sent. Edit the queued message before sending.')
								}
							: entry
					)
		}));
		if (submitted) for (const id of ids) queuedSubmissionGuards.delete(id);
	};
	const submitQueuedMessages = async (
		targetChatId: string,
		entries: { id: string; prompt: string; files: any[] }[]
	) => {
		const queue: QueuedSubmission = {
			chatId: targetChatId,
			messageIds: entries.map((entry) => entry.id)
		};
		if (
			entries.some(
				(entry) => queuedSubmissionGuards.has(entry.id) && !queuedSubmissionGuards.get(entry.id)!()
			)
		)
			throw new Error(
				$i18n.t('The chat, account, or model changed. Edit the queued message before sending.')
			);
		const result = await submitHandler(entries.map((entry) => entry.prompt).join('\n\n'), {
			attachments: entries.flatMap((entry) => entry.files),
			fromQueue: true,
			queuedSubmission: queue
		});
		if (result.status === 'submitted') settleQueuedSubmission(queue, true);
		else if (!hasPendingQueuedSubmission(queue))
			throw new Error(
				$i18n.t('Message could not be sent. Edit the queued message before sending.')
			);
	};
	let processingQueueChats = new Set<string>();

	const processNextInQueue = async (targetChatId: string) => {
		if (
			targetChatId !== $chatId ||
			pendingWebSearchSubmission?.queue?.chatId === targetChatId ||
			processingQueueChats.has(targetChatId) ||
			stoppingChats.has(targetChatId)
		)
			return;

		const queue = $chatRequestQueues[targetChatId];
		if (!queue || queue.length === 0 || queue[0].error) return;
		if (temporaryController.chatId === targetChatId) {
			try {
				if (!(await temporaryController.waitForSettled()) || targetChatId !== $chatId) return;
			} catch (error) {
				reportTemporaryError(error);
				return;
			}
			if (
				processingQueueChats.has(targetChatId) ||
				stoppingChats.has(targetChatId) ||
				queue !== $chatRequestQueues[targetChatId]
			)
				return;
		}
		const lastMessage = history.currentId ? history.messages[history.currentId] : null;
		if (
			(lastMessage && lastMessage.role === 'assistant' && !lastMessage.done) ||
			queue.some((m) =>
				(m.files ?? []).some((file) => ['uploading', 'error'].includes(file.status))
			)
		) {
			return;
		}

		processingQueueChats.add(targetChatId);
		const boundary = queue.findIndex((entry) =>
			entry.files.some((file: any) => file.type === 'filesystem')
		);
		const queuedMessages =
			embedded || boundary === 0
				? queue.slice(0, 1)
				: boundary < 0
					? [...queue]
					: queue.slice(0, boundary);
		const queuedMessageIds = new Set(queuedMessages.map((m) => m.id));
		try {
			await submitQueuedMessages(targetChatId, queuedMessages);
		} catch (error) {
			console.error(error);
			settleQueuedSubmission(
				{ chatId: targetChatId, messageIds: queuedMessages.map((entry) => entry.id) },
				false,
				error
			);
		} finally {
			processingQueueChats.delete(targetChatId);
		}
	};

	const sendQueuedMessageNow = async (id) => {
		if (processingQueueChats.has($chatId) || pendingWebSearchSubmission?.queue?.chatId === $chatId)
			return;

		const queue = $chatRequestQueues[$chatId] ?? [];
		const item = queue.find((m) => m.id === id);
		if (!item || (item.files ?? []).some((file) => ['uploading', 'error'].includes(file.status))) {
			return;
		}

		const targetChatId = $chatId;
		processingQueueChats.add(targetChatId);
		let stopped = false;
		try {
			stopped = await stopResponse(false);
			if (!stopped || $chatId !== targetChatId) return;
			await tick();
			if ($chatId !== targetChatId) return;
			await submitQueuedMessages(targetChatId, [item]);
		} catch (error) {
			toast.error(String(error));
			settleQueuedSubmission({ chatId: targetChatId, messageIds: [item.id] }, false, error);
		} finally {
			processingQueueChats.delete(targetChatId);
			// Completion can arrive before submitPrompt returns, while the queue is locked.
			if (stopped && $chatId === targetChatId) {
				await processNextInQueue(targetChatId);
			}
		}
	};

	const editQueuedMessage = async (id) => {
		queuedSubmissionGuards.delete(id);
		const queue = $chatRequestQueues[$chatId] ?? [];
		const item = queue.find((m) => m.id === id);
		if (!item) return;

		chatRequestQueues.update((q) => ({
			...q,
			[$chatId]: queue.filter((m) => m.id !== id)
		}));
		files = item.files;
		messageInput?.setText(item.prompt);

		await processNextInQueue($chatId);
	};

	const deleteQueuedMessage = async (id) => {
		queuedSubmissionGuards.delete(id);
		const queue = $chatRequestQueues[$chatId] ?? [];
		chatRequestQueues.update((q) => ({
			...q,
			[$chatId]: queue.filter((m) => m.id !== id)
		}));

		await processNextInQueue($chatId);
	};

	const chatCompletedHandler = async (_chatId, modelId, responseMessageId, messages) => {
		// Backend handles outlet filters and persistence inline.
		// Just refresh the sidebar chat list.
		if ($chatId == _chatId && !$temporaryChatEnabled) {
			await refreshChatList(localStorage.token);
		}
	};

	const chatActionHandler = async (_chatId, actionId, modelId, responseMessageId, event = null) => {
		const messages = createMessagesList(history, responseMessageId);
		// Actions can also update this message through events.
		const original = structuredClone(history.messages[responseMessageId]);

		const res = await chatAction(localStorage.token, actionId, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: getOutputText(m.output) || m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.sources ? { sources: m.sources } : {})
			})),
			...(event ? { event: event } : {}),
			model_item: $models.find((m) => m.id === modelId),
			chat_id: _chatId,
			session_id: $socket?.id,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			if ($chatId === _chatId && history.messages[responseMessageId]) {
				history.messages[responseMessageId].error = { content: error };
			}
			return null;
		});

		if ($chatId !== _chatId) return;

		let changed = !equal(original, history.messages[responseMessageId]);
		if (res?.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				const updated = {
					...history.messages[message.id],
					...(history.messages[message.id].content !== message.content
						? { originalContent: history.messages[message.id].content }
						: {}),
					...(typeof message.content === 'string' && history.messages[message.id].output?.length
						? { output: setOutputText(history.messages[message.id].output, message.content) }
						: {}),
					...message
				};
				if (!equal(history.messages[message.id], updated)) {
					history.messages[message.id] = updated;
					changed = true;
				}
			}
		}

		if (changed && !$temporaryChatEnabled) {
			chat = await updateChatById(localStorage.token, _chatId, {
				models: selectedModels,
				messages: createMessagesList(history, responseMessageId),
				history: history,
				files: chatFiles
			});

			await refreshChatList(localStorage.token);
		}
	};

	const getChatEventEmitter = async (modelId: string, chatId: string = '') => {
		return setInterval(() => {
			$socket?.emit('usage', {
				action: 'chat',
				model: modelId,
				chat_id: chatId
			});
		}, 1000);
	};

	const createMessagePair = async (userPrompt) => {
		if (isShared) return;
		messageInput?.setText('');
		if (selectedModels.length === 0) {
			toast.error($i18n.t('Model not selected'));
		} else {
			const userMessageId = uuidv4();

			const responseMessages = [];
			for (const [modelIdx, modelId] of selectedModels.entries()) {
				const model = $models.filter((m) => m.id === modelId).at(0);

				if (model) {
					const responseMessageId = uuidv4();
					responseMessages.push({
						id: responseMessageId,
						parentId: userMessageId,
						childrenIds: [],
						role: 'assistant',
						user_id: $user?.id,
						content: `[RESPONSE] ${responseMessageId}`,
						done: true,

						model: modelId,
						modelName: model.name ?? model.id,
						modelIdx: modelIdx,
						timestamp: Math.floor(Date.now() / 1000)
					});
				}
			}

			const lastResponseMessage = responseMessages.at(-1);
			if (!lastResponseMessage) {
				toast.error($i18n.t('Model not found'));
				return;
			}

			const messages = createMessagesList(history, history.currentId);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: responseMessages.map((m) => m.id),
				role: 'user',
				user_id: $user?.id,
				user: { id: $user?.id, name: $user?.name },
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000),
				models: selectedModels
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				history.messages[parentMessage.id] = parentMessage;
			}
			history.messages[userMessageId] = userMessage;
			for (const responseMessage of responseMessages) {
				history.messages[responseMessage.id] = responseMessage;
			}

			history.currentId = lastResponseMessage.id;

			await tick();

			if (autoScroll) {
				scrollToBottom();
			}

			if (!$chatId) {
				await initChatHandler(history);
			} else {
				await saveChatHandler($chatId, history);
			}
		}
	};

	const addMessages = async ({ modelId, parentId, messages }) => {
		const model = $models.filter((m) => m.id === modelId).at(0);

		let parentMessage = history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			let messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: 0,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		history.currentId = currentParentId;
		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		if (!$chatId) {
			await initChatHandler(history);
		} else {
			await saveChatHandler($chatId, history);
		}
	};

	const responseCompletionEventHandler = (data, message) => {
		message.output = applyResponseStreamEvent(message.output ?? [], data);

		if (data?.type === 'response.output_text.delta') {
			const value = data.delta ?? '';
			if (!(message.content == '' && value == '\n')) {
				message.content += value;

				if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
					navigator.vibrate(5);
				}
				dispatchCallOverlayAudio(message);
			}
		} else if (data?.type === 'response.completed' || data?.type?.endsWith('.done')) {
			message.content = getOutputText(message.output) || message.content;
		}

		history.messages[message.id] = message;
		history = history;
	};

	const chatCompletionEventHandler = async (data, message, chatId, shared = false) => {
		const { id, done, choices, content, output, sources, selected_model_id, error, usage } = data;

		if (error) {
			if (shared) message.error = error;
			else handleOpenAIError(error, message);
		} else if (error === null) {
			// The backend explicitly confirmed recovery in a later provider round.
			message.error = null;
			if (done === false) message.done = false;
		}

		// Store raw OR-aligned output items from backend
		if (output) {
			message.output = output;
			message.content = getOutputText(output);
			if (
				data.type === 'response.output_text.delta' &&
				!shared &&
				navigator.vibrate &&
				$settings?.hapticFeedback
			) {
				navigator.vibrate(5);
			}
			if (!shared) dispatchCallOverlayAudio(message);
		}

		if (sources && !message?.sources) {
			message.sources = sources;
		}

		if (choices && !output) {
			if (choices[0]?.message?.content) {
				// Non-stream response
				message.content += choices[0]?.message?.content;
				if (!shared) dispatchCallOverlayAudio(message);
			} else {
				// Stream response
				let value = choices[0]?.delta?.content ?? '';
				if (message.content == '' && value == '\n') {
					console.log('Empty response');
				} else {
					message.content += value;

					if (!shared && navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
						navigator.vibrate(5);
					}
					if (!shared) dispatchCallOverlayAudio(message);
				}
			}
		}

		if (content && !output) {
			// REALTIME_CHAT_SAVE is disabled
			message.content = content;

			if (!shared && navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}
			if (!shared) dispatchCallOverlayAudio(message);
		}

		if (selected_model_id) {
			message.selectedModelId = selected_model_id;
			message.arena = true;
		}

		if (usage) {
			message.usage = usage;
			captureUsageCheckpoint(message);
		}

		history.messages[message.id] = message;
		history = history;

		if (done) {
			message.done = true;
			message.meta = { ...message.meta, response_ended_at: Date.now() / 1000 };
			if ($contextCompactions[message.id] === 'in_progress') clearContextCompaction(message.id);
			if (shared) return;
			if (message.error) {
				clearContextCompaction(message.id);
				bridge?.update();
				await processNextInQueue(chatId);
				return;
			}
			const visibleContent =
				getOutputText(message?.output) || removeAllDetails(message?.content ?? '');

			if ($settings.responseAutoCopy) {
				copyToClipboard(visibleContent);
			}

			if ($settings.responseAutoPlayback && !$showCallOverlay && !bridge?.connected) {
				await tick();
				document.getElementById(`speak-button-${message.id}`)?.click();
			}

			// Emit chat event for TTS (only when call overlay is active)
			dispatchCallOverlayAudio(message, true);
			eventTarget.dispatchEvent(
				new CustomEvent('chat:finish', {
					detail: {
						id: message.id,
						content: visibleContent
					}
				})
			);

			history.messages[message.id] = message;

			await tick();
			if (shouldAutoScrollResponse()) {
				scrollToBottom();
			}

			// Fire-and-forget: run chatCompletedHandler for background work
			// (outlet filters, chat save, title gen, follow-ups, tags)
			// without blocking the user from sending new messages.
			chatCompletedHandler(
				chatId,
				message.model,
				message.id,
				createMessagesList(history, message.id)
			);

			// Process next queued request if any
			await processNextInQueue(chatId);
		}

		bridge?.update();
	};

	// Chat functions

	const submitPrompt = async (
		inputContent: string,
		inputFiles: any[],
		bridgeRequest: { userMessageId: string; modelId: string } | null = null
	) => {
		if (selectedModels.includes('')) throw new Error($i18n.t('Model not selected'));
		const _files = structuredClone(inputFiles);

		chatFiles.push(
			..._files.filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'folder', 'collection'].includes(item.type) ||
					(item.type === 'file' && !isRasterImageContentType(item?.content_type))
			)
		);
		chatFiles = chatFiles.filter(
			// One copy per attachment; the model does not need an encore.
			(item, index, array) => array.findIndex((i) => equal(i, item)) === index
		);

		// Create user message
		let userMessageId = bridgeRequest?.userMessageId ?? uuidv4();
		let userMessage = {
			id: userMessageId,
			parentId: history.currentId ?? null,
			childrenIds: [],
			role: 'user',
			user: { id: $user?.id, name: $user?.name },
			user_id: $user?.id,
			content: inputContent,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000), // Unix epoch
			models: selectedModels
		};

		// A transcribed Bridge message already exists in the same history.
		const existingUserMessage = history.messages[userMessageId];
		if (existingUserMessage) userMessage = { ...existingUserMessage, files: userMessage.files };
		history.messages[userMessageId] = userMessage;

		// Append messageId to childrenIds of parent message
		if (!existingUserMessage && history.currentId !== null) {
			history.messages[history.currentId].childrenIds.push(userMessageId);
		}

		history.currentId = userMessageId;

		// focus on chat input (skip during voice call to avoid triggering mobile keyboard)
		if (!$showCallOverlay) {
			messageInput?.focus({ preventScroll: true });
		}

		saveSessionSelectedModels();

		return await sendMessage(history, userMessageId, {
			modelId: bridgeRequest?.modelId ?? null
		});
	};

	const handleManualCompact = async () => {
		if (
			isShared ||
			(($temporaryChatEnabled || isTemporaryChatId($chatId)) && !temporaryController.identity)
		)
			return;
		if (!contextCompactionEnabled) {
			toast.message($i18n.t('Context compaction is disabled'));
			return;
		}

		if (!$chatId || !history?.currentId) {
			toast.message($i18n.t('No chat to compact'));
			return;
		}

		const currentMessage = history.messages?.[history.currentId];
		if (
			generating ||
			taskIds?.length ||
			(currentMessage?.role === 'assistant' && !currentMessage.done)
		) {
			toast.warning($i18n.t('Wait for the current response to finish before compacting.'));
			return;
		}

		const model = atSelectedModel?.id ?? selectedModels.find((modelId) => modelId);
		if (!model) return;
		// Preserve the former toast translation for i18n extraction.
		// const toastId = toast.loading($i18n.t('Compacting context...'));
		const compactChatId = $chatId;
		const compactMessageId = history.currentId;
		if ($contextCompactions[compactMessageId] === 'in_progress') return;
		$contextCompactions = { ...$contextCompactions, [compactMessageId]: 'in_progress' };

		try {
			if (temporaryController.chatId === compactChatId) {
				await temporaryController.prepare(history, compactMessageId, params, chatFiles);
				temporaryScope = temporaryController.identity;
			}
			const result =
				temporaryController.chatId === compactChatId
					? await temporaryController.compact(
							compactMessageId,
							model,
							$models.find((item) => item.id === model)
						)
					: await compactChatById(
							localStorage.token,
							compactChatId,
							model,
							$models.find((item) => item.id === model),
							$socket?.id
						);
			if ($chatId !== compactChatId) return;
			serverContextUsage = result?.usage ?? result?.context_usage ?? serverContextUsage;
			if (temporaryController.chatId === compactChatId && serverContextUsage)
				serverContextUsage = { ...serverContextUsage, branch_id: compactMessageId };

			if (!result?.compacted) {
				const skippedReason =
					result?.reason === 'too_short'
						? $i18n.t('Chat is too short to compact')
						: result?.reason === 'empty'
							? $i18n.t('No chat to compact')
							: result?.reason === 'disabled'
								? $i18n.t('Context compaction is disabled')
								: $i18n.t('Nothing to compact');
				toast.message(skippedReason);
			}

			if (temporaryController.chatId === compactChatId) await temporaryController.reconcile();
			else await loadChat();
			clearContextCompaction(compactMessageId);
		} catch (error) {
			if ($chatId === compactChatId) {
				$contextCompactions = {
					...$contextCompactions,
					[compactMessageId]: {
						error: String(error?.detail ?? error?.message ?? $i18n.t('Context compaction failed'))
					}
				};
			}
		} finally {
			messageInput?.focus({ preventScroll: true });
		}
	};

	const handleStatusCommand = () => {
		messageInput?.showStatus();
		messageInput?.focus({ preventScroll: true });
	};

	const handleModelCommand = (modelId = '') => {
		if (!modelId) {
			const currentModels = (atSelectedModel?.id ? [atSelectedModel.id] : selectedModels).filter(
				Boolean
			);
			toast.message(
				currentModels.length
					? `Current model: ${currentModels.join(', ')}`
					: $i18n.t('Model not selected')
			);
			messageInput?.setText('');
			prompt = '';
			messageInput?.focus({ preventScroll: true });
			return;
		}

		const model = $models.find((model) => model.id === modelId);
		if (!model) {
			toast.error(`Model not found: ${modelId}`);
			messageInput?.setText('');
			prompt = '';
			messageInput?.focus({ preventScroll: true });
			return;
		}

		selectChatModels([model.id]);
		toast.success(`Model switched to: ${model.id}`);
		messageInput?.setText('');
		prompt = '';
		messageInput?.focus({ preventScroll: true });
	};

	const handleForkChat = async (messageId: string | null = null) => {
		if (!$chatId || $temporaryChatEnabled || !history?.currentId) {
			toast.message($i18n.t('No chat to fork'));
			return;
		}
		if (!($user?.role === 'admin' || ($user?.permissions?.chat?.import ?? true))) {
			toast.error($i18n.t('Access prohibited'));
			return;
		}

		const forkedMessage = history.messages?.[messageId ?? history.currentId];
		if (
			generating ||
			taskIds?.length ||
			(forkedMessage?.role === 'assistant' && !forkedMessage.done)
		) {
			toast.warning($i18n.t('Wait for the current response to finish before forking.'));
			return;
		}

		const toastId = toast.loading($i18n.t('Forking chat...'));

		try {
			const result = await forkChatById(
				localStorage.token,
				$chatId,
				messageId ?? history.currentId
			);

			if (result?.id) {
				if (!embedded) {
					await goto(`/c/${result.id}`);
					await refreshChatList(localStorage.token, { refreshPinned: true });
				}
				toast.success($i18n.t('Chat forked'), { id: toastId });
			} else {
				toast.error($i18n.t('Failed to fork chat'), { id: toastId });
			}
		} catch (error) {
			toast.error(`${error}`, { id: toastId });
		} finally {
			messageInput?.focus({ preventScroll: true });
		}
	};

	const clearCommandInput = () => {
		messageInput?.setText('');
		prompt = '';
	};

	let terminalTabs: ChatTabs;
	let fullWidthTabs = false;
	let mainChatActive = false;
	let mainChatStyle = '';
	let mainChatGroupId = '';
	let tabTitleEditor: import('svelte').Snippet | undefined;
	let tabActions: import('svelte').Snippet | undefined;
	let tabBarActions: import('svelte').Snippet | undefined;

	let initializedTerminalChatId: string | null = null;
	const initializeTerminalDirectory = async (id: string, context: TerminalContext | null) => {
		if (initializedTerminalChatId === id) return;
		if (!$config?.features?.enable_tool_servers || !context?.currentDirectory) return;
		const userSettings: any = $settings;
		const connection = resolveTerminalConnection(
			context.terminalId,
			$terminalServers ?? [],
			$config?.features?.enable_direct_integrations ? (userSettings?.terminalServers ?? []) : [],
			localStorage.token
		);
		if (!connection) throw new Error($i18n.t('Terminal Not Found'));
		const credential = connection.system
			? temporaryContext.terminalCredential(localStorage.token, id)
			: connection.key;
		const result = await setCwd(connection.baseUrl, credential, context.currentDirectory, id);
		if (!result?.cwd)
			throw new Error(
				$i18n.t('Working directory is unavailable. Check the terminal connection and path.')
			);
		initializedTerminalChatId = id;
	};

	const getChatId = async (_history: typeof history, inputFiles = files) => {
		let _chatId = $chatId;
		const terminalId =
			inputFiles.find((file) => file.type === 'filesystem')?.terminal_id ?? $selectedTerminalId;
		const context =
			$config?.features?.enable_tool_servers &&
			!Object.keys(_history.messages).length &&
			terminalContext &&
			terminalContext.terminalId === terminalId
				? terminalContext
				: null;
		// Empty embedded drafts create their backing chat only when the first message is sent.
		if (!_chatId) {
			if (embedded && onCreateEmbeddedChat) {
				const createdChat = await onCreateEmbeddedChat();
				if (!createdChat?.id) {
					toast.error($i18n.t('Failed to create chat'));
					return null;
				}
				if (history !== _history) return null;

				chat = createdChat;
				_chatId = createdChat.id;
				loadedChatIdProp = _chatId;
				await chatId.set(_chatId);
				await chatTitle.set(createdChat?.chat?.title ?? createdChat?.title ?? $i18n.t('Chat'));

				params = structuredClone(createdChat?.chat?.params ?? {});
				delete params.note_id;
				chatFiles = mergeFiles(chatFiles, createdChat?.chat?.files ?? []);
				await onSelectEmbeddedChat?.(_chatId);
			} else if ($temporaryChatEnabled) {
				_chatId =
					contextCompactionMode === 'v2'
						? await ensureTemporaryScope()
						: createTemporaryChatId($socket?.id);
				await chatId.set(_chatId);
			}
			if (!_chatId && (context?.currentDirectory || terminalTabs?.hasTerminals())) {
				_chatId = await initChatHandler(_history);
			}
			await tick();
		}
		if (history !== _history) return null;
		if (_chatId && !temporaryController.identity) await terminalTabs?.bindTerminalChat(_chatId);
		if (history !== _history || $chatId !== _chatId) return null;
		// Also covers temporary/embedded chats and retries after a failed initialization.
		if (_chatId) await initializeTerminalDirectory(_chatId, context);
		if (history !== _history || $chatId !== _chatId) return null;
		return _chatId;
	};

	const submitHandler = async (
		userPrompt: string,
		{
			_raw = false,
			bridge: bridgeRequest = null,
			attachments = null,
			fromQueue = false,
			queuedSubmission = null
		}: {
			_raw?: boolean;
			bridge?: { userMessageId: string; modelId: string } | null;
			attachments?: any[] | null;
			fromQueue?: boolean;
			queuedSubmission?: QueuedSubmission | null;
		} = {}
	): Promise<BridgeSubmission> => {
		if (embedded && onBeforeNoteSubmit) {
			try {
				await onBeforeNoteSubmit();
			} catch (error) {
				toast.error(String(error));
				return { status: 'rejected' };
			}
		}
		if (processing === 'uploading') return { status: 'deferred' };
		const submissionFiles = attachments ?? files;
		const isCurrent = captureSubmissionGuard();
		const clearSubmissionInput = () => {
			if (attachments !== null) return;
			messageInput?.setText('');
			prompt = '';
			files = [];
		};
		const enqueueSubmission = () => {
			const id = uuidv4();
			// The queue key binds its chat, including authorized adoption of a new saved ID.
			const validContext = captureSubmissionGuard();
			queuedSubmissionGuards.set(id, () => validContext($chatId));
			chatRequestQueues.update((q) => ({
				...q,
				[$chatId]: [
					...(q[$chatId] ?? []),
					{ id, prompt: userPrompt, files: structuredClone(submissionFiles) }
				]
			}));
			clearSubmissionInput();
		};
		emitTyping(false);
		if (bridgeRequest && (selectedModelIds.length !== 1 || !bridgeRequest.modelId))
			return { status: 'rejected' };

		const _selectedModels = selectedModels.map((modelId) =>
			modelId.startsWith('agent:') || $models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		if (!equal(selectedModels, _selectedModels)) {
			selectedModels = _selectedModels;
		}

		if (attachments === null) {
			if (String(userPrompt).trim() === '/compact') {
				clearCommandInput();
				await handleManualCompact();
				return { status: 'rejected' };
			}
			if (String(userPrompt).trim() === '/status') {
				clearCommandInput();
				handleStatusCommand();
				return { status: 'rejected' };
			}
			if (String(userPrompt).trim() === '/comments') {
				clearCommandInput();
				messageInput?.openComments();
				return { status: 'rejected' };
			}
			const sideCommand = String(userPrompt)
				.trim()
				.match(/^\/(?:side|btw)(?:\s+([\s\S]*))?$/i);
			if (sideCommand && !sideChatAllowed) {
				toast.error($i18n.t('You do not have permission to use side chat.'));
				return { status: 'rejected' };
			}
			if (sideCommand && !embedded && $chatId && history.currentId) {
				if (selectedModels.length !== 1 || !selectedModelIds[0]) {
					toast.error($i18n.t('Select only one model to open a side chat.'));
					return { status: 'rejected' };
				}
				clearCommandInput();
				sideChatRequest.set({ parentId: $chatId, prompt: sideCommand[1] ?? '' });
				showTabs.set('side');
				return { status: 'rejected' };
			}
			if (String(userPrompt).trim() === '/fork') {
				clearCommandInput();
				await handleForkChat();
				return { status: 'rejected' };
			}
			const modelCommandMatch = String(userPrompt)
				.trim()
				.match(/^\/model(?:\s+([\s\S]+))?$/);
			if (modelCommandMatch) {
				handleModelCommand(modelCommandMatch[1]?.trim() ?? '');
				return { status: 'rejected' };
			}
		}

		if (pendingOAuthTools.length > 0) {
			toast.warning($i18n.t('Please connect all required integrations before sending a message'));
			return { status: 'rejected' };
		}
		if (userPrompt === '' && submissionFiles.length === 0) {
			toast.error($i18n.t('Please enter a prompt'));
			return { status: 'rejected' };
		}
		if (agentModelId && (offlineAgent || $temporaryChatEnabled || selectedModels.length !== 1)) {
			toast.error(
				offlineAgent
					? $i18n.t('Agent is offline. Reconnect its terminal or choose another model.')
					: $i18n.t('Agents require a saved chat with one model.')
			);
			return { status: 'rejected' };
		}
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return { status: 'rejected' };
		}
		const form = getChatVariablesForm(selectedModelIds, chatVariables, $models);
		if (form.conflicts.length > 0) {
			showChatVariablesModal = true;
			toast.error($i18n.t('Chat Variables have conflicting model definitions'));
			return { status: 'rejected' };
		}
		if (form.missing || form.empty) {
			showChatVariablesModal = true;
			return { status: 'deferred' };
		}

		if (
			($config?.file?.max_count ?? null) !== null &&
			submissionFiles.length + chatFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return { status: 'rejected' };
		}

		if (
			$config?.features?.enable_web_search_confirmation &&
			webSearchActive &&
			!webSearchConfirmed
		) {
			if (
				pendingWebSearchSubmission?.queue &&
				pendingWebSearchSubmission.queue !== queuedSubmission
			)
				settleQueuedSubmission(pendingWebSearchSubmission.queue, false);
			pendingWebSearchPrompt = bridgeRequest ? null : (userPrompt ?? '');
			pendingWebSearchSubmission =
				!bridgeRequest && attachments !== null
					? {
							files: structuredClone(submissionFiles),
							queue: queuedSubmission,
							isCurrent: () =>
								isCurrent() &&
								(!queuedSubmission ||
									queuedSubmission.messageIds.every((id) =>
										($chatRequestQueues[queuedSubmission.chatId] ?? []).some(
											(entry) => entry.id === id
										)
									))
						}
					: null;
			openWebSearchConfirm();
			return { status: 'deferred' };
		}

		const currentHistory = history;
		processing = 'uploading';
		try {
			let _chatId = await getChatId(currentHistory, submissionFiles);
			if (_chatId === null || !isCurrent(_chatId)) return { status: 'rejected' };
			if (
				attachments === null &&
				submissionFiles.some((file) => file.status === 'pending' && file.file instanceof File)
			) {
				if (!_chatId) _chatId = await initChatHandler(currentHistory);
				if (!isCurrent(_chatId)) return { status: 'rejected' };
				if (!(await messageInput?.uploadPendingFiles(_chatId))) return { status: 'rejected' };
			}
			if (!isCurrent(_chatId)) return { status: 'rejected' };
		} catch (error) {
			toast.error(`${error}`);
			return { status: 'rejected' };
		} finally {
			processing = '';
		}

		const isReady = captureSubmissionGuard();
		if (
			($chatRequestQueues[$chatId] ?? []).some((m) =>
				(m.files ?? []).some((file) => ['uploading', 'error'].includes(file.status))
			) ||
			(submissionFiles.length > 0 &&
				submissionFiles.some((file) => ['uploading', 'error'].includes(file.status)))
		) {
			if (bridgeRequest || fromQueue) return { status: 'deferred' };
			enqueueSubmission();
			return { status: 'rejected' };
		}

		// Check if the assistant is still generating the main response
		// (don't block on background tasks like title gen, follow-ups, tags)
		const lastMessage = history.currentId ? history.messages[history.currentId] : null;
		const isGenerating = bridgeRequest
			? createMessagesList(history, bridgeRequest.userMessageId).some(
					(message) => message.role === 'assistant' && !message.done
				)
			: lastMessage && lastMessage.role === 'assistant' && !lastMessage.done;

		if (isGenerating) {
			if (bridgeRequest || fromQueue) return { status: 'deferred' };
			if ($settings?.enableMessageQueue ?? true) {
				enqueueSubmission();
				return { status: 'rejected' };
			} else {
				// Interrupt: stop current generation and proceed
				if (!(await stopResponse(false))) return { status: 'rejected' };
				await tick();
			}
		}

		if (!isReady()) return { status: 'rejected' };
		const _files = structuredClone(submissionFiles);
		clearSubmissionInput();
		return (
			(await (bridgeRequest
				? submitPrompt(userPrompt, _files, bridgeRequest)
				: submitPrompt(userPrompt, _files))) ?? { status: 'rejected' }
		);
	};

	const sendMessage = async (
		_history,
		parentId: string,
		{
			messages = null,
			modelId = null,
			modelIdx = null,
			regenerationPrompt = null,
			regenerationMessageId = null
		}: {
			messages?: any[] | null;
			modelId?: string | null;
			modelIdx?: number | null;
			regenerationPrompt?: string | null;
			regenerationMessageId?: string | null;
		} = {}
	) => {
		const parent = _history.messages[parentId];
		if (parent?.files?.some((file: any) => file.type === 'filesystem')) {
			const attached = parent.files.filter((file: any) => file.type === 'filesystem');
			const terminal = attached.find((file: any) => file.terminal_id)?.terminal_id;
			if (
				attached.some(
					(file: any) =>
						(file.terminal_id && file.terminal_id !== terminal) ||
						(file.session_id && file.session_id !== $chatId)
				)
			)
				throw new Error($i18n.t('Attached files must belong to this chat and the same terminal.'));
			if (terminal && !isTerminalAvailable(terminal))
				throw new Error($i18n.t('The attached file’s terminal is unavailable.'));
		}
		if (autoScroll) {
			scrollToBottom();
		}

		let _chatId = JSON.parse(JSON.stringify($chatId));
		_history = structuredClone(_history);

		const responseMessageIds: Record<PropertyKey, string> = {};
		if (!modelId && atSelectedModel) {
			selectedModels = [atSelectedModel.id];
			atSelectedModel = undefined;
		}
		const selectedModelIds = modelId ? [modelId] : selectedModels;
		if (!modelId && history.messages[parentId]) {
			history.messages[parentId].models = [...selectedModelIds];
		}

		// Use a message_ids array with modelIdx to preserve separate columns for duplicate models.
		const messageIdsList: Array<{ model_id: string; message_id: string; modelIdx: number }> = [];
		for (const [_modelIdx, modelId] of selectedModelIds.entries()) {
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (model) {
				let responseMessageId = uuidv4();
				let responseMessage = {
					parentId: parentId,
					id: responseMessageId,
					childrenIds: [],
					role: 'assistant',
					user_id: $user?.id,
					content: '',
					done: false,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: modelIdx ? modelIdx : _modelIdx,
					timestamp: Math.floor(Date.now() / 1000) // Unix epoch
				};

				// Add message to history and Set currentId to messageId
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null && history.messages[parentId]) {
					history.messages[parentId].childrenIds = [
						...history.messages[parentId].childrenIds,
						responseMessageId
					];
				}

				responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`] = responseMessageId;
				messageIdsList.push({
					model_id: modelId,
					message_id: responseMessageId,
					modelIdx: modelIdx ? modelIdx : _modelIdx
				});
			}
		}
		history = history;

		await tick();

		// Re-clone history so sendMessageSocket gets the response messages we just added
		_history = structuredClone(history);

		// Vision capability check
		for (const mid of selectedModelIds) {
			const model = $models.filter((m) => m.id === mid).at(0);
			if (model) {
				const hasImages = createMessagesList(_history, parentId).some((message) =>
					message.files?.some(
						(file) => file.type === 'image' || isRasterImageContentType(file?.content_type)
					)
				);

				if (
					hasImages &&
					!(model.info?.meta?.capabilities?.vision ?? true) &&
					!imageGenerationEnabled
				) {
					toast.error(
						$i18n.t('Model {{modelName}} is not vision capable', {
							modelName: model.name ?? model.id
						})
					);
				}
			}
		}

		// Single request — backend fans out to all models
		const primaryModelId = selectedModelIds[0];
		const primaryModel = $models.filter((m) => m.id === primaryModelId).at(0);
		const primaryResponseMessageId = messageIdsList[0]?.message_id;
		let submittedTaskIds: string[] = [];

		if (primaryModel && primaryResponseMessageId) {
			const chatEventEmitter = await getChatEventEmitter(primaryModel.id, _chatId);

			try {
				scrollToBottom();
				const completion = await sendMessageSocket(
					primaryModel,
					messages && messages.length > 0
						? messages
						: createMessagesList(_history, primaryResponseMessageId),
					_history,
					primaryResponseMessageId,
					_chatId,
					{
						// Send message_ids even for single-column regenerations to preserve modelIdx on reload.
						messageIdsList: messageIdsList.length > 0 ? messageIdsList : undefined,
						regenerationPrompt,
						regenerationMessageId
					}
				);
				if (!completion) return { status: 'rejected' as const };
				submittedTaskIds =
					completion?.task_ids ?? (completion?.task_id ? [completion.task_id] : []);
			} finally {
				if (chatEventEmitter) clearInterval(chatEventEmitter);
			}
		}
		return {
			status: 'submitted' as const,
			chatId: $chatId,
			userMessageId: parentId,
			assistantMessageId: primaryResponseMessageId,
			taskIds: submittedTaskIds
		};
	};

	const getFeatures = (): Record<string, boolean> => {
		let features = {};
		const { available } = getToolFeatureContext();

		if ($config?.features)
			features = {
				voice: ($showCallOverlay && callMode === 'current') || !!bridge?.connected,
				image_generation: available.image_generation ? imageGenerationEnabled : false,
				code_interpreter:
					$config?.features?.enable_code_interpreter &&
					($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
						? codeInterpreterEnabled
						: false,
				web_search: webSearchActive
			};

		return {
			...features,
			knowledge: available.knowledge && knowledgeEnabled,
			knowledge_write: available.knowledge_write && knowledgeEnabled && knowledgeWriteEnabled,
			memory: Boolean(
				available.memory &&
				memoryEnabled &&
				($settings?.memory ?? $config?.features?.enable_memories ?? false)
			),
			notes: available.notes && notesEnabled
		};
	};

	const getStopTokens = () => {
		const stop = params?.stop ?? $settings?.params?.stop;
		if (!stop) return undefined;

		const tokens = Array.isArray(stop) ? stop : stop.split(',').map((s) => s.trim());

		return tokens.filter(Boolean).map((token) => {
			try {
				return decodeURIComponent(JSON.parse(`"${token.replace(/"/g, '\\"')}"`));
			} catch {
				return token;
			}
		});
	};

	const sendMessageSocket = async (
		model,
		_messages,
		_history,
		responseMessageId,
		_chatId,
		{
			messageIdsList,
			regenerationPrompt,
			regenerationMessageId,
			continueResponse = false
		}: {
			messageIdsList?: Array<{ model_id: string; message_id: string }>;
			regenerationPrompt?: string | null;
			regenerationMessageId?: string | null;
			continueResponse?: boolean;
		} = {}
	) => {
		const responseMessage = _history.messages[responseMessageId];
		const userMessage = _history.messages[responseMessage.parentId];
		const requestToken = localStorage.token;
		const requestCurrent = temporaryController.capture();
		let temporaryContext: TemporaryRequestIdentity | undefined;
		if ($temporaryChatEnabled && (contextCompactionMode === 'v2' || temporaryController.identity)) {
			try {
				temporaryContext = await temporaryController.prepare(
					_history,
					userMessage.id,
					params,
					chatFiles
				);
				temporaryScope = temporaryController.identity;
				_chatId = temporaryController.chatId;
				await chatId.set(_chatId);
			} catch (error: any) {
				if (requestCurrent()) {
					reportTemporaryError(error);
					for (const { message_id } of messageIdsList ?? [{ message_id: responseMessageId }]) {
						if (history.messages[message_id])
							history.messages[message_id] = {
								...history.messages[message_id],
								done: true,
								error: { content: String(error?.message ?? error?.detail?.code ?? error) }
							};
					}
					history = history;
				}
				return null;
			}
		}
		// Retain the first identity even when the original message is deleted.
		// Capture it before awaiting so switching chats cannot change this send.
		const connectionSessionId =
			$temporaryChatEnabled || isTemporaryChatId(_chatId)
				? (temporaryConnectionSessionId = getTemporaryConnectionSessionId(
						_history.messages,
						temporaryConnectionSessionId
					))
				: undefined;
		const filesystemAttachment = userMessage?.files?.find(
			(file: any) => file.type === 'filesystem'
		);
		const requestTerminalId = filesystemAttachment?.terminal_id ?? $selectedTerminalId;

		const chatMessageFiles = _messages
			.filter((message) => message.files)
			.flatMap((message) => message.files);

		// Filter chatFiles to only include files that are in the chatMessageFiles
		chatFiles = chatFiles.filter((item) => {
			const fileExists = chatMessageFiles.some((messageFile) => messageFile.id === item.id);
			return fileExists;
		});

		let files = structuredClone(chatFiles);
		files.push(
			...(userMessage?.files ?? []).filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'collection', 'folder'].includes(item.type) ||
					(item.type === 'file' && !isRasterImageContentType(item?.content_type))
			)
		);
		// One copy per attachment; the model does not need an encore.
		files = files.filter((item, index, array) => array.findIndex((i) => equal(i, item)) === index);

		scrollToBottom();
		eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		await tick();

		let userLocation;
		if ($settings?.userLocation) {
			userLocation = await getAndUpdateUserLocation(localStorage.token).catch((err) => {
				console.error(err);
				return undefined;
			});
		}

		const stream =
			model?.info?.params?.stream_response ??
			params?.stream_response ??
			$settings?.params?.stream_response ??
			true;
		// Always include system prompt — backend extracts it and prepends to DB messages.
		// Only temp chats need conversation messages (persisted chats load from DB).
		let messages: any[] = [
			params?.system || $settings.system
				? { role: 'system', content: `${params?.system || $settings?.system || ''}` }
				: undefined
		].filter(Boolean);

		if ($temporaryChatEnabled) {
			messages = [
				...messages,
				..._messages.map((message) => ({
					...message,
					...(message.output && message.role === 'assistant'
						? { output: message.output }
						: { content: processDetails(message.content) })
				}))
			].filter((message) => message);

			messages = messages
				.map((message) => {
					const imageFiles = (message?.files ?? []).filter(
						(file) => file.type === 'image' || isRasterImageContentType(file?.content_type)
					);

					if (message.output && message.role === 'assistant') {
						return {
							role: message.role,
							model: message.model,
							output: message.output,
							meta: message.meta
						};
					}

					if (message.role === 'user' && imageFiles.length > 0) {
						return {
							id: message.id,
							role: message.role,
							contextSummary: message?.contextSummary,
							content: [
								{
									type: 'text',
									text: message?.merged?.content ?? message.content
								},
								...imageFiles.map((file) => ({
									type: 'image_url',
									image_url: {
										url: file.url
									}
								}))
							]
						};
					}

					return {
						id: message.id,
						role: message.role,
						contextSummary: message?.contextSummary,
						content: message?.merged?.content ?? message.content,
						model: message.model,
						meta: message.meta
					};
				})
				.filter(
					(message) =>
						message?.role === 'user' || message?.content?.trim() || message?.output?.length
				);
		}

		const toolIds = [];
		const toolServerIds: (string | number)[] = [];

		for (const toolId of selectedToolIds) {
			if (toolId.startsWith('direct_server:')) {
				let serverId = toolId.replace('direct_server:', '');
				// Check if serverId is a number
				if (!isNaN(parseInt(serverId))) {
					toolServerIds.push(parseInt(serverId));
				} else {
					toolServerIds.push(serverId);
				}
			} else {
				toolIds.push(toolId);
			}
		}

		// The backend applies the skill loading setting to menu-selected skills.
		// Keep mentions in the message so the backend can resolve them.
		const skillIds = [...selectedSkillIds];

		// Only send terminal_id if the model has terminal capability enabled
		const terminalEnabled =
			$config?.features?.enable_tool_servers && (model.info?.meta?.capabilities?.terminal ?? true);
		const useChatVariablesFallback =
			!_chatId || $temporaryChatEnabled || isTemporaryChatId(_chatId);

		if (temporaryContext && (!requestCurrent() || requestToken !== localStorage.token)) return null;
		const res = await generateOpenAIChatCompletion(
			requestToken,
			{
				stream: stream,
				model: model.id,
				...(messages.length > 0 ? { messages } : {}),
				params: {
					...controlAdvancedParams,
					...(!supportsModelControls(model) && reasoningEffortByModel[model.id]
						? { reasoning_effort: reasoningEffortByModel[model.id] }
						: {}),
					model_controls: controlSelections,
					stop: getStopTokens()
				},

				files: embedded
					? [...(files ?? []), ...noteFiles]
					: (files?.length ?? 0) > 0
						? files
						: undefined,

				filter_ids:
					$config?.features?.enable_functions && selectedFilterIds.length > 0
						? selectedFilterIds
						: undefined,
				tool_ids: toolIds.filter((id) =>
					id.startsWith('server:')
						? $config?.features?.enable_tool_servers
						: $config?.features?.enable_tools
				),
				skill_ids: skillIds.length > 0 ? skillIds : undefined,
				terminal_id: terminalEnabled && requestTerminalId ? requestTerminalId : null,
				terminal_cwd:
					!_chatId && terminalContext?.terminalId === requestTerminalId
						? terminalContext.currentDirectory
						: undefined,
				tool_servers: $config?.features?.enable_tool_servers
					? [
							...($toolServers ?? []).filter(
								(server, idx) => toolServerIds.includes(idx) || toolServerIds.includes(server?.id)
							),
							// Direct terminal servers — always included when enabled (not routed through selectedToolIds)
							...(terminalEnabled
								? ($terminalServers ?? [])
										.filter(
											(server) =>
												!server.id && (!filesystemAttachment || server.url === requestTerminalId)
										)
										.map((server) => ({ ...server, is_terminal: true }))
								: [])
						]
					: [],
				features: getFeatures(),
				image_generation_engine_id: getFeatures().image_generation
					? imageGenerationEngineId
					: undefined,
				variables: {
					...getPromptVariables(
						$user?.name,
						$settings?.userLocation ? userLocation : undefined,
						$user?.email
					)
				},
				...(useChatVariablesFallback ? { chat_variables: chatVariables } : {}),
				model_item: $models.find((m) => m.id === model.id),

				session_id: $socket?.id,
				chat_id: _chatId || undefined,
				...(temporaryContext ? { temporary_context: temporaryContext } : {}),
				connection_session_id: connectionSessionId,
				folder_id: $selectedFolder?.id ?? undefined,

				id: responseMessageId,
				...(messageIdsList ? { message_ids: messageIdsList } : {}),
				parent_id: userMessage?.parentId ?? null,
				user_message: userMessage,
				...(regenerationPrompt ? { regeneration_prompt: regenerationPrompt } : {}),
				...(regenerationMessageId ? { regeneration_message_id: regenerationMessageId } : {}),
				...(continueResponse ? { assistant_message_id: responseMessageId } : {}),

				background_tasks: {
					...(!$temporaryChatEnabled &&
					(!_chatId ||
						((userMessage?.parentId ?? null) === null &&
							createMessagesList(_history, responseMessageId).length === 2))
						? {
								title_generation: $settings?.title?.auto ?? true,
								tags_generation: $settings?.autoTags ?? true
							}
						: {}),
					follow_up_generation: $settings?.autoFollowUps ?? true
				}
			},
			`${WEBUI_BASE_URL}/api`
		).catch(async (error) => {
			if (temporaryContext && (!requestCurrent() || _chatId !== $chatId)) return null;
			if (temporaryContext) {
				await temporaryController.handleError(error);
				reportTemporaryError(error);
			}
			console.log(error);

			let errorMessage = error;
			if (error?.error?.message) {
				errorMessage = error.error.message;
			} else if (error?.message) {
				errorMessage = error.message;
			}

			if (typeof errorMessage === 'object') {
				errorMessage = $i18n.t(`Uh-oh! There was an issue with the response.`);
			}

			if (!temporaryContext) toast.error(`${errorMessage}`);
			responseMessage.error = {
				content: error
			};

			responseMessage.done = true;

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;

			return null;
		});

		if (temporaryContext && (!requestCurrent() || _chatId !== $chatId)) return null;
		if (filesystemAttachment && (!res || res.error))
			throw new Error($i18n.t('File request failed. Retry from the message or queue.'));
		if (res) {
			if (res.error) {
				await handleOpenAIError(res.error, responseMessage);
			} else {
				// An early completion event may have already finished these replies.
				if (
					(messageIdsList ?? [{ message_id: responseMessageId }]).some(
						({ message_id }) => history.messages[message_id]?.done === false
					)
				) {
					onToolCallResolved(res);
				}

				// Finish new-chat setup only if navigation has not changed the active chat during the request.
				if (
					res.chat_id &&
					history.messages[responseMessageId] &&
					res.chat_id !== _chatId &&
					($chatId === _chatId || $chatId === res.chat_id)
				) {
					chatRequestQueues.update((q) => {
						if (!q[_chatId]?.length) return q;

						const { [_chatId]: pendingQueue, ...rest } = q;
						return {
							...rest,
							[res.chat_id]: [
								...pendingQueue.map((entry) => ({
									...entry,
									files: entry.files.map((file) =>
										file.type === 'filesystem' && file.session_id == null
											? { ...file, session_id: res.chat_id }
											: file
									)
								})),
								...(rest[res.chat_id] ?? [])
							]
						};
					});
					for (const file of userMessage?.files ?? [])
						if (file.type === 'filesystem' && file.session_id == null)
							file.session_id = res.chat_id;
					for (const file of history.messages[userMessage.id]?.files ?? [])
						if (file.type === 'filesystem' && file.session_id == null)
							file.session_id = res.chat_id;
					await chatId.set(res.chat_id);
					if (!$temporaryChatEnabled && !embedded) {
						// Carry the picks made on the new-chat page over to this chat
						saveDraft(getChatInputDraft(), res.chat_id, false);
						window.history.replaceState(window.history.state, '', `/c/${res.chat_id}`);
						await refreshChatList(localStorage.token);

						// Save chat params omitted from the completion request; the backend already saves files.
						if (Object.keys(params).length > 0) {
							await updateChatParams(localStorage.token, res.chat_id, params);
						}
					}
					await processNextInQueue(res.chat_id);
				}
			}
		}

		await tick();
		if (shouldAutoScrollResponse()) {
			scrollToBottom();
		}
		return res;
	};

	const handleOpenAIError = (error, responseMessage) => {
		const responseFailed = responseMessage.done && responseMessage.error;
		const errorMessage = getErrorMessage(error, $i18n.t('Server connection failed'));
		responseMessage.error = { content: errorMessage };
		responseMessage.done = true;
		if (responseMessage.statusHistory) {
			responseMessage.statusHistory = responseMessage.statusHistory.filter(
				(status) => status.action !== 'knowledge_search'
			);
		}
		history.messages[responseMessage.id] = responseMessage;
		if (!responseFailed) toast.error(errorMessage);
	};

	const stopResponse = async (processQueue = true, messageId = history.currentId) => {
		const targetChatId = $chatId;
		const currentId = history.currentId;
		const stopScope =
			temporaryController.chatId === targetChatId ? temporaryController.identity : null;
		const stopCurrent = temporaryController.capture();
		const stopToken = localStorage.token;
		const responseMessage = messageId ? history.messages[messageId] : null;
		if (responseMessage && (responseMessage.user_id ?? chat?.user_id ?? $user?.id) !== $user?.id)
			return false;
		eventTarget.dispatchEvent(new CustomEvent('voice:cancel_animation'));
		cancelBrowserInteractions();
		if (bridge?.connected && responseMessage) responseMessage.bridgeStopping = true;
		const hasTaskIds = (taskIds?.length ?? 0) > 0;
		const hasPendingAssistantResponse =
			!!$chatId &&
			(hasTaskIds || (responseMessage?.role === 'assistant' && responseMessage?.done !== true));

		if (hasTaskIds || hasPendingAssistantResponse) {
			stoppingChats.set(targetChatId, (stoppingChats.get(targetChatId) ?? 0) + 1);
			try {
				const results = $chatId
					? [await stopTasksByChatId(stopToken, targetChatId, stopScope)]
					: await Promise.all(
							(taskIds ?? []).map((taskId) => stopTask(stopToken, taskId, stopScope))
						);
				const failed = results.find((result) => result?.status === false);
				if (failed) throw failed.message;
			} catch (error) {
				if (responseMessage) responseMessage.bridgeStopping = false;
				toast.error(`${error}`);
				return false;
			} finally {
				const remaining = (stoppingChats.get(targetChatId) ?? 1) - 1;
				if (remaining) stoppingChats.set(targetChatId, remaining);
				else stoppingChats.delete(targetChatId);
			}

			// Cancellation events or navigation may have selected a newer reply while
			// the backend was confirming this stop. Its state belongs to that reply.
			if (!stopCurrent() || $chatId !== targetChatId || history.currentId !== currentId)
				return false;
			taskIds = null;

			// Set all response messages to done
			if (responseMessage?.parentId && history.messages[responseMessage.parentId]) {
				for (const messageId of history.messages[responseMessage.parentId].childrenIds) {
					history.messages[messageId].done = true;
					clearContextCompaction(messageId);
				}
			}

			if (shouldAutoScrollResponse()) {
				scrollToBottom();
			}
		}

		if (generating) {
			generating = false;
			generationController?.abort();
			generationController = null;
		}

		if (processQueue) {
			await processNextInQueue(targetChatId);
		}
		return true;
	};

	const submitMessage = async (parentId, prompt) => {
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return;
		}
		let userPrompt = prompt;
		let userMessageId = uuidv4();

		let userMessage = {
			id: userMessageId,
			parentId: parentId,
			childrenIds: [],
			role: 'user',
			user_id: $user?.id,
			user: { id: $user?.id, name: $user?.name },
			content: userPrompt,
			models: selectedModels,
			timestamp: Math.floor(Date.now() / 1000) // Unix epoch
		};

		if (parentId !== null) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				userMessageId
			];
		}

		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		await sendMessage(history, userMessageId);
	};

	const regenerateResponse = async (message, suggestionPrompt = null) => {
		if (agentModelId || message.model?.startsWith('agent:')) return;
		console.log('regenerateResponse');

		if (history.currentId) {
			let userMessage = history.messages[message.parentId];

			if (!userMessage) {
				toast.error($i18n.t('Parent message not found'));
				return;
			}

			if (autoScroll) {
				scrollToBottom();
			}

			const currentModels = atSelectedModel ? [atSelectedModel.id] : selectedModels;
			const options = {
				...(suggestionPrompt
					? {
							messages: createMessagesList(history, message.id),
							regenerationPrompt: suggestionPrompt,
							regenerationMessageId: message.id
						}
					: {}),
				...(currentModels.length > 1
					? {
							// Regenerate the selected column with its current model.
							modelId: currentModels[message.modelIdx ?? 0] ?? currentModels[0],
							modelIdx: message.modelIdx
						}
					: {})
			};
			userMessage.models = [...currentModels];
			await sendMessage(history, userMessage.id, options);
		}
	};

	const continueResponse = async (message) => {
		if (agentModelId || message.model?.startsWith('agent:')) return;
		console.log('continueResponse');
		const _chatId = JSON.parse(JSON.stringify($chatId));

		if (history.messages[message.id].done == true) {
			const responseMessage = history.messages[message.id];
			responseMessage.done = false;
			await tick();

			const model = $models
				.filter((m) => m.id === (responseMessage?.selectedModelId ?? responseMessage.model))
				.at(0);

			if (model) {
				await sendMessageSocket(
					model,
					createMessagesList(history, responseMessage.id),
					history,
					responseMessage.id,
					_chatId,
					{ continueResponse: true }
				);
			}
		}
	};

	const mergeResponses = async (messageId, responses, _chatId) => {
		console.log('mergeResponses', messageId, responses);
		const message = history.messages[messageId];
		const mergedResponse = {
			status: true,
			content: ''
		};
		message.merged = mergedResponse;
		history.messages[messageId] = message;

		try {
			generating = true;
			const [res, controller] = await generateMoACompletion(
				localStorage.token,
				message.model ?? '',
				message.parentId ? history.messages[message.parentId].content : '',
				responses
			);

			if (res && res.ok && res.body && generating) {
				generationController = controller as AbortController;
				const textStream = await createOpenAITextStream(
					res.body,
					Boolean($settings?.splitLargeChunks ?? false)
				);
				for await (const update of textStream) {
					const { value, done, sources, error, usage } = update;
					if (error || done) {
						generating = false;
						generationController = null;
						break;
					}

					if (mergedResponse.content == '' && value == '\n') {
						continue;
					} else {
						mergedResponse.content += value;
						history.messages[messageId] = message;
					}

					autoScrollToBottom();
				}

				await saveChatHandler(_chatId, history);
			} else {
				console.error(res);
			}
		} catch (e) {
			console.error(e);
		}
	};

	const initChatHandler = async (_history: typeof history) => {
		let _chatId = $chatId;
		const selectedFolderId = $selectedFolder?.id;
		const context = terminalContext;

		if (!$temporaryChatEnabled) {
			const createdChat = await createNewChat(
				localStorage.token,
				{
					id: _chatId,
					title: $i18n.t('New Chat'),
					models: selectedModels,
					system: $settings.system ?? undefined,
					params: params,
					reasoning_effort_by_model: getReasoningEffortByModelForSave(),
					history: _history,
					messages: createMessagesList(_history, _history.currentId),
					tags: [],
					timestamp: Date.now()
				},
				$selectedFolder?.id ?? null,
				chatVariables
			);

			if (!createdChat?.id) throw new Error($i18n.t('Failed to create chat'));
			if (history !== _history || $chatId !== _chatId) return '';
			chat = createdChat;
			_chatId = chat.id;
			await chatId.set(_chatId);

			if (!embedded) {
				saveDraft(getChatInputDraft(), _chatId, false);
				window.history.replaceState(window.history.state, '', `/c/${_chatId}`);
			}

			await tick();

			if (!embedded) {
				await refreshChatList(localStorage.token);
			}

			if (selectedFolderId) {
				await refreshFolderChatLists(selectedFolderId, chat);
			}

			selectedFolder.set(null);
		} else {
			_chatId =
				contextCompactionMode === 'v2'
					? await ensureTemporaryScope()
					: isTemporaryChatId(_chatId)
						? _chatId
						: createTemporaryChatId($socket?.id);
			await chatId.set(_chatId);
		}
		await initializeTerminalDirectory(_chatId, context);
		await tick();

		return _chatId;
	};

	const saveChatHandler = async (
		_chatId,
		historyToSave: { messages: Record<string, any>; currentId: string | null }
	) => {
		const savedBranchId = historyToSave.currentId;
		if (isShared) return;
		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(localStorage.token, _chatId, {
					models: selectedModels,
					history: historyToSave,
					messages: createMessagesList(historyToSave, savedBranchId),
					reasoning_effort_by_model: getReasoningEffortByModelForSave(),
					files: chatFiles
				});
				if (
					$chatId === _chatId &&
					history.currentId === savedBranchId &&
					contextCompactionEnabled &&
					contextUsageModelId &&
					savedBranchId
				) {
					void refreshContextUsage(_chatId, contextUsageModelId, savedBranchId);
				}
			} else if (
				temporaryController.chatId === _chatId &&
				!taskIds?.length &&
				!hasPendingAssistantLeaf()
			) {
				try {
					await temporaryController.prepare(historyToSave, savedBranchId ?? '', params, chatFiles);
					temporaryScope = temporaryController.identity;
					if ($chatId === _chatId && savedBranchId)
						await refreshContextUsage(_chatId, contextUsageModelId, savedBranchId);
				} catch (error) {
					reportTemporaryError(error);
				}
			}
		}
	};

	const saveParam = async (key: string, value: unknown) => {
		if (isShared || !$chatId || $temporaryChatEnabled || isTemporaryChatId($chatId)) return;
		await updateChatParams(localStorage.token, $chatId, { [key]: value }).catch((error) => {
			toast.error(`${error}`);
		});
	};

	const selectChatModels = (modelIds: string[]) => {
		atSelectedModel = undefined;
		const agent = modelIds.find((id) => id.startsWith('agent:'));
		selectedModels = agent ? [agent] : modelIds;
		if (agent) {
			const terminal = $models.find((model) => model.id === agent);
			if (terminal?.owned_by === 'terminal') selectedTerminalId.set(terminal.agent.terminal_id);
		}
		saveSessionSelectedModels();
		void saveControls(selectedModels);
	};

	const saveControls = async (models: string[] | null = null) => {
		if (isShared) return;
		if (!$chatId || $temporaryChatEnabled || chat?.id !== $chatId) return;
		// Model choices are passed explicitly; automatic fallbacks must not be saved.
		if (!models?.length || !models.every(Boolean)) models = null;
		if (!models && equal(chatFiles, chat.chat?.files ?? [])) return;

		const targetChatId = $chatId,
			targetChat = chat,
			targetToken = localStorage.token;
		const res = await updateChatById(targetToken, targetChatId, {
			files: structuredClone(chatFiles),
			...(models ? { models: [...models] } : {})
		}).catch((err) => {
			console.error('[controls autosave]', err);
			return null;
		});
		// Refresh the dedupe baseline so a later revert still saves.
		if (
			res &&
			$chatId === targetChatId &&
			chat === targetChat &&
			localStorage.token === targetToken
		)
			chat = res;
	};

	const MAX_DRAFT_LENGTH = 5000;
	let saveDraftTimeout: ReturnType<typeof setTimeout> | null = null;
	// chatIdProp is empty for chats started from the home page (URL set via replaceState)
	const getDraftChatId = () => chatIdProp || $chatId || null;

	const getChatInputDraft = () => ({
		prompt,
		files: files
			.filter((file) => file.type !== 'image' && !(file.file instanceof File))
			.map((file) => ({
				...file,
				user: undefined,
				access_grants: undefined
			})),
		selectedToolIds,
		selectedSkillIds,
		selectedFilterIds,
		imageGenerationEnabled,
		knowledgeEnabled,
		knowledgeWriteEnabled,
		memoryEnabled,
		notesEnabled,
		webSearchEnabled,
		codeInterpreterEnabled
	});

	const saveDraft = async (draft: any, chatId: string | null = null, debounce = true) => {
		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
		}

		if (draft.prompt !== null && draft.prompt.length < MAX_DRAFT_LENGTH) {
			const key = `chat-input${chatId ? `-${chatId}` : ''}`;
			const write = () => sessionStorage.setItem(key, JSON.stringify(draft));
			if (debounce) {
				saveDraftTimeout = setTimeout(write, 500);
			} else {
				write();
			}
		} else {
			sessionStorage.removeItem(`chat-input${chatId ? `-${chatId}` : ''}`);
		}
	};

	const oauthRedirectHandler = async (
		tool: {
			id: string;
			serverId: string;
			authType?: string | null;
		},
		draft = getChatInputDraft()
	) => {
		await tick();
		saveSessionSelectedModels();
		await saveDraft(draft, null, false);
		initiateOAuthRedirect(tool);
	};

	const clearDraft = async (chatId: string | null = null) => {
		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
		}
		await sessionStorage.removeItem(`chat-input${chatId ? `-${chatId}` : ''}`);
	};

	const moveChatHandler = async (chatId, folderId) => {
		if (chatId && folderId) {
			const res = await updateChatFolderIdById(localStorage.token, chatId, folderId).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (res) {
				await refreshChatList(localStorage.token, { refreshPinned: true });
				await refreshFolderChatLists();

				toast.success($i18n.t('Chat moved successfully'));
			}
		} else {
			toast.error($i18n.t('Failed to move chat'));
		}
	};

	const archiveChatHandler = async (id: string) => {
		try {
			const res = await archiveChatById(localStorage.token, id);
			initNewChat();
			await goto('/');
			await refreshChatList(localStorage.token, { refreshPinned: true });
			await refreshFolderChatLists();
			toast.success(res?.archived ? $i18n.t('Chat archived.') : $i18n.t('Chat unarchived.'));
		} catch (error) {
			console.error('Error archiving chat:', error);
			toast.error($i18n.t('Failed to archive chat.'));
		}
	};

	let showDeleteConfirm = false;

	const confirmWebSearch = async () => {
		const userPrompt = pendingWebSearchPrompt;
		const submission = pendingWebSearchSubmission;
		pendingWebSearchSubmission = null;
		const modelId = pendingWebSearchToggleModelId;
		pendingWebSearchPrompt = null;
		pendingWebSearchToggleModelId = undefined;
		clearTimeout(webSearchConfirmTimeout);
		showWebSearchConfirm = false;

		if (userPrompt !== null) {
			if (submission && !submission.isCurrent()) {
				settleQueuedSubmission(submission.queue, false);
				return;
			}
			webSearchConfirmed = true;
			if (submission) {
				if (submission.queue) processingQueueChats.add(submission.queue.chatId);
				try {
					const result = await submitHandler(userPrompt, {
						attachments: submission.files,
						fromQueue: !!submission.queue,
						queuedSubmission: submission.queue
					});
					if (result.status === 'submitted') settleQueuedSubmission(submission.queue, true);
					else if (!hasPendingQueuedSubmission(submission.queue))
						settleQueuedSubmission(submission.queue, false);
				} catch (error) {
					settleQueuedSubmission(submission.queue, false, error);
					toast.error(String(error));
				} finally {
					if (submission.queue) processingQueueChats.delete(submission.queue.chatId);
				}
			} else await submitHandler(userPrompt);
		} else if (
			modelId !== undefined &&
			modelId === getToolFeatureModelId(selectedModels, atSelectedModel?.id) &&
			getToolFeatureContext().available.web_search
		) {
			webSearchConfirmed = true;
			webSearchEnabled = true;
			await persistToolFeaturePreference(modelId, 'web_search', true);
		}
	};

	const cancelWebSearch = () => {
		if (pendingWebSearchToggleModelId !== undefined) webSearchEnabled = false;
		resetWebSearchConfirmation();
	};

	const deleteChatHandler = async (id: string) => {
		showDeleteConfirm = true;
	};

	const confirmDeleteChat = async () => {
		const id = $chatId;
		if (!id) return;

		try {
			const res = await deleteChatById(localStorage.token, id);
			if (res) {
				initNewChat();
				await goto('/');
				await refreshChatList(localStorage.token, { refreshPinned: true });
				await refreshFolderChatLists();
				allTags.set(await getAllTags(localStorage.token));
				toast.success($i18n.t('Chat deleted.'));
			}
		} catch (error) {
			console.error('Error deleting chat:', error);
			toast.error(`${error}`);
		}
	};
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$settings.showChatTitleInTab !== false && $chatTitle
			? `${$chatTitle.length > 30 ? `${$chatTitle.slice(0, 30)}...` : $chatTitle} / ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

<audio id="audioElement" style="display: none;"></audio>

{#if getChatVariablesForm(selectedModelIds, chatVariables, $models).conflicts.length > 0}
	<Modal ariaLabel={$i18n.t('Chat Variables')} bind:show={showChatVariablesModal} size="md">
		<div>
			<div class="flex justify-between px-4 pt-3 pb-1 dark:text-gray-300">
				<div class="self-center text-sm font-medium">{$i18n.t('Chat Variables')}</div>
				<button
					aria-label={$i18n.t('Close')}
					class="self-center rounded-lg py-1 text-gray-500 transition hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 px-0"
					on:click={() => {
						showChatVariablesModal = false;
					}}
				>
					<XMark className="size-4" />
				</button>
			</div>

			<div class="px-5 pb-4 text-sm text-gray-600 dark:text-gray-300">
				<div class="mb-2 text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Selected models define incompatible Chat Variables.')}
				</div>
				<div class="flex flex-col gap-1">
					{#each getChatVariablesForm(selectedModelIds, chatVariables, $models).conflicts as conflict}
						<div class="rounded-lg border border-red-200 px-3 py-2 text-xs dark:border-red-900/60">
							<div class="font-medium text-red-600 dark:text-red-400">{conflict.key}</div>
							<div class="mt-1 text-gray-500 dark:text-gray-400">
								{conflict.modelIds.join(', ')}
							</div>
						</div>
					{/each}
				</div>
			</div>
		</div>
	</Modal>
{:else}
	<InputVariablesModal
		bind:show={showChatVariablesModal}
		title={$i18n.t('Chat Variables')}
		variables={getChatVariablesForm(selectedModelIds, chatVariables, $models).variables}
		onSave={saveChatVariables}
	/>
{/if}

<EventConfirmDialog
	show={pendingUrlActions !== null}
	title={$i18n.t('Open link')}
	on:confirm={async () => {
		const actions = pendingUrlActions;
		const url = $page.url.href;
		pendingUrlActions = null;
		if (!actions) return;
		for (const source of actions.urls) {
			if ($page.url.href !== url) return;
			await uploadWeb(source);
		}
		if (actions.call && $page.url.href === url) openCallOverlay();
	}}
	on:cancel={() => (pendingUrlActions = null)}
>
	<div class="text-sm text-gray-500 space-y-2 max-h-60 overflow-auto break-words">
		{#if pendingUrlActions?.urls.length}
			<p>{$i18n.t('Load content from these URLs?')}</p>
			{#each pendingUrlActions.urls as url}
				<p>{url}</p>
			{/each}
		{/if}
		{#if pendingUrlActions?.call}
			<p>{$i18n.t('Start a voice call using your microphone?')}</p>
		{/if}
	</div>
</EventConfirmDialog>

<WebSearchConfirmDialog
	bind:show={showWebSearchConfirm}
	title={$i18n.t('Use Web Search?')}
	message={($config?.features?.web_search_confirmation_content ?? '').trim() !== ''
		? ($config?.features?.web_search_confirmation_content ?? '')
		: $i18n.t('Your query will be sent to the configured web search provider.')}
	confirmLabel={$i18n.t('Continue')}
	cancelLabel={$i18n.t('Cancel')}
	on:confirm={confirmWebSearch}
	on:cancel={cancelWebSearch}
/>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete chat?')}
	on:confirm={() => {
		confirmDeleteChat();
	}}
>
	<div class=" text-sm text-gray-500 flex-1 line-clamp-3">
		{$i18n.t('This will delete')} <span class="  font-normal">{$chatTitle}</span>.
	</div>
</DeleteConfirmDialog>

<EventConfirmDialog
	bind:show={showEventConfirmation}
	title={eventConfirmationTitle}
	message={eventConfirmationMessage}
	input={eventConfirmationInput}
	inputPlaceholder={eventConfirmationInputPlaceholder}
	inputValue={eventConfirmationInputValue}
	inputType={eventConfirmationInputType}
	inputOptions={eventConfirmationInputOptions}
	on:confirm={(e) => {
		if (eventConfirmationInput) {
			eventCallback(e.detail);
		} else if (e.detail) {
			eventCallback(e.detail);
		} else {
			eventCallback(true);
		}
	}}
	on:cancel={() => {
		eventCallback(false);
	}}
/>

{#if browserInteraction?.type === 'request:elicitation'}
	{#key browserInteraction.id}
		{@const interaction = browserInteraction}
		<ElicitationDialog
			data={interaction.data}
			onResponse={(value) => resolveBrowserInteraction(interaction, value)}
		/>
	{/key}
{/if}

{#if browserInteraction?.data?.tool_call}
	{#key browserInteraction.id}
		{@const interaction = browserInteraction}
		<EventConfirmDialog
			show={true}
			title={$i18n.t('Tool Permissions')}
			confirmLabel={$i18n.t('Allow')}
			cancelLabel={$i18n.t('Deny')}
			on:confirm={() => resolveBrowserInteraction(interaction, true)}
			on:cancel={() => resolveBrowserInteraction(interaction, false)}
		>
			<div class="text-sm text-gray-500">
				<div class="font-medium break-all">{interaction.data.tool_call.name}</div>
				<pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(
						interaction.data.tool_call.arguments,
						null,
						2
					)}</pre>
			</div>
		</EventConfirmDialog>
	{/key}
{/if}

<div
	class="{embedded
		? 'h-full'
		: 'h-screen max-h-[100dvh]'} transition-width duration-200 ease-in-out {$showSidebar &&
	!embedded
		? '  md:max-w-[calc(100%-var(--sidebar-width))]'
		: ' '} w-full max-w-full min-w-0 flex flex-col"
	id={chatContainerId}
	role="presentation"
	on:dragover|capture={(event) => terminalTabs?.dragOverChat(event)}
	on:drop|capture={(event) => terminalTabs?.dropChat(event)}
	on:dragleave={(event) => terminalTabs?.leaveChatDrop(event)}
>
	<div in:fade={{ duration: 50 }} class="w-full h-full flex flex-col">
		{#if backgroundImage}
			<div
				class="pointer-events-none absolute top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
				style="background-image: url({backgroundImage})"
			/>
			<div
				class="pointer-events-none absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
			></div>
		{/if}

		<div class="relative w-full h-full min-h-0 flex">
			<div
				class="h-full flex relative max-w-full min-w-0 min-h-0 flex-1 flex-col col-start-1 row-start-2"
				class:hidden={fullWidthTabs && !mainChatActive}
				style={fullWidthTabs ? mainChatStyle : undefined}
				data-main-chat-pane
				data-panel-group={fullWidthTabs ? mainChatGroupId : undefined}
				on:focusin={() => fullWidthTabs && terminalTabs?.focusMainChat()}
				on:pointerdown={() => fullWidthTabs && terminalTabs?.focusMainChat()}
				id={fullWidthTabs ? 'chat-panel-main-chat-view' : undefined}
				role={fullWidthTabs ? 'tabpanel' : undefined}
				aria-labelledby={fullWidthTabs ? 'chat-panel-main-chat' : undefined}
			>
				{#if loading}
					<div class="flex items-center justify-center h-full w-full">
						<Spinner className="size-5" />
					</div>
				{:else}
					<FilesOverlay show={dragged} />
					{#if embedded}
						<div
							class="h-8 shrink-0 flex items-center justify-between gap-2 border-b border-gray-50/80 px-3 pb-1 text-gray-700 dark:border-gray-850/40 dark:text-gray-200"
						>
							<div class="flex min-w-0 items-center gap-2">
								<EmbeddedChatHistoryDropdown
									title={embeddedHeaderTitle}
									chats={embeddedChats}
									canCreateNew={!!onNewEmbeddedChat &&
										Object.keys(history?.messages ?? {}).length > 0}
									{loading}
									onNewChat={onNewEmbeddedChat}
									onSelectChat={onSelectEmbeddedChat}
									onDeleteChat={onDeleteEmbeddedChat}
								/>
							</div>
							{#if onCloseEmbedded}<Tooltip content={$i18n.t('Close')} placement="bottom">
									<button
										type="button"
										class="rounded-md py-1 text-gray-500 transition hover:text-gray-900 dark:hover:text-white px-0"
										on:click={() => onCloseEmbedded?.()}
										aria-label={$i18n.t('Close')}
									>
										<XMark className="size-4" strokeWidth="2" />
									</button>
								</Tooltip>{/if}
						</div>
					{:else}
						<Navbar
							tabbed={fullWidthTabs}
							bind:tabTitleEditor
							bind:tabActions
							bind:tabBarActions
							{liveParticipants}
							bind:this={navbarElement}
							readOnly={isShared}
							chat={{
								id: $chatId,
								archived: chat?.archived ?? false,
								folder_id: chat?.folder_id,
								chat: {
									title: $chatTitle,
									models: selectedModels,
									system: $settings.system ?? undefined,
									params: params,
									history: history,
									timestamp: Date.now()
								}
							}}
							{history}
							title={$chatTitle}
							shareEnabled={!!history.currentId}
							{initNewChat}
							scrollToTop={!isNearTop ? scrollToTop : null}
							{archiveChatHandler}
							{deleteChatHandler}
							{moveChatHandler}
							onSaveTempChat={async () => {
								try {
									if (!history?.currentId || !Object.keys(history.messages).length) {
										toast.error($i18n.t('No conversation to save'));
										return;
									}
									const raw = await prepareTemporaryCopy();
									const savingCurrent = temporaryController.capture();
									const savingToken = localStorage.token;
									const messages = createMessagesList(raw.history, raw.history.currentId);
									const title =
										messages.find((m) => m.role === 'user')?.content ?? $i18n.t('New Chat');

									const savedChat = await createNewChat(
										savingToken,
										{
											id: (temporarySaveId ??= uuidv4()),
											title: title.length > 50 ? `${title.slice(0, 50)}...` : title,
											models: selectedModels,
											params: params,
											reasoning_effort_by_model: getReasoningEffortByModelForSave(),
											history: raw.history,
											files: raw.files,
											messages: messages,
											timestamp: Date.now()
										},
										null,
										chatVariables,
										{
											key: temporarySaveId!,
											temporaryContext: temporaryController.identity ?? undefined
										}
									);

									if (savedChat && savingCurrent() && savingToken === localStorage.token) {
										resetTemporaryScope();
										temporaryChatEnabled.set(false);
										chatId.set(savedChat.id);
										await refreshChatList(localStorage.token);

										await goto(`/c/${savedChat.id}`);
										toast.success($i18n.t('Conversation saved successfully'));
									}
								} catch (error) {
									console.error('Failed to save temporary chat:', error);
									toast.error($i18n.t('Failed to save conversation'));
								}
							}}
						/>
					{/if}
					<div id="chat-pane" class="flex flex-col flex-auto z-10 w-full @container overflow-auto">
						<div class="sr-only" aria-live="polite" aria-atomic="true">{liveStatus}</div>
						{#if offlineAgent}
							<div class="mx-auto max-w-3xl px-4 py-2 text-xs text-gray-500" role="status">
								{$i18n.t('Agent is offline. Reconnect its terminal or choose another model.')}
							</div>
						{/if}
						{#if ($settings?.landingPageMode === 'chat' && !$selectedFolder) || createMessagesList(history, history.currentId).length > 0}
							<div
								class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
								id="messages-container"
								bind:this={messagesContainerElement}
								on:scroll={(e) => {
									if (!messagesContainerElement) return;
									const { scrollTop, scrollHeight, clientHeight } = messagesContainerElement;
									// Content growing below the viewport must not stop following.
									autoScroll =
										scrollHeight - scrollTop <= clientHeight + 5 ||
										(autoScroll && scrollTop >= lastScrollTop);
									lastScrollTop = scrollTop;
									isNearTop = scrollTop <= 100;
								}}
							>
								<div class=" h-full w-full flex flex-col">
									<Messages
										bind:this={messagesRef}
										chatId={$chatId}
										user={chatOwner ?? (chat ? { id: chat.user_id } : $user)}
										readOnly={isShared}
										shareMode={chat?.chat?.share_mode ?? null}
										bind:history
										bind:autoScroll
										bind:prompt
										setInputText={(text) => {
											messageInput?.setTextHandler(text);
										}}
										bind:selectedModels
										{atSelectedModel}
										className={embedded || fullWidthTabs ? 'h-full flex pt-4' : 'h-full flex pt-18'}
										{sendMessage}
										{showMessage}
										{submitMessage}
										{continueResponse}
										{regenerateResponse}
										{mergeResponses}
										{chatActionHandler}
										{addMessages}
										{onToolCallResolved}
										allowDelete={!(generating || taskIds?.length)}
										forkHandler={$temporaryChatEnabled ? null : handleForkChat}
										topPadding={!embedded && !fullWidthTabs}
										bottomPadding={files.length > 0}
										{onSelect}
										{onInsertToNote}
									/>
								</div>
							</div>

							{#if isShared && chat?.chat?.share_mode !== 'continue'}
								{#if canClone}
									<div
										class="pointer-events-none absolute inset-x-0 bottom-0 z-10 flex justify-center gap-2 bg-linear-to-t from-white dark:from-gray-900 to-transparent pb-5 pt-10"
									>
										<button
											class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white pointer-events-auto"
											disabled={cloning}
											on:click={cloneSharedChat}
										>
											{$i18n.t('Clone Chat')}
										</button>
									</div>
								{/if}
							{:else}
								<div
									id={embedded ? messageInputDropzoneId : undefined}
									class=" pb-2 {dragged ? 'z-0' : 'z-10'}"
								>
									<MessageInput
										bind:terminalContext
										{typingUsers}
										on:typing={(event) => emitTyping(event.detail)}
										callActive={!!(bridge?.connected || bridge?.connecting)}
										callEnabled={!isShared}
										bind:this={messageInput}
										{history}
										{taskIds}
										bind:selectedModels
										{reasoningEffortByModel}
										modelControlSelections={controlSelections}
										modelControlAdvancedParams={controlAdvancedParams}
										onModelControlChange={setModelControl}
										onReasoningEffortChange={setReasoningEffortOverride}
										onModelSelect={selectChatModels}
										onModelMention={(model) => selectChatModels([model.id])}
										bind:chatFiles
										bind:params
										onParamChange={saveParam}
										bind:files
										bind:prompt
										bind:autoScroll
										bind:selectedToolIds
										bind:selectedSkillIds
										bind:selectedFilterIds
										bind:imageGenerationEnabled
										bind:knowledgeEnabled
										bind:knowledgeWriteEnabled
										bind:memoryEnabled
										bind:notesEnabled
										bind:codeInterpreterEnabled
										{pendingOAuthTools}
										{oauthRedirectHandler}
										bind:webSearchEnabled
										bind:atSelectedModel
										bind:showCommands
										bind:dragged
										dropzoneId={messageInputDropzoneId}
										chatId={$chatId}
										{contextUsage}
										contextCompactionEnabled={contextCompactionEnabled &&
											!isShared &&
											(!$temporaryChatEnabled || !!temporaryScope)}
										{contextCompactionMode}
										{embedded}
										compactHandler={handleManualCompact}
										statusHandler={handleStatusCommand}
										forkHandler={handleForkChat}
										{toolApprovalMode}
										onToolApprovalModeChange={handleToolApprovalModeChange}
										{generating}
										{stopResponse}
										{createMessagePair}
										{onUpload}
										{onUpdate}
										messageQueue={$chatRequestQueues[$chatId] ?? []}
										{chatTasks}
										askUser={savedAskUserPrompt ?? socketAskUserPrompt}
										onQueueSendNow={sendQueuedMessageNow}
										onQueueEdit={editQueuedMessage}
										onQueueDelete={deleteQueuedMessage}
										onChange={(data: any) => {
											if (!$temporaryChatEnabled) {
												saveDraft(data, getDraftChatId());
											}
										}}
										onWebSearchToggle={handleWebSearchToggle}
										onImageGenerationToggle={handleImageGenerationToggle}
										{imageGenerationEngines}
										{imageGenerationEngineId}
										{imageGenerationEnginesLoading}
										onImageGenerationEngineChange={handleImageGenerationEngineChange}
										onContextToolToggle={handleContextToolToggle}
										on:chatVariables={() => {
											showChatVariablesModal = true;
										}}
										on:submit={async (e) => {
											clearDraft(getDraftChatId());
											if (e.detail || files.length > 0) {
												await tick();

												submitHandler(withSelectedText(e.detail));
											}
										}}
									/>

									<div
										class="absolute bottom-1 text-xs text-gray-500 text-center line-clamp-1 right-0 left-0"
									>
										<!-- {$i18n.t('LLMs can make mistakes. Verify important information.')} -->
									</div>
								</div>
							{/if}
						{:else if embedded}
							<div class="flex h-full min-h-0 flex-col justify-end">
								{#if suggestedPrompts.length > 0}
									<div class="flex flex-1 items-end px-5 pb-8">
										<div class="w-full">
											<div class="mb-2 text-[0.75rem] text-gray-300 dark:text-gray-700">
												{$i18n.t('Suggested prompts')}
											</div>
											<div class="flex flex-col">
												{#each suggestedPrompts as suggestion}
													<button
														type="button"
														class="flex min-h-8 w-full items-center justify-between py-1 text-left text-[0.8125rem] leading-5 text-gray-500 transition hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-300"
														on:click={async () => {
															await tick();
															await submitHandler(withSelectedText(suggestion));
														}}
													>
														<span class="min-w-0 truncate">{suggestion}</span>
													</button>
												{/each}
											</div>
										</div>
									</div>
								{/if}
								<div id={embedded ? messageInputDropzoneId : undefined} class="pb-2 z-10">
									<MessageInput
										bind:terminalContext
										{typingUsers}
										on:typing={(event) => emitTyping(event.detail)}
										callActive={!!(bridge?.connected || bridge?.connecting)}
										callEnabled={!isShared}
										bind:this={messageInput}
										{history}
										{taskIds}
										bind:selectedModels
										{reasoningEffortByModel}
										modelControlSelections={controlSelections}
										modelControlAdvancedParams={controlAdvancedParams}
										onModelControlChange={setModelControl}
										onReasoningEffortChange={setReasoningEffortOverride}
										onModelSelect={selectChatModels}
										onModelMention={(model) => selectChatModels([model.id])}
										bind:chatFiles
										bind:params
										onParamChange={saveParam}
										bind:files
										bind:prompt
										bind:autoScroll
										bind:selectedToolIds
										bind:selectedSkillIds
										bind:selectedFilterIds
										bind:imageGenerationEnabled
										bind:knowledgeEnabled
										bind:knowledgeWriteEnabled
										bind:memoryEnabled
										bind:notesEnabled
										bind:codeInterpreterEnabled
										{pendingOAuthTools}
										{oauthRedirectHandler}
										bind:webSearchEnabled
										bind:atSelectedModel
										bind:showCommands
										bind:dragged
										dropzoneId={messageInputDropzoneId}
										chatId={$chatId}
										{contextUsage}
										contextCompactionEnabled={contextCompactionEnabled &&
											!isShared &&
											(!$temporaryChatEnabled || !!temporaryScope)}
										{contextCompactionMode}
										{embedded}
										compactHandler={handleManualCompact}
										statusHandler={handleStatusCommand}
										forkHandler={handleForkChat}
										{toolApprovalMode}
										onToolApprovalModeChange={handleToolApprovalModeChange}
										{generating}
										{stopResponse}
										{createMessagePair}
										{onUpload}
										{onUpdate}
										messageQueue={$chatRequestQueues[$chatId] ?? []}
										{chatTasks}
										askUser={savedAskUserPrompt ?? socketAskUserPrompt}
										onQueueSendNow={sendQueuedMessageNow}
										onQueueEdit={editQueuedMessage}
										onQueueDelete={deleteQueuedMessage}
										onChange={(data: any) => {
											if (!$temporaryChatEnabled) {
												saveDraft(data, getDraftChatId());
											}
										}}
										onWebSearchToggle={handleWebSearchToggle}
										onImageGenerationToggle={handleImageGenerationToggle}
										{imageGenerationEngines}
										{imageGenerationEngineId}
										{imageGenerationEnginesLoading}
										onImageGenerationEngineChange={handleImageGenerationEngineChange}
										onContextToolToggle={handleContextToolToggle}
										on:chatVariables={() => {
											showChatVariablesModal = true;
										}}
										on:submit={async (e) => {
											clearDraft(getDraftChatId());
											if (e.detail || files.length > 0) {
												await tick();
												submitHandler(withSelectedText(e.detail));
											}
										}}
									/>
								</div>
							</div>
						{:else}
							<div class="flex items-center h-full">
								<Placeholder
									chatId={$chatId}
									bind:terminalContext
									callActive={!!(bridge?.connected || bridge?.connecting)}
									callEnabled={!isShared}
									bind:selectedModelIdx
									{history}
									bind:selectedModels
									{reasoningEffortByModel}
									modelControlSelections={controlSelections}
									modelControlAdvancedParams={controlAdvancedParams}
									onModelControlChange={setModelControl}
									onReasoningEffortChange={setReasoningEffortOverride}
									onModelSelect={selectChatModels}
									onModelMention={(model) => selectChatModels([model.id])}
									bind:chatFiles
									bind:params
									onParamChange={saveParam}
									bind:messageInput
									bind:files
									bind:prompt
									bind:autoScroll
									bind:selectedToolIds
									bind:selectedSkillIds
									bind:selectedFilterIds
									bind:imageGenerationEnabled
									bind:knowledgeEnabled
									bind:knowledgeWriteEnabled
									bind:memoryEnabled
									bind:notesEnabled
									bind:codeInterpreterEnabled
									bind:webSearchEnabled
									bind:atSelectedModel
									bind:showCommands
									bind:dragged
									{toolApprovalMode}
									onToolApprovalModeChange={handleToolApprovalModeChange}
									{pendingOAuthTools}
									{oauthRedirectHandler}
									{stopResponse}
									{createMessagePair}
									{onSelect}
									{onUpload}
									{onUpdate}
									messageQueue={$chatRequestQueues[$chatId] ?? []}
									askUser={savedAskUserPrompt ?? socketAskUserPrompt}
									onQueueSendNow={sendQueuedMessageNow}
									onQueueEdit={editQueuedMessage}
									onQueueDelete={deleteQueuedMessage}
									onWebSearchToggle={handleWebSearchToggle}
									onImageGenerationToggle={handleImageGenerationToggle}
									{imageGenerationEngines}
									{imageGenerationEngineId}
									{imageGenerationEnginesLoading}
									onImageGenerationEngineChange={handleImageGenerationEngineChange}
									onContextToolToggle={handleContextToolToggle}
									on:chatVariables={() => {
										showChatVariablesModal = true;
									}}
									onChange={(data: any) => {
										if (!$temporaryChatEnabled) {
											saveDraft(data, getDraftChatId());
										}
									}}
									on:submit={async (e) => {
										clearDraft(getDraftChatId());
										if (e.detail || files.length > 0) {
											await tick();
											submitHandler(withSelectedText(e.detail));
										}
									}}
								/>
							</div>
						{/if}
					</div>
				{/if}
			</div>

			{#if !embedded}
				<ChatTabs
					chatLoading={loading}
					bind:fullWidth={fullWidthTabs}
					bind:mainChatActive
					bind:mainChatStyle
					bind:mainChatGroupId
					title={$chatTitle || $i18n.t('New Chat')}
					tabTitleEditor={loading ? undefined : tabTitleEditor}
					tabActions={loading ? undefined : tabActions}
					tabBarActions={loading ? undefined : tabBarActions}
					bind:terminalContext
					bind:history
					bind:files
					chatId={$chatId}
					bind:this={terminalTabs}
					folderId={chat ? (chat.folder_id ?? null) : ($selectedFolder?.id ?? null)}
					chatUser={chatOwner}
					modelId={selectedModelIds?.at(0) ?? null}
					canSideChat={sideChatAllowed}
					systemPrompt={params?.system || $settings.system || ''}
					submitPrompt={submitHandler}
					onAskFile={askFile}
					{stopResponse}
					{bridge}
					bind:callMode
					{showMessage}
					{eventTarget}
					{codeInterpreterEnabled}
				/>
			{/if}
		</div>
	</div>
</div>

<style>
	:global(:where(html:not(.native-scrollbars))) ::-webkit-scrollbar {
		height: 0.5rem;
		width: 0.5rem;
	}
</style>
