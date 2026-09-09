<script lang="ts">
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';
	const { saveAs } = fileSaver;

	dayjs.extend(relativeTime);

	import { onMount, getContext, tick, onDestroy } from 'svelte';
	const i18n = getContext('i18n');

	import {
		WEBUI_NAME,
		user,
		skills as _skills,
		workspaceActions,
		workspaceCounts
	} from '$lib/stores';
	import { goto } from '$app/navigation';
	import {
		getSkills,
		getSkillById,
		getSkillItems,
		exportSkills,
		createNewSkill,
		deleteSkillById,
		toggleSkillById,
		loadSkillFromUrl
	} from '$lib/apis/skills';
	import { capitalizeFirstLetter } from '$lib/utils';
	import {
		parseSkillMarkdown,
		openclawToSkill,
		skillToOpenclawMarkdown,
		extractSkillFromZip,
		listSkillsInZip,
		buildSkillZip,
		extractGating,
		base64ToBlob,
		getOpenclaw,
		type SkillFileEntry,
		type SkillZipListEntry
	} from '$lib/utils/skills';
	import TagInput from '$lib/components/common/Tags/TagInput.svelte';

	import Tooltip from '../common/Tooltip.svelte';
	import ConfirmDialog from '../common/ConfirmDialog.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Modal from '../common/Modal.svelte';
	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import GarbageBin from '../icons/GarbageBin.svelte';
	import Search from '../icons/Search.svelte';
	import XMark from '../icons/XMark.svelte';
	import Spinner from '../common/Spinner.svelte';
	import ViewSelector from './common/ViewSelector.svelte';
	import Badge from '$lib/components/common/Badge.svelte';
	import Switch from '../common/Switch.svelte';
	import SkillMenu from './Skills/SkillMenu.svelte';
	import Pagination from '../common/Pagination.svelte';
	import ChevronDown from '../icons/ChevronDown.svelte';
	import ChevronUp from '../icons/ChevronUp.svelte';

	let shiftKey = false;
	let loaded = false;

	let importFiles;
	let importInputElement: HTMLInputElement;

	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let selectedSkill = null;
	let showDeleteConfirm = false;

	let showUrlImport = false;
	let urlImportValue = '';
	let urlImportLoading = false;

	let showSkillPicker = false;
	let skillPickerItems: SkillZipListEntry[] = [];
	let skillPickerBlob: Blob | null = null;
	let skillPickerSource: Record<string, any> | null = null;

	let filteredItems = null;
	let total = null;
	let loading = false;

	let tagsContainerElement: HTMLDivElement;
	let viewOption = '';
	let sortKey = 'updated_at';
	let sortDirection = 'desc';
	let openSkillMenuId: string | null = null;
	let page = 1;

	$: if (loaded) {
		workspaceActions.set([
			{
				id: 'skills-new',
				label: $i18n.t('Create'),
				href: '/workspace/skills/create',
				visible: $user?.role === 'admin' || $user?.permissions?.workspace?.skills
			},
			{
				id: 'skills-import',
				label: $i18n.t('Import'),
				onClick: () => importInputElement?.click(),
				visible: $user?.role === 'admin' || $user?.permissions?.workspace?.skills_import
			},
			{
				id: 'skills-import-url',
				label: $i18n.t('Import from URL'),
				onClick: () => {
					urlImportValue = '';
					showUrlImport = true;
				},
				visible: $user?.role === 'admin' || $user?.permissions?.workspace?.skills_import
			},
			{
				id: 'skills-export',
				label: $i18n.t('Export JSON'),
				onClick: async () => {
					const _skills = await exportSkills(localStorage.token).catch((error) => {
						toast.error(`${error}`);
						return null;
					});
					if (_skills) {
						let blob = new Blob([JSON.stringify(_skills)], {
							type: 'application/json'
						});
						saveAs(blob, `skills-export-${Date.now()}.json`);
					}
				},
				visible: $user?.role === 'admin' || $user?.permissions?.workspace?.skills_export
			}
		]);
	}

	const loadSkillItems = async () => {
		if (!loaded) return;

		loading = true;
		try {
			const res = await getSkillItems(
				localStorage.token,
				query,
				viewOption,
				page,
				sortKey,
				sortDirection
			).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				filteredItems = res.items;
				total = res.total;
				workspaceCounts.update((counts) => ({ ...counts, skills: total }));
			}
		} catch (err) {
			console.error(err);
		} finally {
			loading = false;
		}
	};

	const handleSearchInput = () => {
		loading = true;
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			if (page !== 1) {
				page = 1;
			} else {
				loadSkillItems();
			}
		}, 300);
	};

	// Immediate response to page/filter changes
	$: if (
		loaded &&
		page &&
		viewOption !== undefined &&
		sortKey !== undefined &&
		sortDirection !== undefined
	) {
		loadSkillItems();
	}

	const setSortKey = (key: string) => {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
		} else {
			sortKey = key;
			sortDirection = key === 'updated_at' ? 'desc' : 'asc';
		}
	};

	const openSkill = (skill) => {
		goto(`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`);
	};

	const shouldIgnoreRowClick = (target: EventTarget | null) => {
		return target instanceof Element && !!target.closest('button, a, input, [role="menu"]');
	};

	const cloneHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (_skill) {
			sessionStorage.skill = JSON.stringify({
				..._skill,
				id: `${_skill.id}_clone`,
				name: `${_skill.name} (Clone)`
			});
			goto('/workspace/skills/create');
		}
	};

	const exportHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (_skill) {
			let blob = new Blob([JSON.stringify([_skill])], {
				type: 'application/json'
			});
			saveAs(blob, `skill-${_skill.id}-export-${Date.now()}.json`);
		}
	};

	const exportSkillMdHandler = async (skill) => {
		const _skill = await getSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (_skill) {
			const files = _skill?.meta?.openclaw?.files ?? {};
			if (Object.keys(files).length > 0) {
				const blob = await buildSkillZip(_skill);
				saveAs(blob, `skill-${_skill.id}.zip`);
			} else {
				const blob = new Blob([skillToOpenclawMarkdown(_skill)], {
					type: 'text/markdown;charset=utf-8'
				});
				saveAs(blob, 'SKILL.md');
			}
		}
	};

	const prefillFromOpenclaw = (
		parsed,
		fallbackName,
		source,
		files: Record<string, SkillFileEntry> | null = null,
		skipped = 0
	) => {
		const skill = openclawToSkill(parsed, fallbackName, source);
		if (files && Object.keys(files).length > 0) {
			skill.meta.openclaw.files = files;
		}

		const gating = extractGating(skill);
		if (gating && (gating.bins.length > 0 || gating.anyBins.length > 0)) {
			toast.info($i18n.t('Requires CLI tools in the terminal environment'));
		}
		if (skipped > 0) {
			toast.info($i18n.t('{{count}} files were skipped (too large)', { count: skipped }));
		}

		sessionStorage.skill = JSON.stringify({
			...skill,
			is_active: true,
			access_grants: []
		});
		goto('/workspace/skills/create');
	};

	const importZipCandidate = async (
		blob: Blob,
		candidate: SkillZipListEntry,
		source: any,
		fileName: string | null = null
	) => {
		const { skillMarkdown, files, skipped, dirName } = await extractSkillFromZip(blob, {
			path: candidate.dirName
		});
		const parsed = parseSkillMarkdown(skillMarkdown);
		if (!parsed) {
			toast.error($i18n.t('Invalid SKILL.md file'));
			return;
		}
		const fallbackName = dirName.split('/').pop() || (fileName ?? 'skill').replace(/\.zip$/i, '');
		prefillFromOpenclaw(parsed, fallbackName, source, files, skipped);
	};

	const pickSkillHandler = async (item: SkillZipListEntry) => {
		const blob = skillPickerBlob;
		const source = skillPickerSource;
		showSkillPicker = false;
		skillPickerBlob = null;
		skillPickerItems = [];
		skillPickerSource = null;
		if (!blob) return;
		try {
			await importZipCandidate(blob, item, source);
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const urlImportHandler = async () => {
		const url = urlImportValue.trim();
		if (!url || urlImportLoading) return;

		urlImportLoading = true;
		try {
			const res = await loadSkillFromUrl(localStorage.token, url).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (res) {
				if (res.format === 'zip') {
					const blob = base64ToBlob(res.content);
					const source = res.source ?? { type: 'url', url };
					const candidates = await listSkillsInZip(blob);

					if (candidates.length === 0) {
						toast.error($i18n.t('SKILL.md not found in archive'));
						return;
					}

					let chosen = null;
					const select = res.select;
					if (select?.path) {
						const suffix = `${select.path}/SKILL.md`;
						chosen =
							candidates.find((c) => c.path === suffix || c.path.endsWith(`/${suffix}`)) ?? null;
					} else if (select?.name) {
						chosen =
							candidates.find((c) => (c.dirName.split('/').pop() ?? '') === select.name) ??
							candidates.find((c) => c.name === select.name) ??
							null;
					}
					if (!chosen && candidates.length === 1) {
						chosen = candidates[0];
					}

					if (!chosen) {
						// Multi-skill archive without a clear target: let the user pick
						skillPickerBlob = blob;
						skillPickerItems = candidates;
						skillPickerSource = source;
						showUrlImport = false;
						showSkillPicker = true;
						return;
					}

					await importZipCandidate(blob, chosen, source, res.fileName);
				} else {
					const parsed = parseSkillMarkdown(res.content);
					if (!parsed) {
						toast.error($i18n.t('Invalid SKILL.md file'));
						return;
					}
					const fallbackName = (res.fileName ?? 'SKILL.md').replace(/\.md$/i, '');
					prefillFromOpenclaw(parsed, fallbackName, res.source ?? { type: 'url', url });
				}
				showUrlImport = false;
			}
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			urlImportLoading = false;
		}
	};

	const deleteHandler = async (skill) => {
		const res = await deleteSkillById(localStorage.token, skill.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Skill deleted successfully'));
		}

		page = 1;
		loadSkillItems();
		await _skills.set(await getSkills(localStorage.token));
	};

	onMount(async () => {
		viewOption = localStorage?.workspaceViewOption || '';
		loaded = true;

		const onKeyDown = (event) => {
			if (event.key === 'Shift') {
				shiftKey = true;
			}
		};

		const onKeyUp = (event) => {
			if (event.key === 'Shift') {
				shiftKey = false;
			}
		};

		const onBlur = () => {
			shiftKey = false;
		};

		window.addEventListener('keydown', onKeyDown);
		window.addEventListener('keyup', onKeyUp);
		window.addEventListener('blur', onBlur);

		return () => {
			clearTimeout(searchDebounceTimer);
			window.removeEventListener('keydown', onKeyDown);
			window.removeEventListener('keyup', onKeyUp);
			window.removeEventListener('blur', onBlur);
		};
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{$i18n.t('Skills')} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<input
		bind:this={importInputElement}
		bind:files={importFiles}
		type="file"
		accept=".md,.json,.zip"
		hidden
		on:change={() => {
			if (importFiles && importFiles.length > 0) {
				const file = importFiles[0];
				const ext = file.name.split('.').pop()?.toLowerCase();

				if (ext === 'json') {
					// JSON import: create skills via API
					const reader = new FileReader();
					reader.onload = async (event) => {
						try {
							const content = event.target?.result;
							if (typeof content !== 'string') return;

							const parsedSkills = JSON.parse(content);
							const items = Array.isArray(parsedSkills) ? parsedSkills : [parsedSkills];

							for (const skill of items) {
								await createNewSkill(localStorage.token, skill).catch((error) => {
									toast.error(`${error}`);
								});
							}

							toast.success($i18n.t('Skill imported successfully'));
							page = 1;
							loadSkillItems();
							_skills.set(await getSkills(localStorage.token));
						} catch (e) {
							toast.error($i18n.t('Invalid JSON file'));
						}
					};
					reader.readAsText(file);
				} else if (ext === 'zip') {
					// Zip import: extract SKILL.md and bundled files, then open in editor
					const reader = new FileReader();
					reader.onload = async (event) => {
						try {
							const data = event.target?.result;
							if (!(data instanceof ArrayBuffer)) return;

							const { skillMarkdown, files, skipped, dirName } = await extractSkillFromZip(data);
							const parsed = parseSkillMarkdown(skillMarkdown);
							if (!parsed) {
								toast.error($i18n.t('Invalid SKILL.md file'));
								return;
							}

							const fallbackName = dirName.split('/').pop() || file.name.replace(/\.zip$/i, '');
							prefillFromOpenclaw(parsed, fallbackName, { type: 'zip' }, files, skipped);
						} catch (error) {
							toast.error(`${error}`);
						}
					};
					reader.readAsArrayBuffer(file);
				} else {
					// Markdown import: parse frontmatter and open in editor
					const reader = new FileReader();
					reader.onload = (event) => {
						const mdContent = event.target?.result;
						if (typeof mdContent === 'string') {
							const fileName = file.name.replace(/\.md$/i, '');
							const parsed = parseSkillMarkdown(mdContent) ?? {
								frontmatter: {},
								body: mdContent
							};
							prefillFromOpenclaw(parsed, fileName, { type: 'file' });
						}
					};
					reader.readAsText(file);
				}

				importInputElement.value = '';
			}
		}}
	/>

	<div class="space-y-1">
		<div class="flex h-8 w-full items-center gap-2">
			<div class="flex min-w-0 flex-1">
				<div class=" self-center ml-1 mr-3">
					<Search className="size-3.5" />
				</div>
				<input
					class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
					bind:value={query}
					on:input={handleSearchInput}
					aria-label={$i18n.t('Search Skills')}
					placeholder={$i18n.t('Search Skills')}
				/>
				{#if query}
					<div class="self-center pl-1.5 translate-y-[0.5px] rounded-l-xl bg-transparent">
						<button
							class="p-0.5 rounded-full hover:bg-gray-100 dark:hover:bg-gray-900 transition"
							aria-label={$i18n.t('Clear search')}
							on:click={() => {
								query = '';
								handleSearchInput();
							}}
						>
							<XMark className="size-3" strokeWidth="2" />
						</button>
					</div>
				{/if}
			</div>

			<div
				class="flex max-w-[55%] shrink-0 overflow-x-auto scrollbar-none"
				bind:this={tagsContainerElement}
				on:wheel={(e) => {
					if (e.deltaY !== 0) {
						e.preventDefault();
						e.currentTarget.scrollLeft += e.deltaY;
					}
				}}
			>
				<div
					class="flex w-fit gap-0.5 text-center text-sm rounded-full bg-transparent whitespace-nowrap"
				>
					<ViewSelector
						bind:value={viewOption}
						align="end"
						onChange={async (value) => {
							localStorage.workspaceViewOption = value;
							page = 1;
							await tick();
						}}
					/>
				</div>
			</div>
		</div>

		{#if filteredItems === null || loading}
			<div class="w-full h-full flex justify-center items-center my-16 mb-24">
				<Spinner className="size-5" />
			</div>
		{:else if (filteredItems ?? []).length !== 0}
			<div class="my-1">
				<div
					class="flex w-full items-center gap-2 px-1.5 pb-0.5 text-xs text-gray-400 dark:text-gray-600"
				>
					<button
						class="flex min-w-0 flex-1 items-center gap-1 py-0.5 text-left"
						type="button"
						on:click={() => setSortKey('name')}
					>
						{$i18n.t('Title')}
						{#if sortKey === 'name'}
							{#if sortDirection === 'asc'}
								<ChevronUp className="size-2" />
							{:else}
								<ChevronDown className="size-2" />
							{/if}
						{/if}
					</button>

					<div class="hidden w-44 shrink-0 md:block"></div>

					<button
						class="flex w-36 shrink-0 items-center justify-end gap-1 py-0.5 text-right"
						type="button"
						on:click={() => setSortKey('updated_at')}
					>
						{$i18n.t('Updated at')}
						{#if sortKey === 'updated_at'}
							{#if sortDirection === 'asc'}
								<ChevronUp className="size-2" />
							{:else}
								<ChevronDown className="size-2" />
							{/if}
						{/if}
					</button>
				</div>

				<div class="grid gap-y-0.5">
					{#each filteredItems as skill (skill.id)}
						<div
							class="group flex min-h-8 w-full cursor-pointer items-center gap-2 overflow-hidden rounded-xl px-2 py-1 text-left"
							role="button"
							tabindex="0"
							on:click={(e) => {
								if (shouldIgnoreRowClick(e.target)) return;
								openSkill(skill);
							}}
							on:keydown={(e) => {
								if (e.currentTarget !== e.target) return;
								if (e.key === 'Enter' || e.key === ' ') {
									e.preventDefault();
									openSkill(skill);
								}
							}}
						>
							<div class="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
								<div class="flex min-w-0 flex-1 flex-col overflow-hidden">
									<div class="flex min-w-0 items-center gap-2 overflow-hidden">
										<div class="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
											<Tooltip content={skill.id} className="min-w-0" placement="top-start">
												<div
													class="truncate text-[0.8125rem] leading-5 text-gray-800 group-hover:underline dark:text-gray-200"
												>
													{#if getOpenclaw(skill)?.frontmatter?.metadata?.openclaw?.emoji}
														{getOpenclaw(skill).frontmatter.metadata.openclaw.emoji}
													{/if}
													{skill.name}
												</div>
											</Tooltip>

											<div
												class="min-w-0 max-w-[40%] shrink-0 truncate text-[0.6875rem] leading-5 text-gray-500"
											>
												/{skill.id}
											</div>

											<Tooltip
												content={dayjs((skill.updated_at ?? skill.created_at) * 1000).format(
													'LLLL'
												)}
											>
												<div
													class="shrink-0 truncate text-[0.6875rem] leading-5 text-gray-400 dark:text-gray-600"
												>
													{dayjs((skill.updated_at ?? skill.created_at) * 1000).fromNow()}
												</div>
											</Tooltip>

											{#if !skill.is_active}
												<Badge type="muted" content={$i18n.t('Inactive')} />
											{/if}

											{#if !skill.write_access}
												<Badge type="muted" content={$i18n.t('Read Only')} />
											{/if}

											{#if skill?.meta?.openclaw}
												<Badge type="muted" content="OpenClaw" />
											{/if}
										</div>
									</div>

									{#if skill.description}
										<Tooltip content={skill.description} className="min-w-0" placement="top-start">
											<div
												class="mt-0.5 truncate text-[0.6875rem] leading-4 text-gray-400 dark:text-gray-600"
											>
												{skill.description}
											</div>
										</Tooltip>
									{/if}
								</div>
							</div>

							<div
								class="hidden max-w-44 shrink-0 self-center truncate text-right text-[0.6875rem] leading-5 text-gray-500 dark:text-gray-500 md:block"
							>
								<Tooltip
									content={skill?.user?.email ?? $i18n.t('Deleted User')}
									className="min-w-0"
									placement="top-start"
								>
									<div class="truncate">
										{capitalizeFirstLetter(
											skill?.user?.name ?? skill?.user?.email ?? $i18n.t('Deleted User')
										)}
									</div>
								</Tooltip>
							</div>

							{#if skill.write_access}
								<div class="ml-2 flex shrink-0 flex-row items-center self-center">
									{#if shiftKey}
										<Tooltip content={$i18n.t('Delete')}>
											<button
												class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition dark:text-gray-500"
												type="button"
												aria-label={$i18n.t('Delete')}
												on:click={(e) => {
													e.preventDefault();
													e.stopPropagation();
													deleteHandler(skill);
												}}
											>
												<GarbageBin className="size-4" />
											</button>
										</Tooltip>
									{:else}
										<div class="flex shrink-0 flex-row items-center gap-1.5 self-center">
											<SkillMenu
												show={openSkillMenuId === skill.id}
												editHandler={() => {
													goto(`/workspace/skills/edit?id=${encodeURIComponent(skill.id)}`);
												}}
												cloneHandler={() => {
													cloneHandler(skill);
												}}
												exportHandler={() => {
													exportHandler(skill);
												}}
												exportSkillMdHandler={() => {
													exportSkillMdHandler(skill);
												}}
												deleteHandler={async () => {
													selectedSkill = skill;
													showDeleteConfirm = true;
												}}
												onClose={() => {
													openSkillMenuId = null;
												}}
											>
												<button
													class="flex size-6 items-center justify-center rounded-lg text-gray-400 transition dark:text-gray-500"
													type="button"
													aria-label={$i18n.t('Skill Menu')}
													on:click={(e) => {
														e.preventDefault();
														e.stopPropagation();
														openSkillMenuId = openSkillMenuId === skill.id ? null : skill.id;
													}}
												>
													<EllipsisHorizontal className="size-4" />
												</button>
											</SkillMenu>

											<button
												class="flex h-6 items-center"
												type="button"
												on:click={(e) => {
													e.stopPropagation();
													e.preventDefault();
												}}
											>
												<Tooltip
													content={skill.is_active ? $i18n.t('Enabled') : $i18n.t('Disabled')}
												>
													<Switch
														bind:state={skill.is_active}
														on:change={async () => {
															toggleSkillById(localStorage.token, skill.id);
														}}
													/>
												</Tooltip>
											</button>
										</div>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>

			{#if total > 30}
				<div class="flex justify-center mt-4 mb-2">
					<Pagination bind:page count={total} perPage={30} />
				</div>
			{/if}
		{:else}
			<div class="flex w-full flex-col items-center justify-center py-16 pb-24">
				<div class="max-w-sm text-center text-gray-900 dark:text-gray-100">
					<div class="mb-1.5 text-sm">{$i18n.t('No skills found')}</div>
					<div class="text-center text-xs leading-5 text-gray-500">
						{$i18n.t('Try adjusting your search or filter to find what you are looking for.')}
					</div>
				</div>
			</div>
		{/if}
	</div>

	<Modal bind:show={showUrlImport} size="sm">
		<div>
			<div class=" flex justify-between dark:text-gray-300 px-4 pt-3 pb-1">
				<div class=" text-sm font-medium self-center">{$i18n.t('Import from URL')}</div>
				<button
					class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
					aria-label={$i18n.t('Close')}
					on:click={() => {
						showUrlImport = false;
					}}
				>
					<XMark className={'size-4'} />
				</button>
			</div>

			<form
				class="flex w-full items-center gap-2 px-4 pb-4 pt-1"
				on:submit|preventDefault={urlImportHandler}
			>
				<input
					class="w-full rounded-lg bg-gray-50 px-3 py-1.5 text-sm outline-hidden dark:bg-gray-850 dark:text-gray-200"
					type="text"
					placeholder={$i18n.t('Enter a URL, GitHub/skills.sh link, or @owner/skill')}
					aria-label={$i18n.t('Enter a URL, GitHub/skills.sh link, or @owner/skill')}
					bind:value={urlImportValue}
					required
				/>

				<button
					class="flex h-7 shrink-0 items-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
					type="submit"
					disabled={urlImportLoading}
				>
					{$i18n.t('Import')}
					{#if urlImportLoading}
						<Spinner className="size-3" />
					{/if}
				</button>
			</form>
		</div>
	</Modal>

	<Modal bind:show={showSkillPicker} size="sm">
		<div>
			<div class=" flex justify-between dark:text-gray-300 px-4 pt-3 pb-1">
				<div class=" text-sm font-medium self-center">{$i18n.t('Select a skill to import')}</div>
				<button
					class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
					aria-label={$i18n.t('Close')}
					on:click={() => {
						showSkillPicker = false;
						skillPickerBlob = null;
						skillPickerItems = [];
						skillPickerSource = null;
					}}
				>
					<XMark className={'size-4'} />
				</button>
			</div>

			<div class="max-h-96 overflow-y-auto px-2 pb-3 pt-1">
				{#each skillPickerItems as item (item.path)}
					<button
						class="w-full rounded-lg px-2.5 py-1.5 text-left transition hover:bg-gray-50 dark:hover:bg-gray-850"
						type="button"
						on:click={() => pickSkillHandler(item)}
					>
						<div class="truncate text-sm text-gray-800 dark:text-gray-200">{item.name}</div>
						{#if item.description}
							<div class="mt-0.5 line-clamp-2 text-xs text-gray-500 dark:text-gray-400">
								{item.description}
							</div>
						{/if}
						<div class="mt-0.5 truncate text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{item.dirName || 'SKILL.md'}
						</div>
					</button>
				{/each}
			</div>
		</div>
	</Modal>

	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title={$i18n.t('Delete skill?')}
		on:confirm={() => {
			deleteHandler(selectedSkill);
		}}
	>
		<div class=" text-sm text-gray-500 truncate">
			{$i18n.t('This will delete')} <span class="  font-normal">{selectedSkill.name}</span>.
		</div>
	</DeleteConfirmDialog>
{:else}
	<div class="w-full h-full flex justify-center items-center">
		<Spinner className="size-5" />
	</div>
{/if}
