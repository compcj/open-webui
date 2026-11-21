<script lang="ts">
	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import Dropdown from '../../common/Dropdown.svelte';
	import DropdownMenu from '../../common/DropdownMenu.svelte';
	import Switch from '../../common/Switch.svelte';
	import ChromeBrowser from './ChromeBrowser.svelte';
	import {
		getBrowserSession,
		updateBrowserSession,
		browserViewUrl,
		browserFrameUrl,
		type BrowserSession
	} from '$lib/apis/terminal/browser';
	import { TerminalRequestError, type TerminalTarget } from '$lib/apis/terminal';
	import PortList from '../FileNav/PortList.svelte';
	import { config, user } from '$lib/stores';
	import { previewTicketToken, requestPreviewTicket } from '$lib/apis/terminal/preview';
	import Icon from '../FileNav/Icon.svelte';
	import Tooltip from '../../common/Tooltip.svelte';

	const i18n = getContext<any>('i18n');
	export let target: TerminalTarget | null = null;
	export let baseUrl = '';
	$: connection = target;
	$: chatId = target?.context.chatId ?? null;
	export let active = true;
	export let initialUrl = '';
	export let sessionId = '';
	export let onUnavailable: (message: string, status?: number) => void = () => {};
	let ready: Promise<void> = Promise.resolve();
	export async function bindChat(id: string) {
		await ready;
		if (sessionChatId === id) return;
		if (sessionChatId || !session || !sessionConnection)
			throw new Error('Browser is not connected');
		await updateBrowserSession(sessionConnection, session.id, { chat_id: id });
		sessionConnection = {
			...sessionConnection,
			context: { ...sessionConnection.context, chatId: id }
		};
		sessionChatId = id;
	}
	export let overlay = false;
	export let onNavigate: (url: string, label: string) => void = () => {};
	export let onOpenBrowser: (url: string) => void = () => {};
	export let onClose: (() => void) | null = null;
	let session: BrowserSession | null = null;
	let sessionChatId = target?.context.chatId ?? null;
	let sessionConnection = target;
	let chrome: ChromeBrowser;
	let switching = false;
	let deviceMode: 'desktop' | 'mobile' = 'desktop';
	let optionsOpen = false;
	let quality: 'low' | 'balanced' | 'crisp' = 'balanced';
	let chromeBack = false;
	let chromeForward = false;
	let frameReady = false;
	let chromeToken = '';
	let stopChromeAuthorization: (() => void) | undefined;
	$: chromeSessionId = session?.mode === 'chrome' ? session.id : null;
	$: viewUrl =
		session && sessionConnection && chromeToken
			? browserViewUrl(sessionConnection, { ...session, token: chromeToken })
			: '';
	$: authorizeChrome(active, chromeSessionId, sessionConnection, sessionChatId, $user?.id);
	const authorizeChrome = (
		enabled: boolean,
		targetSession: string | null,
		targetConnection: TerminalTarget | null,
		targetChat: string | null,
		actor: string | undefined
	) => {
		stopChromeAuthorization?.();
		chromeToken = '';
		if (!enabled || !targetSession || !targetConnection || !actor) return;
		const request = new AbortController();
		let renewal: ReturnType<typeof setTimeout>;
		let expiry: ReturnType<typeof setTimeout>;
		stopChromeAuthorization = () => {
			request.abort();
			clearTimeout(renewal);
			clearTimeout(expiry);
		};
		const refresh = async () => {
			try {
				const authorized = await getBrowserSession(targetConnection, targetSession, request.signal);
				if (request.signal.aborted) return;
				if (authorized.mode !== 'chrome' || !authorized.token)
					throw new Error('Browser session is unavailable');
				chromeToken = authorized.token;
				clearTimeout(expiry);
				expiry = setTimeout(() => {
					chromeToken = '';
				}, 5 * 60_000);
				renewal = setTimeout(refresh, 60_000);
			} catch {
				if (request.signal.aborted) return;
				chromeToken = '';
				loading = false;
				error = $i18n.t('Browser session is unavailable');
			}
		};
		void refresh();
	};
	$: if (session && sessionConnection) {
		void updateBrowserSession(sessionConnection, session.id, { active }).catch(() => {});
	}

	const persist = (url: string, title: string) => {
		if (session && sessionConnection)
			void updateBrowserSession(sessionConnection, session.id, { url, title }).catch(() => {});
	};
	const switchMode = async (mode: 'proxy' | 'chrome', force = false) => {
		if (!session || !sessionConnection || switching || (!force && session.mode === mode)) return;
		if (mode === 'proxy' && !previewAvailable) return;
		switching = true;
		try {
			session = await updateBrowserSession(sessionConnection, session.id, {
				mode,
				url: currentUrl
			});
			frameReady = false;
			if (session.mode === 'proxy') {
				history = [];
				historyIndex = -1;
			}
			error = '';
			return true;
		} catch (reason) {
			error = reason instanceof Error ? reason.message : String(reason);
			if (mode === 'chrome') {
				switching = false;
				await fallbackToProxy(error);
			}
		} finally {
			switching = false;
		}
	};
	const fallbackToProxy = async (message: string) => {
		if (!session || !sessionConnection || switching || !previewAvailable) return;
		// The server may already be in Chrome mode even if its startup request failed.
		const switched = await switchMode('proxy', true);
		connectionStatus = '';
		if (switched) {
			loading = false;
			error = `${$i18n.t('Chrome failed. Switched to Proxy.')} ${message}`;
		}
	};
	const frameCommand = (command: string, url?: string) =>
		previewTicket &&
		iframe?.contentWindow?.postMessage(
			{
				type: 'terminal-browser-command',
				command,
				url,
				preview_ticket: previewTicketToken(new URL(previewTicket))
			},
			'*'
		);
	const reload = () =>
		session?.mode === 'chrome'
			? chrome?.command('reload')
			: session
				? frameCommand('reload')
				: navigate(currentUrl, false);
	let urlInput = initialUrl;
	let currentUrl = '';
	let frameUrl = '';
	let previewTicket = '';
	let previewError = false;
	let stopPreview: (() => void) | undefined;
	let previewIdentity: string | null = null;
	let previewConnection: TerminalTarget | null = null;
	$: previewBase = $config?.features?.terminal_preview_base_url ?? '';
	$: previewAvailable = Boolean(previewBase && connection?.system);
	$: previewPort = localhostPort(currentUrl);
	$: managePreview(
		active && session?.mode === 'proxy' && previewAvailable,
		connection,
		session?.id,
		sessionChatId,
		previewBase,
		$user?.id,
		previewPort
	);

	const managePreview = (
		enabled: boolean,
		targetConnection: TerminalTarget | null,
		targetSession: string | undefined,
		targetChat: string | null,
		base: string,
		actor: string | undefined,
		port: number | null
	) => {
		const credential = targetConnection?.key;
		const scope = typeof credential === 'object' ? credential.temporaryContext : null;
		const identity =
			enabled && targetConnection && targetSession && actor
				? JSON.stringify([
						targetConnection.selector,
						targetConnection.baseUrl,
						targetConnection.context?.userId,
						targetConnection.context?.modelId,
						targetSession,
						targetChat,
						base,
						actor,
						port,
						scope?.id,
						scope?.epoch
					])
				: null;
		// Tab state updates recreate the connection descriptor. Keep the frame and use the latest
		// credential on renewal unless the authorized target or temporary scope actually changes.
		previewConnection = identity === null ? null : targetConnection;
		if (identity === previewIdentity) return;
		previewIdentity = identity;
		stopPreview?.();
		previewTicket = '';
		frameUrl = '';
		frameReady = false;
		previewError = false;
		if (identity === null || !targetSession) return;

		const request = new AbortController();
		let previous: string | undefined;
		let renew: ReturnType<typeof setTimeout>;
		let expire: ReturnType<typeof setTimeout>;
		stopPreview = () => {
			request.abort();
			clearTimeout(renew);
			clearTimeout(expire);
		};
		const refresh = async () => {
			const currentConnection = previewConnection;
			if (request.signal.aborted || !currentConnection) return;
			try {
				const ticket = await requestPreviewTicket(
					currentConnection,
					targetChat,
					port
						? { kind: 'port', port, session_id: targetSession }
						: { kind: 'browser', session_id: targetSession },
					base,
					request.signal,
					previous
				);
				if (request.signal.aborted) return;
				previous = ticket.url;
				const capability = new URL(ticket.url);
				const prefix = `${capability.origin}/p/${previewTicketToken(capability)}`;
				if (previewTicket !== prefix) {
					previewTicket = prefix;
					frameReady = false;
					frameUrl = currentUrl ? browserFrameUrl(prefix, currentUrl) : '';
				}
				clearTimeout(expire);
				expire = setTimeout(
					() => {
						previewTicket = '';
						frameUrl = '';
						frameReady = false;
						previewError = true;
					},
					Math.max(0, ticket.expires_at * 1000 - Date.now())
				);
				renew = setTimeout(refresh, 60_000);
			} catch {
				if (request.signal.aborted) return;
				clearTimeout(expire);
				previewTicket = '';
				frameUrl = '';
				frameReady = false;
				loading = false;
				previewError = true;
			}
		};
		void refresh();
	};
	onDestroy(() => {
		stopPreview?.();
		stopChromeAuthorization?.();
	});
	let frameKey = 0;
	let iframe: HTMLIFrameElement;
	let addressInput: HTMLInputElement;
	let history: string[] = [];
	let historyIndex = -1;
	let replaceHistory = false;
	let error = '';
	let connectionStatus = '';
	let loading = false;
	$: canGoBack = session?.mode === 'chrome' ? chromeBack : historyIndex > 0;
	$: canGoForward = session?.mode === 'chrome' ? chromeForward : historyIndex < history.length - 1;

	const address = (value: string): URL => {
		const input = value.trim();
		const hasScheme = /^[a-z][a-z\d+.-]*:/i.test(input) && !/^[^/]+:\d+(?:[/?#]|$)/.test(input);
		const local = /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?=[:/?#]|$)/i.test(input);
		const url =
			/^[/?#]/.test(input) && currentUrl
				? new URL(input, currentUrl)
				: new URL(hasScheme ? input : `${local ? 'http' : 'https'}://${input}`);
		if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password)
			throw new Error($i18n.t('Enter an HTTP or HTTPS URL without credentials.'));
		if (
			(previewBase && url.origin === new URL(previewBase).origin) ||
			(previewTicket && url.href.includes(previewTicketToken(new URL(previewTicket))))
		)
			throw new Error($i18n.t('Terminal preview unavailable'));
		return url;
	};
	const localhostPort = (value: string) => {
		if (!value) return null;
		const url = new URL(value);
		return url.protocol === 'http:' &&
			['localhost', '127.0.0.1', '0.0.0.0', '[::1]'].includes(url.hostname)
			? Number(url.port || 80)
			: null;
	};
	const remember = (url: string, replace = false) => {
		if (replace && historyIndex >= 0) {
			history[historyIndex] = url;
			return;
		}
		if (history[historyIndex] === url) return;
		history = [...history.slice(0, historyIndex + 1), url];
		historyIndex = history.length - 1;
	};
	const navigate = (value = urlInput, addHistory = true) => {
		if (!value.trim()) return;
		try {
			const url = address(value);
			const port = localhostPort(url.href);
			// Port tickets are bound to one host and port.
			if (session?.mode === 'proxy' && port !== previewPort) frameReady = false;
			currentUrl = port
				? `http://localhost:${port}${url.pathname}${url.search}${url.hash}`
				: url.href;
			urlInput = currentUrl;
			if (session?.mode === 'proxy') replaceHistory = !addHistory;
			if (session?.mode === 'chrome') chrome?.command('navigate', { url: url.href });
			else if (session && frameReady) frameCommand(addHistory ? 'navigate' : 'replace', currentUrl);
			else if (previewTicket && port === previewPort) {
				frameUrl = browserFrameUrl(previewTicket, currentUrl);
				frameKey++;
			}
			persist(currentUrl, url.host);
			loading = true;
			error = '';
			onNavigate(currentUrl, url.host);
		} catch (reason) {
			error = reason instanceof Error ? reason.message : String(reason);
		}
	};
	const move = (step: number) => {
		if (session?.mode === 'chrome') {
			chrome?.command(step < 0 ? 'back' : 'forward');
			return;
		}
		const next = historyIndex + step;
		if (next < 0 || next >= history.length) return;
		historyIndex = next;
		const destination = history[next];
		if (session && frameReady && localhostPort(destination) === previewPort) {
			frameCommand(step < 0 ? 'back' : 'forward');
		} else {
			navigate(destination, false);
		}
	};
	const loaded = () => {
		loading = false;
		frameReady = true;
	};
	const receiveNavigation = (event: MessageEvent) => {
		if (event.source !== iframe?.contentWindow || event.origin !== 'null' || !previewTicket) return;
		const data = event.data;
		if (
			!data ||
			data.preview_ticket !== previewTicketToken(new URL(previewTicket)) ||
			!['terminal-browser-popup', 'terminal-browser-navigate', 'terminal-browser-state'].includes(
				data.type
			) ||
			typeof data.url !== 'string' ||
			data.url.length > 8192 ||
			(data.replace !== undefined && typeof data.replace !== 'boolean') ||
			(data.title !== undefined && (typeof data.title !== 'string' || data.title.length > 1024))
		)
			return;
		try {
			const url = address(data.url);
			if (data.type === 'terminal-browser-popup') {
				onOpenBrowser(url.href);
				return;
			}
			if (data.type === 'terminal-browser-navigate') {
				navigate(url.href, !data.replace);
			} else if (data.type === 'terminal-browser-state') {
				if (currentUrl !== url.href || document.activeElement !== addressInput) urlInput = url.href;
				currentUrl = url.href;
				remember(url.href, replaceHistory || data.replace === true);
				replaceHistory = false;
				persist(url.href, typeof data.title === 'string' ? data.title : url.host);
				onNavigate(
					url.href,
					typeof data.title === 'string' && data.title.trim() ? data.title : url.host
				);
			}
		} catch {
			/* Ignore malformed messages from page content. */
		}
	};
	onMount(() => {
		let disposed = false;
		const release = () => {
			if (session && sessionConnection)
				void updateBrowserSession(sessionConnection, session.id, {
					active: false
				}).catch(() => {});
		};
		window.addEventListener('message', receiveNavigation);
		window.addEventListener('pagehide', release);
		ready = (async () => {
			try {
				if (!sessionConnection || !sessionId) throw new Error('Session no longer available');
				const restored = await getBrowserSession(sessionConnection, sessionId);
				if (disposed) return;
				session = restored;
				const port = localhostPort(restored.url);
				const restoredUrl = restored.url ? address(restored.url) : null;
				currentUrl =
					port && restoredUrl
						? `http://localhost:${port}${restoredUrl.pathname}${restoredUrl.search}${restoredUrl.hash}`
						: restored.url;
				urlInput = currentUrl;
				if (restored.mode === 'proxy' && restored.url) remember(restored.url);
				onNavigate(restored.url, restored.title || restored.url || 'Browser');
			} catch (reason) {
				if (disposed) return;
				error = String(reason);
				onUnavailable(error, reason instanceof TerminalRequestError ? reason.status : undefined);
			}
		})();
		return () => {
			disposed = true;
			window.removeEventListener('message', receiveNavigation);
			window.removeEventListener('pagehide', release);
			release();
		};
	});
</script>

<div class="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
	<div class="flex min-w-0 shrink-0 items-center gap-1 px-1.5 py-[3px] sm:px-2">
		{#each [{ name: 'Back', icon: 'chevron-left', disabled: !canGoBack, action: () => move(-1) }, { name: 'Forward', icon: 'chevron-right', disabled: !canGoForward, action: () => move(1) }, { name: 'Refresh', icon: 'refresh', disabled: !currentUrl, action: reload }] as button}
			<Tooltip content={$i18n.t(button.name)}
				><button
					type="button"
					class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:pointer-events-none disabled:opacity-35 dark:hover:bg-white/6 dark:hover:text-gray-300"
					disabled={button.disabled}
					aria-label={$i18n.t(button.name)}
					on:click={button.action}
					><Icon
						name={button.icon}
						size={12}
						class={button.icon === 'refresh' && loading ? 'animate-spin' : ''}
					/></button
				></Tooltip
			>
		{/each}
		<form class="relative min-w-0 flex-1" on:submit|preventDefault={() => navigate()}>
			<input
				bind:this={addressInput}
				bind:value={urlInput}
				on:click={() => addressInput.select()}
				aria-label={$i18n.t('URL')}
				placeholder="https://example.com"
				spellcheck="false"
				class="h-7 w-full min-w-0 rounded-full bg-transparent py-1 pr-8 pl-3 text-left text-xs font-normal text-gray-700 outline-none transition-colors hover:bg-gray-100/60 focus:bg-gray-100 sm:px-8 sm:text-center dark:text-gray-300 dark:hover:bg-white/5 dark:focus:bg-white/8"
			/>
		</form>
		{#if session}
			<Dropdown align="end" bind:show={optionsOpen}>
				<button
					type="button"
					aria-label={$i18n.t('Browser options')}
					class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700 disabled:pointer-events-none disabled:opacity-35 dark:hover:bg-white/6 dark:hover:text-gray-300"
					><Icon name="three-dots" size={12} /></button
				>
				<div slot="content">
					<DropdownMenu className="w-48 font-normal">
						{#each [...(session.chrome_available ? ['chrome'] : []), ...(previewAvailable ? ['proxy'] : [])] as mode}
							<button
								type="button"
								role="menuitemradio"
								aria-checked={session.mode === mode}
								disabled={switching}
								on:click={() => {
									optionsOpen = false;
									void switchMode(mode as 'proxy' | 'chrome');
								}}
								class="font-normal disabled:opacity-40"
							>
								<span class="flex-1 text-left"
									>{mode === 'chrome' ? 'Chrome' : $i18n.t('Proxy')}</span
								>
								{#if session.mode === mode}<Icon name="check" size={12} />{/if}
							</button>
						{/each}
						{#if session.mode === 'chrome'}
							<hr class="border-gray-100 dark:border-gray-800" />
							{#each [{ value: 'low', label: 'Low' }, { value: 'balanced', label: 'Balanced' }, { value: 'crisp', label: 'Crisp' }] as item}
								<button
									type="button"
									role="menuitemradio"
									aria-checked={quality === item.value}
									on:click={() => {
										quality = item.value as typeof quality;
										optionsOpen = false;
									}}
									class="font-normal"
								>
									<span class="flex-1 text-left">{$i18n.t(item.label)}</span>
									{#if quality === item.value}<Icon name="check" size={12} />{/if}
								</button>
							{/each}
							<hr class="border-gray-100 dark:border-gray-800" />
							<button
								type="button"
								role="menuitemcheckbox"
								aria-checked={deviceMode === 'mobile'}
								class="font-normal"
								on:click={() => (deviceMode = deviceMode === 'mobile' ? 'desktop' : 'mobile')}
							>
								<span class="flex-1 whitespace-nowrap text-left">{$i18n.t('Mobile view')}</span>
								<div class="shrink-0" inert>
									<Switch state={deviceMode === 'mobile'} />
								</div>
							</button>
						{/if}
					</DropdownMenu>
				</div>
			</Dropdown>
		{/if}

		{#if onClose}<button
				type="button"
				aria-label={$i18n.t('Close')}
				class="flex size-5 shrink-0 items-center justify-center text-gray-500 dark:text-gray-400"
				on:click={onClose}><Icon name="xmark" size={11} /></button
			>{/if}
	</div>
	{#if error}
		<p role="alert" class="px-2 py-1 text-xs text-gray-500 dark:text-gray-400">{error}</p>
	{:else if connectionStatus}
		<p role="status" class="px-2 py-1 text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t(connectionStatus)}
		</p>
	{/if}
	<div class="relative min-h-0 flex-1">
		{#if overlay}<div class="absolute inset-0 z-10"></div>{/if}
		{#if session?.mode === 'chrome' && viewUrl}
			<div class="h-full" class:hidden={!currentUrl}>
				<ChromeBrowser
					bind:this={chrome}
					streamUrl={`${viewUrl}/stream`}
					{active}
					mobile={deviceMode === 'mobile'}
					{quality}
					onError={(message) => (error = message)}
					onFailure={(message) => {
						if (chromeToken) void fallbackToProxy(message);
					}}
					onStatus={(message) => (connectionStatus = message)}
					onState={(state) => {
						if (!state.url) return;
						if (currentUrl !== state.url || document.activeElement !== addressInput)
							urlInput = state.url;
						currentUrl = state.url;
						chromeBack = state.canGoBack;
						chromeForward = state.canGoForward;
						loading = false;
						onNavigate(state.url, state.title.trim() || new URL(state.url).host);
					}}
				/>
			</div>
		{:else if frameUrl && previewAvailable}
			{#key frameKey}<iframe
					bind:this={iframe}
					src={frameUrl}
					title={$i18n.t('Browser')}
					class="block h-full w-full border-0 bg-white"
					sandbox="allow-scripts allow-forms allow-downloads"
					referrerpolicy="no-referrer"
					on:load={loaded}
				></iframe>{/key}
		{:else if session?.mode === 'proxy' && (!previewAvailable || previewError)}
			<div class="p-4 text-xs text-gray-500" role="status">
				{previewError
					? $i18n.t('Terminal preview unavailable')
					: $i18n.t(
							'Proxy previews require an independent preview host. Use Chrome mode to browse.'
						)}
			</div>
		{/if}
		{#if !currentUrl && !frameUrl}<div class="flex h-full overflow-y-auto p-6">
				<div class="m-auto w-full max-w-xs">
					<div class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
						<Icon name="browser" size={18} strokeWidth={1.4} class="text-gray-400" />
						{$i18n.t('Browser')}
					</div>
					{#if connection}
						<div class="mt-3">
							<PortList
								target={connection}
								{active}
								on:previewPort={(event) => navigate(`http://localhost:${event.detail}/`)}
							/>
						</div>
					{/if}
				</div>
			</div>{/if}
	</div>
</div>
