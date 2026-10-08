<script lang="ts">
	import { createSkillDiscoveryLoader } from '$lib/utils/skill-discovery';
	import { resolveLocalizedResource } from '$lib/utils/localizedContent';
	import { getContext, onDestroy } from 'svelte';
	import { getSkillItems } from '$lib/apis/skills';
	import {
		listTerminalSkills,
		resolveTerminalConnection,
		type TerminalSkill
	} from '$lib/apis/terminal';
	import {
		chatId,
		selectedTerminalId,
		settings,
		terminalServers,
		terminalSkills
	} from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Cube from '$lib/components/icons/Cube.svelte';

	const i18n: any = getContext('i18n');

	export let query = '';
	export let onSelect = (e) => {};

	let selectedIdx = 0;
	export let filteredItems = [];

	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			getItems();
		}, 200);
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	let itemRequestId = 0;
	let previousTerminalId: string | null = $selectedTerminalId;
	$: if ($selectedTerminalId !== previousTerminalId) {
		previousTerminalId = $selectedTerminalId;
		itemRequestId += 1;
		filteredItems = [];
		getItems();
	}
	const getItems = async () => {
		const requestId = ++itemRequestId;
		const terminalId = $selectedTerminalId;
		const [res, terminalItems] = await Promise.all([
			getSkillItems(localStorage.token, query).catch(() => null),
			getTerminalItems(query)
		]);
		if (requestId !== itemRequestId || terminalId !== $selectedTerminalId) return;
		filteredItems = [...(res?.items ?? []), ...terminalItems];
	};

	const loadTerminalSkillItems = createSkillDiscoveryLoader<TerminalSkill>(
		() => JSON.stringify([$selectedTerminalId, $chatId, localStorage.token]),
		(items) => terminalSkills.set(items)
	);
	const getTerminalItems = async (query = ''): Promise<TerminalSkill[]> => {
		const connection = resolveTerminalConnection(
			$selectedTerminalId,
			$terminalServers ?? [],
			$settings?.terminalServers ?? [],
			localStorage.token
		);
		const items = await loadTerminalSkillItems(() =>
			listTerminalSkills(connection, $chatId || null).catch(() => [])
		);
		const q = query.trim().toLowerCase();
		return q
			? items.filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(q))
			: items;
	};

	$: if (query) {
		selectedIdx = 0;
	}

	export const selectUp = () => {
		selectedIdx = Math.max(0, selectedIdx - 1);
	};

	export const selectDown = () => {
		selectedIdx = Math.min(selectedIdx + 1, filteredItems.length - 1);
	};

	export const select = async () => {
		const skill = filteredItems[selectedIdx];
		if (skill) {
			onSelect({ type: 'skill', data: skill });
		}
	};

	const escapeTooltipText = (value = '') =>
		String(value)
			.replaceAll('&', '&amp;')
			.replaceAll('<', '&lt;')
			.replaceAll('>', '&gt;')
			.replaceAll('"', '&quot;')
			.replaceAll("'", '&#39;');

	const getTooltipContent = (skill) => {
		const name = escapeTooltipText(resolveLocalizedResource(skill, $i18n.language));
		const description = escapeTooltipText(
			resolveLocalizedResource(skill, $i18n.language, 'description')
		);

		return `<div class="max-w-80 whitespace-normal text-left leading-snug">
			<span class="break-words font-normal">${name}</span>${description ? `: <span class="break-words opacity-80">${description}</span>` : ''}
		</div>`;
	};
</script>

<div class="px-2 py-1 text-[0.6875rem] text-gray-500 dark:text-gray-400">
	{$i18n.t('Skills')}
</div>

{#if filteredItems.length > 0}
	{#each filteredItems as skill, skillIdx}
		<Tooltip
			content={getTooltipContent(skill)}
			placement="top-start"
			tippyOptions={{ maxWidth: '20rem' }}
		>
			<button
				class="flex h-[1.6875rem] w-full items-center rounded-xl px-2 text-left text-[0.8125rem] hover:bg-gray-50/40 dark:hover:bg-gray-800/40 {skillIdx ===
				selectedIdx
					? 'bg-gray-50/40 dark:bg-gray-800/40 selected-command-option-button'
					: ''}"
				type="button"
				on:click={() => {
					onSelect({ type: 'skill', data: skill });
				}}
				on:mousemove={() => {
					selectedIdx = skillIdx;
				}}
				on:focus={() => {}}
				data-selected={skillIdx === selectedIdx}
			>
				<div class="flex w-full min-w-0 items-center text-black dark:text-gray-100">
					<div class="mr-2 flex size-4.5 shrink-0 items-center justify-center">
						<Cube className="size-3.5" />
					</div>
					<div class="truncate min-w-0 flex-1">
						{resolveLocalizedResource(skill, $i18n.language)}
					</div>
					<div class="ml-2 max-w-24 shrink-0 truncate text-xs text-gray-500 dark:text-gray-400">
						{skill.source === 'terminal' ? $i18n.t('Terminal') : skill.id}
					</div>
				</div>
			</button>
		</Tooltip>
	{/each}
{/if}
