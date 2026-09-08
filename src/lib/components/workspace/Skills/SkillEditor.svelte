<script lang="ts">
	import { onMount, tick, getContext } from 'svelte';

	import Textarea from '$lib/components/common/Textarea.svelte';
	import { toast } from 'svelte-sonner';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AccessButton from '$lib/components/common/AccessButton.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import { user } from '$lib/stores';
	import { slugify, formatSkillName } from '$lib/utils';
	import { parseSkillMarkdown, extractGating, extractInstallSpecs } from '$lib/utils/skills';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { updateSkillAccessGrants, installSkillDeps } from '$lib/apis/skills';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { goto } from '$app/navigation';

	export let onSubmit: Function;
	export let edit = false;
	export let skill = null;
	export let clone = false;
	export let disabled = false;

	const i18n = getContext('i18n');

	let loading = false;

	let name = '';
	let id = '';
	let description = '';
	let content = '';

	let accessGrants = [];
	let showAccessControlModal = false;
	$: if (!edit && !clone && name) {
		id = slugify(name);
	}

	let openclawState: any = null;
	let openclawEnvText = '';
	let openclawConfigText = '';
	let showOpenclaw = false;

	let installDepsLoading = false;
	let installTerminals: any[] | null = null;
	let installTerminalId = '';
	let installDepsOutput: string | null = null;
	let installDepsOk: boolean | null = null;

	$: openclawGating = openclawState ? extractGating({ openclaw: openclawState }) : null;
	$: openclawInstallSpecs = openclawState ? extractInstallSpecs({ openclaw: openclawState }) : [];
	$: openclawGatingSummary = (() => {
		if (!openclawGating) return '';
		const parts: string[] = [];
		if (openclawGating.bins.length > 0)
			parts.push(`Requires CLI: ${openclawGating.bins.join(', ')}`);
		if (openclawGating.anyBins.length > 0)
			parts.push(`Any of: ${openclawGating.anyBins.join(' | ')}`);
		if (openclawGating.env.length > 0) parts.push(`Env: ${openclawGating.env.join(', ')}`);
		if (openclawGating.config.length > 0) parts.push(`Config: ${openclawGating.config.join(', ')}`);
		if (openclawGating.os.length > 0) parts.push(`OS: ${openclawGating.os.join(', ')}`);
		return parts.join(' · ');
	})();

	const handleContentInput = () => {
		if (edit) return;
		const parsed = parseSkillMarkdown(content);
		if (parsed) {
			if (parsed.frontmatter?.name && !name) {
				name = formatSkillName(parsed.frontmatter.name);
			}
			if (parsed.frontmatter?.description && !description) {
				description = parsed.frontmatter.description;
			}
		}
	};

	const submitHandler = async () => {
		if (disabled) {
			toast.error($i18n.t('You do not have permission to edit this skill.'));
			return;
		}
		loading = true;
		if (!edit) id = slugify(id);

		if (openclawState) {
			try {
				openclawState.env = openclawEnvText.trim() ? JSON.parse(openclawEnvText) : {};
			} catch {
				toast.error($i18n.t('Invalid env JSON'));
				loading = false;
				return;
			}

			try {
				openclawState.config = openclawConfigText.trim() ? JSON.parse(openclawConfigText) : {};
			} catch {
				toast.error($i18n.t('Invalid config JSON'));
				loading = false;
				return;
			}
		}

		await onSubmit({
			id,
			name,
			description,
			content,
			is_active: true,
			meta: {
				...(skill?.meta ?? {}),
				...(openclawState ? { openclaw: openclawState } : {}),
				tags: skill?.meta?.tags ?? []
			},
			access_grants: accessGrants
		});

		loading = false;
	};

	const installDepsHandler = async () => {
		if (!skill?.id || installDepsLoading) return;

		installDepsLoading = true;
		try {
			if (installTerminals === null) {
				const terminals = await getTerminalServers(localStorage.token);
				if (!terminals || terminals.length === 0) {
					toast.error($i18n.t('No terminals available'));
					return;
				}

				if (terminals.length > 1) {
					// Let the user pick a terminal, then click again to run
					installTerminals = terminals;
					installTerminalId = terminals[0].id;
					return;
				}

				installTerminals = terminals;
				installTerminalId = terminals[0].id;
			}

			installDepsOutput = null;
			installDepsOk = null;

			const res = await installSkillDeps(localStorage.token, skill.id, installTerminalId).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (res) {
				installDepsOutput = res.output ?? '';
				installDepsOk = res.ok === true;
				if (installDepsOk) {
					toast.success($i18n.t('Dependencies installed'));
				} else {
					toast.error($i18n.t('Dependency installation failed'));
				}
			}
		} finally {
			installDepsLoading = false;
		}
	};

	onMount(async () => {
		if (skill) {
			name = skill.name || '';
			await tick();
			id = skill.id || '';
			description = skill.description || '';
			content = skill.content || '';
			accessGrants = skill?.access_grants === undefined ? [] : skill?.access_grants;

			if (skill?.meta?.openclaw) {
				openclawState = structuredClone(skill.meta.openclaw);
				openclawEnvText = JSON.stringify(openclawState.env ?? {}, null, 2);
				openclawConfigText = JSON.stringify(openclawState.config ?? {}, null, 2);
			}
		}
	});
