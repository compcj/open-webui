<script lang="ts">
	import { resolveTerminalTarget } from '$lib/apis/terminal';
	// Keep keys in nested template expressions visible to i18next-parser.
	// $i18n.t("Terminal unavailable");
	// $i18n.t("No preview available");

	import { getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { toast } from 'svelte-sonner';
	import {
		settings,
		config,
		selectedTerminalId,
		showTabs,
		showFileNavPath,
		terminalServers,
		user
	} from '$lib/stores';
	import {
		downloadDeliveryFile,
		downloadFileBlob,
		downloadFilePreview,
		getFileDelivery,
		readFile,
		validateDeliveryUrl
	} from '$lib/apis/terminal';
	import { getFileContentById } from '$lib/apis/files';
	import FilePreview from '$lib/components/chat/FileNav/FilePreview.svelte';
	import Icon from '$lib/components/chat/FileNav/Icon.svelte';
	import { fileIconName } from '$lib/components/chat/FileNav/fileIcon';
	import { normalizeDocumentTargetPage } from '$lib/utils/documentPreview';
	import { hasPdfSignature } from '$lib/utils/terminal-files';

	export let item: any;
	export let chatId = '';
	export let modelId: string | null = null;

	const i18n = getContext<Writable<i18nType>>('i18n');
	const temporaryContext: any = getContext('temporary-context');

	let expanded = true;
	let loading = false;
	let disposed = false;
	let loadedKey = '';
	let error = '';
	let showRaw = false;
	let objectUrls: string[] = [];
	let terminal: ReturnType<typeof resolveTerminalTarget> = null;

	let fileImageUrl: string | null = null;
	let fileVideoUrl: string | null = null;
	let fileAudioUrl: string | null = null;
	let filePdfData: ArrayBuffer | null = null;
	let fileSqliteData: ArrayBuffer | null = null;
	let fileModelData: ArrayBuffer | null = null;
	let fileDocxData: ArrayBuffer | null = null;
	let fileContent: string | null = null;
	let fileOfficeSlides: string[] | null = null;
	let currentSlide = 0;
	let selectedExcelSheet = '';
	let excelWorkbook: import('xlsx').WorkBook | null = null;

	const IMAGE_EXTS = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'ico', 'avif']);
	const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'ogv', 'avi', 'mkv']);
	const AUDIO_EXTS = new Set(['mp3', 'wav', 'ogg', 'oga', 'flac', 'm4a', 'aac', 'wma', 'opus']);
	const SQLITE_EXTS = new Set(['db', 'sqlite', 'sqlite3', 'db3']);
	const OFFICE_EXTS = new Set(['docx', 'xlsx', 'xls', 'pptx']);

	$: fileId =
		item?.source === 'open_webui'
			? (String(item.path ?? '').match(/^\/api\/v1\/files\/([^/]+)\/content$/)?.[1] ?? '')
			: '';
	// Content URLs have no extension; use the filename to select the preview.
	$: path = String(fileId ? item.name : item?.full_path || item?.path || '');
	$: name = String(item?.name || path.split('/').filter(Boolean).at(-1) || 'file');
	$: targetPage = normalizeDocumentTargetPage(item?.page);
	$: selector = item?.terminal_selector;
	$: signedDelivery = typeof item?.download_url === 'string';
	$: deliveryOwner = typeof item?.owner_id === 'string' && item.owner_id === $user?.id;
	$: {
		(selector,
			signedDelivery,
			$terminalServers,
			$selectedTerminalId,
			$settings,
			$config,
			$user,
			modelId,
			chatId);
		terminal = resolveTerminal();
	}
	$: unavailable = (signedDelivery && (!terminal || !deliveryOwner)) || (!terminal && !fileId);
	$: previewKey =
		(fileId && ($user?.id ?? '') + '|' + fileId) ||
		(terminal && !unavailable
			? `${terminal.baseUrl}|${JSON.stringify(terminal.key)}|${JSON.stringify(terminal.context)}|${path}|${item?.download_url ?? ''}|${item?.image_url ?? ''}|${item?.session_id ?? chatId}`
			: '');
	$: previewClass = isImage(path)
		? 'h-[22rem]'
		: isPdf(path) ||
			  isOffice(path) ||
			  ['glb', 'gltf', 'stl', 'obj', 'ply'].includes(getExt(path)) ||
			  getExt(path) === 'html' ||
			  getExt(path) === 'htm' ||
			  getExt(path) === 'svg'
			? 'h-96'
			: 'h-72';
	$: if (unavailable) {
		loadedKey = '';
		clearPreview();
	}
	$: if (expanded && !unavailable && path && previewKey !== loadedKey && !loading) {
		void loadPreview(previewKey);
	}

	const getExt = (value: string) => value.split('.').pop()?.toLowerCase() ?? '';
	const isImage = (value: string) => IMAGE_EXTS.has(getExt(value));
	const isVideo = (value: string) => VIDEO_EXTS.has(getExt(value));
	const isAudio = (value: string) => AUDIO_EXTS.has(getExt(value));
	const isSqlite = (value: string) => SQLITE_EXTS.has(getExt(value));
	const isPdf = (value: string) => getExt(value) === 'pdf';
	const isOffice = (value: string) => OFFICE_EXTS.has(getExt(value));

	const t = (key: string, vars?: Record<string, unknown>) => $i18n?.t?.(key, vars) ?? key;

	function resolveTerminal() {
		if (!selector || (!signedDelivery && selector !== $selectedTerminalId)) return null;
		return resolveTerminalTarget(
			selector,
			$terminalServers ?? [],
			temporaryContext?.terminalCredential(localStorage.token, chatId) ?? localStorage.token,
			{ chatId: item?.session_id || chatId, modelId },
			$settings?.terminalServers ?? []
		);
	}

	function clearPreview() {
		for (const url of objectUrls) URL.revokeObjectURL(url);
		objectUrls = [];
		fileImageUrl = null;
		fileVideoUrl = null;
		fileAudioUrl = null;
		filePdfData = null;
		fileSqliteData = null;
		fileModelData = null;
		fileDocxData = null;
		fileContent = null;
		fileOfficeSlides = null;
		currentSlide = 0;
		selectedExcelSheet = '';
		excelWorkbook = null;
	}

	const previewIsCurrent = (key: string) => !disposed && !unavailable && key === previewKey;

	async function blobForPreview(key: string) {
		if (!previewIsCurrent(key)) return null;
		if (fileId) {
			const data = await getFileContentById(fileId).catch(() => null);
			return data && previewIsCurrent(key)
				? { blob: new Blob([data], { type: item?.content_type ?? '' }), filename: name }
				: null;
		}
		if (!terminal) return null;
		if (signedDelivery) {
			return downloadDeliveryFile(item.download_url, terminal);
		}
		return downloadFileBlob(terminal, path);
	}

	async function loadPreview(key: string) {
		const activeTerminal = terminal;
		async function current<T>(promise: Promise<T>) {
			const value = await promise;
			if (!previewIsCurrent(key)) throw new DOMException('Preview context changed', 'AbortError');
			return value;
		}
		if (!activeTerminal && !fileId) return;
		loadedKey = key;
		loading = true;
		error = '';
		clearPreview();

		try {
			if (getExt(path) === 'svg' && activeTerminal) {
				if (signedDelivery) {
					fileImageUrl = item?.image_url
						? validateDeliveryUrl(item.image_url, activeTerminal.baseUrl, 'image')
						: null;
					const result = await current(blobForPreview(key));
					if (!result) throw new Error(t('Preview failed'));
					if (!previewIsCurrent(key)) return;
					fileContent = await current(result.blob.text());
				} else {
					const isSystem = ($terminalServers ?? []).some((server) => server.id === selector);
					const [content, delivery] = await Promise.all([
						readFile(activeTerminal, path),
						isSystem ? getFileDelivery(activeTerminal, path) : Promise.resolve(null)
					]);
					if (!previewIsCurrent(key)) return;
					fileContent = content;
					fileImageUrl = delivery?.image_url ?? null;
				}
				return;
			}
			const signedImageUrl =
				signedDelivery && activeTerminal && item?.image_url
					? validateDeliveryUrl(item.image_url, activeTerminal.baseUrl, 'image')
					: null;
			if (signedImageUrl && isImage(path)) {
				fileImageUrl = signedImageUrl;
			} else if (isImage(path) || isVideo(path) || isAudio(path)) {
				const result = await current(blobForPreview(key));
				if (!result) throw new Error(t('Preview failed'));
				const url = URL.createObjectURL(result.blob);
				if (!previewIsCurrent(key)) {
					URL.revokeObjectURL(url);
					return;
				}
				objectUrls = [...objectUrls, url];
				if (isImage(path)) fileImageUrl = url;
				else if (isVideo(path)) fileVideoUrl = url;
				else fileAudioUrl = url;
			} else if (['glb', 'gltf', 'stl', 'obj', 'ply'].includes(getExt(path))) {
				const result = await current(blobForPreview(key));
				if (!result) throw new Error(t('Preview failed'));
				fileModelData = await current(result.blob.arrayBuffer());
			} else if (isPdf(path) || isSqlite(path) || isOffice(path)) {
				const ext = getExt(path);

				if (isPdf(path)) {
					const result = await current(blobForPreview(key));
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await current(result.blob.arrayBuffer());
					if (!hasPdfSignature(arrayBuffer)) throw new Error(t('Preview failed'));
					if (!previewIsCurrent(key)) return;
					filePdfData = arrayBuffer;
				} else if (isSqlite(path)) {
					const result = await current(blobForPreview(key));
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await current(result.blob.arrayBuffer());
					fileSqliteData = arrayBuffer;
				} else if (ext === 'docx') {
					const preview =
						activeTerminal && !signedDelivery
							? await downloadFilePreview(activeTerminal, path)
							: null;
					if (preview) {
						filePdfData = await current(preview.blob.arrayBuffer());
					} else {
						const result = await current(blobForPreview(key));
						if (!result) throw new Error(t('Preview failed'));
						fileDocxData = await current(result.blob.arrayBuffer());
					}
				} else if (ext === 'xlsx' || ext === 'xls') {
					const result = await current(blobForPreview(key));
					if (!result) throw new Error(t('Preview failed'));
					const arrayBuffer = await current(result.blob.arrayBuffer());
					const { readExcelWorkbook } = await import('$lib/utils/excelToTable');
					const workbook = await current(readExcelWorkbook(arrayBuffer));
					if (workbook.SheetNames.length > 0) {
						selectedExcelSheet = workbook.SheetNames[0];
						excelWorkbook = workbook;
					}
				} else if (ext === 'pptx') {
					const preview =
						activeTerminal && !signedDelivery
							? await downloadFilePreview(activeTerminal, path)
							: null;
					if (preview) {
						filePdfData = await current(preview.blob.arrayBuffer());
					} else {
						const result = await current(blobForPreview(key));
						if (!result) throw new Error(t('Preview failed'));
						const arrayBuffer = await current(result.blob.arrayBuffer());
						const { pptxToImages } = await import('$lib/utils/pptxToHtml');
						const resultImages = await current(pptxToImages(arrayBuffer));
						fileOfficeSlides = resultImages.images;
					}
				}
			} else {
				if (signedDelivery || fileId) {
					const result = await current(blobForPreview(key));
					fileContent = result ? await current(result.blob.text()) : null;
				} else if (activeTerminal) {
					fileContent = await current(readFile(activeTerminal, path));
				}
			}
		} catch (e) {
			if (previewIsCurrent(key)) error = e instanceof Error ? e.message : t('Preview failed');
		} finally {
			loading = false;
		}
	}

	function openInFiles() {
		if (unavailable || !path) return;
		showTabs.set(true);
		showFileNavPath.set(targetPage ? { path, page: targetPage } : path);
	}

	async function downloadFile() {
		if (unavailable || !path) return;
		const key = previewKey;
		const result = await blobForPreview(key);
		if (!previewIsCurrent(key)) return;
		if (!result) {
			toast.error(t('Download failed'));
			return;
		}

		const url = URL.createObjectURL(result.blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = result.filename;
		a.click();
		URL.revokeObjectURL(url);
	}

	onDestroy(() => {
		disposed = true;
		clearPreview();
	});
</script>

<div
	class="my-2 w-full overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-white/8 dark:bg-gray-950/20"
>
	<div
		class="flex h-8 items-center {expanded ? 'border-b border-gray-100 dark:border-white/8' : ''}"
	>
		<button
			type="button"
			class="flex h-full min-w-0 flex-1 items-center gap-2 px-2.5 text-left"
			on:click={() => (expanded = !expanded)}
			aria-expanded={expanded}
		>
			<div
				class="flex size-5 shrink-0 items-center justify-center text-gray-500 dark:text-gray-400"
			>
				<Icon name={fileIconName(name || path || '', 'file')} size={14} />
			</div>
			<div class="min-w-0 flex-1 truncate text-xs font-medium text-gray-800 dark:text-gray-100">
				{name}
			</div>
		</button>

		{#if !signedDelivery || deliveryOwner}
			{#if getExt(path) === 'svg' && fileContent !== null}
				<button
					type="button"
					class="mr-1 rounded px-1.5 py-1 text-[0.6875rem] text-gray-500 transition-colors hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
					on:click|stopPropagation={() => (showRaw = !showRaw)}
					aria-label={showRaw ? t('Preview') : t('Source')}
				>
					{showRaw ? t('Preview') : t('Source')}
				</button>
			{/if}
			<button
				type="button"
				class="mr-1 flex size-6 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:text-gray-700 disabled:opacity-40 disabled:hover:text-gray-400 dark:text-gray-500 dark:hover:text-gray-200 dark:disabled:hover:text-gray-500"
				disabled={unavailable}
				on:click|stopPropagation={downloadFile}
				aria-label={t('Download')}
			>
				<Icon name="download" size={13} />
			</button>
			{#if !signedDelivery && !fileId}
				<button
					type="button"
					class="mr-1 flex size-6 shrink-0 items-center justify-center rounded text-gray-400 transition-colors hover:text-gray-700 disabled:opacity-40 disabled:hover:text-gray-400 dark:text-gray-500 dark:hover:text-gray-200 dark:disabled:hover:text-gray-500"
					disabled={unavailable}
					on:click|stopPropagation={openInFiles}
					aria-label={t('Open')}
				>
					<Icon name="external-link" size={13} />
				</button>
			{/if}
		{/if}
	</div>

	{#if expanded}
		{#if unavailable}
			<div class="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
				{t('Terminal unavailable')}
			</div>
		{:else if error}
			<div class="px-4 py-3 text-xs text-red-500 dark:text-red-400">{error}</div>
		{:else}
			<div
				class="{previewClass} max-h-[75vh] min-h-24 resize-y overflow-hidden bg-gray-50 dark:bg-gray-950"
			>
				<FilePreview
					selectedFile={path}
					fileLoading={loading}
					{fileImageUrl}
					{fileVideoUrl}
					{fileAudioUrl}
					{filePdfData}
					{fileSqliteData}
					{fileModelData}
					{fileDocxData}
					{fileContent}
					target={fileId ? null : terminal}
					{chatId}
					{excelWorkbook}
					{fileOfficeSlides}
					{currentSlide}
					{targetPage}
					bind:selectedExcelSheet
					readOnly={true}
					bind:showRaw
				/>
				{#if !loading && fileImageUrl === null && fileVideoUrl === null && fileAudioUrl === null && filePdfData === null && fileSqliteData === null && fileModelData === null && fileDocxData === null && fileContent === null && excelWorkbook === null && fileOfficeSlides === null}
					<div
						class="flex h-full items-center justify-center px-3 text-xs text-gray-500 dark:text-gray-400"
					>
						{t('No preview available')}
					</div>
				{/if}
			</div>
		{/if}
	{/if}
</div>