</script>

<AccessControlModal
	bind:show={showAccessControlModal}
	bind:accessGrants
	accessRoles={['read', 'write']}
	share={$user?.permissions?.sharing?.skills || $user?.role === 'admin'}
	sharePublic={$user?.permissions?.sharing?.public_skills || $user?.role === 'admin'}
	shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) || $user?.role === 'admin'}
	onChange={async () => {
		if (edit && skill?.id) {
			try {
				await updateSkillAccessGrants(localStorage.token, skill.id, accessGrants);
				toast.success($i18n.t('Saved'));
			} catch (error) {
				toast.error(`${error}`);
			}
		}
	}}
/>

<div class="flex h-full w-full min-w-0 flex-col overflow-hidden">
	<form class="flex h-full min-h-0 min-w-0 flex-col" on:submit|preventDefault={submitHandler}>
		<button
			class="mb-1 flex h-6 w-fit items-center gap-1 rounded-md text-xs text-gray-400 transition-colors duration-75 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300"
			type="button"
			on:click={() => {
				goto('/workspace/skills');
			}}
		>
			<ChevronLeft className="size-3" strokeWidth="2" />
			<span>{$i18n.t('Back')}</span>
		</button>

		<div class="flex shrink-0 items-start gap-2 pb-2 px-1">
			<div class="min-w-0 flex-1">
				<Tooltip content={$i18n.t('e.g. Code Review Guidelines')} placement="top-start">
					<input
						class="w-full bg-transparent text-sm outline-hidden"
						type="text"
						placeholder={$i18n.t('Skill Name')}
						aria-label={$i18n.t('Skill Name')}
						bind:value={name}
						required
						{disabled}
					/>
				</Tooltip>

				<div class="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-gray-500">
					{#if edit}
						<div class="shrink-0 truncate font-mono" title={id}>
							{id}
						</div>
					{:else}
						<Tooltip
							className="min-w-[8rem] flex-1"
							content={$i18n.t('e.g. code-review-guidelines')}
							placement="top-start"
						>
							<input
								class="w-full bg-transparent font-mono outline-hidden disabled:text-gray-500"
								type="text"
								placeholder={$i18n.t('Skill ID')}
								aria-label={$i18n.t('Skill ID')}
								bind:value={id}
								required
								disabled={edit}
							/>
						</Tooltip>
					{/if}

					<Tooltip
						className="flex min-w-0 flex-1 items-center"
						content={$i18n.t('e.g. Step-by-step instructions for code reviews')}
						placement="top-start"
					>
						<input
							class="w-full bg-transparent outline-hidden"
							type="text"
							placeholder={$i18n.t('Skill Description')}
							aria-label={$i18n.t('Skill Description')}
							bind:value={description}
							{disabled}
						/>
					</Tooltip>
				</div>
			</div>

			<div class="flex shrink-0 items-center gap-1 pr-0.5">
				{#if !disabled}
					<AccessButton on:click={() => (showAccessControlModal = true)} />
				{:else}
					<span class="rounded-lg bg-gray-100 px-2 py-1 text-xs text-gray-500 dark:bg-gray-850">
						{$i18n.t('Read Only')}
					</span>
				{/if}
			</div>
		</div>

		<div class="min-h-0 flex-1 overflow-hidden rounded-lg bg-gray-50/60 dark:bg-white/[0.03]">
			{#if disabled}
				<div class="h-full overflow-y-auto px-3 py-2">
					<pre
						class="whitespace-pre-wrap font-mono text-[0.6875rem] leading-relaxed">{content}</pre>
				</div>
			{:else}
				<textarea
					class="h-full w-full resize-none bg-transparent px-3 py-2 font-mono text-[0.6875rem] leading-relaxed outline-hidden placeholder:text-gray-400 dark:placeholder:text-gray-600"
					bind:value={content}
					on:input={handleContentInput}
					placeholder={$i18n.t('Enter skill instructions in markdown...')}
					aria-label={$i18n.t('Skill Instructions')}
					required
				></textarea>
			{/if}
		</div>

		{#if openclawState}
			<div class="mt-1 shrink-0 rounded-lg bg-gray-50/60 px-3 py-2 dark:bg-white/[0.03]">
				<button
					class="flex w-full items-center justify-between text-xs text-gray-500 transition hover:text-gray-700 dark:hover:text-gray-300"
					type="button"
					aria-expanded={showOpenclaw}
					on:click={() => (showOpenclaw = !showOpenclaw)}
				>
					<span class="font-medium">OpenClaw</span>
					{#if showOpenclaw}
						<ChevronUp className="size-3" strokeWidth="2.75" />
					{:else}
						<ChevronDown className="size-3" strokeWidth="2.75" />
					{/if}
				</button>

				{#if showOpenclaw}
					<div class="mt-2 flex flex-col gap-2 text-xs text-gray-700 dark:text-gray-300">
						{#if openclawGatingSummary}
							<div>
								<div class="text-gray-500 dark:text-gray-400">{openclawGatingSummary}</div>
								{#if openclawGating && (openclawGating.bins.length > 0 || openclawGating.anyBins.length > 0)}
									<div class="mt-0.5 text-gray-400 dark:text-gray-500">
										{$i18n.t('Requires CLI tools in the terminal environment')}
									</div>
								{/if}
							</div>
						{/if}

						{#if openclawInstallSpecs.length > 0}
							<div class="flex flex-wrap gap-1">
								{#each openclawInstallSpecs as spec}
									<span class="rounded-lg bg-gray-100 px-2 py-0.5 text-gray-500 dark:bg-gray-850">
										{spec?.label ?? spec?.kind ?? ''}
									</span>
								{/each}
							</div>
						{/if}

						<div>
							<div class="mb-0.5 text-gray-500 dark:text-gray-400">{$i18n.t('Env (JSON)')}</div>
							<textarea
								class="h-16 w-full resize-none rounded-md bg-gray-100 px-2 py-1 font-mono text-[0.6875rem] outline-hidden placeholder:text-gray-400 dark:bg-gray-850 dark:placeholder:text-gray-600"
								bind:value={openclawEnvText}
								placeholder={$i18n.t('Do not store secrets here')}
								aria-label={$i18n.t('Env (JSON)')}
								{disabled}
							></textarea>
						</div>

						{#if openclawGating && openclawGating.config.length > 0}
							<div>
								<div class="mb-0.5 text-gray-500 dark:text-gray-400">
									{$i18n.t('Config (JSON)')}
								</div>
								<textarea
									class="h-16 w-full resize-none rounded-md bg-gray-100 px-2 py-1 font-mono text-[0.6875rem] outline-hidden placeholder:text-gray-400 dark:bg-gray-850 dark:placeholder:text-gray-600"
									bind:value={openclawConfigText}
									placeholder={'{}'}
									aria-label={$i18n.t('Config (JSON)')}
									{disabled}
								></textarea>
							</div>
						{/if}

						{#if edit && skill?.id && !disabled && openclawInstallSpecs.length > 0}
							<div class="flex items-center gap-2">
								{#if installTerminals && installTerminals.length > 1}
									<select
										class="h-7 rounded-lg bg-gray-100 px-1.5 text-xs outline-hidden dark:bg-gray-850"
										bind:value={installTerminalId}
										aria-label={$i18n.t('Select a terminal')}
									>
										{#each installTerminals as terminal}
											<option value={terminal.id}>{terminal?.name ?? terminal.id}</option>
										{/each}
									</select>
								{/if}

								<button
									class="flex h-7 items-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
									type="button"
									disabled={installDepsLoading}
									on:click={installDepsHandler}
								>
									{$i18n.t('Install dependencies')}
									{#if installDepsLoading}
										<Spinner className="size-3" />
									{/if}
								</button>
							</div>

							{#if installDepsOutput !== null}
								<pre
									class="max-h-32 overflow-y-auto whitespace-pre-wrap rounded-md px-2 py-1 font-mono text-[0.6875rem] {installDepsOk
										? 'bg-green-500/10 text-green-700 dark:text-green-300'
										: 'bg-red-500/10 text-red-700 dark:text-red-300'}">{installDepsOutput}</pre>
							{/if}
						{/if}
					</div>
				{/if}
			</div>
		{/if}

		{#if !disabled}
			<div class="flex shrink-0 justify-end py-2">
				<button
					class="flex h-7 items-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
					type="submit"
					disabled={loading}
				>
					{$i18n.t(edit ? 'Save' : 'Save & Create')}
					{#if loading}
						<Spinner className="size-3" />
					{/if}
				</button>
			</div>
		{/if}
	</form>
</div>
