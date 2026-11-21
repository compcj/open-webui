<script lang="ts">
	import { createEventDispatcher, getContext, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { settings } from '$lib/stores';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		getAskUserResult,
		hasAskUserAnswer,
		type AskUserAnswer,
		type AskUserOption,
		type AskUserQuestion,
		type AskUserResult
	} from './askUser';

	const i18n: Writable<i18nType> = getContext('i18n');
	const dispatch = createEventDispatcher<{ confirm: AskUserResult; cancel: void }>();

	export let show = false;
	export let questions: AskUserQuestion[] = [];
	export let allowOther = true;
	export let timeoutMs: number | null = null;

	let answers: Record<string, AskUserAnswer> = {};
	let questionIndex = 0;
	let wasOpen = false;
	let timeoutHandle: ReturnType<typeof setTimeout> | null = null;

	const clearAutoCancel = () => {
		if (timeoutHandle) {
			clearTimeout(timeoutHandle);
			timeoutHandle = null;
		}
	};

	const cancel = () => {
		clearAutoCancel();
		show = false;
		dispatch('cancel');
	};

	$: if (show && !wasOpen) {
		answers = {};
		questionIndex = 0;
		wasOpen = true;
		clearAutoCancel();
		if (typeof timeoutMs === 'number' && timeoutMs > 0) {
			timeoutHandle = setTimeout(() => {
				cancel();
			}, timeoutMs);
		}
	}

	$: if (!show && wasOpen) {
		clearAutoCancel();
		wasOpen = false;
	}

	onDestroy(clearAutoCancel);

	$: question = questions[questionIndex];
	$: selectedAnswer = question ? answers[question.id] : undefined;
	$: hasOptions = Boolean(question?.options?.length);
	$: result = getAskUserResult(questions, answers);
	$: direction = ($settings?.chatDirection?.toLowerCase() ?? 'auto') as 'ltr' | 'rtl' | 'auto';

	const submit = (result: AskUserResult | null) => {
		if (!result) return;
		clearAutoCancel();
		show = false;
		dispatch('confirm', result);
	};

	const advance = () => {
		if (!hasAskUserAnswer(answers[question.id])) return;
		if (questionIndex < questions.length - 1) {
			questionIndex += 1;
		} else {
			submit(getAskUserResult(questions, answers));
		}
	};

	const selectOption = (option: AskUserOption, index: number) => {
		answers = {
			...answers,
			[question.id]: {
				type: 'option',
				option_index: index,
				label: option.label,
				description: option.description ?? ''
			}
		};
		advance();
	};

	const setTextAnswer = (text: string) => {
		answers = { ...answers, [question.id]: { type: 'other', text } };
	};
</script>

{#if show}
	<section dir={direction} class="my-1 rounded-2xl bg-gray-50/70 px-3.5 py-3 dark:bg-white/[0.035]">
		<div class="space-y-2">
			{#if question}
				{#key question.id}
					<div class="space-y-2">
						<div>
							<div class="flex items-center justify-between gap-3">
								<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
									{question.header}
								</div>
								{#if questions.length > 1}
									<div
										class="shrink-0 text-right text-[0.6875rem] text-gray-500 dark:text-gray-400"
									>
										{questionIndex + 1}/{questions.length}
									</div>
								{/if}
							</div>
							<div class="mt-0.5 text-xs leading-relaxed text-gray-600 dark:text-gray-300">
								{question.question}
							</div>
						</div>

						<div class="space-y-1">
							<div class="space-y-0.5">
								{#each question.options || [] as option, optionIndex}
									{@const selected =
										selectedAnswer?.type === 'option' &&
										selectedAnswer.option_index === optionIndex}
									<button
										type="button"
										aria-pressed={selected}
										class="group flex w-full min-w-0 items-baseline gap-2 rounded-lg py-1.5 text-start transition-colors {selected
											? 'text-gray-950 dark:text-white'
											: 'text-gray-700 hover:text-gray-950 dark:text-gray-300 dark:hover:text-white'}"
										on:click={() => selectOption(option, optionIndex)}
									>
										<span class="min-w-0 break-words text-xs">{option.label}</span>
										{#if option.description}
											<Tooltip
												as="span"
												className="min-w-0 flex-1"
												content={option.description}
												placement="top-start"
											>
												<span
													class="block truncate text-xs leading-relaxed text-gray-500 transition-colors group-hover:text-gray-700 dark:text-gray-400 dark:group-hover:text-gray-300"
												>
													{option.description}
												</span>
											</Tooltip>
										{/if}
										{#if optionIndex === 0}
											<span
												class="shrink-0 rounded-full bg-gray-200/70 px-1.5 py-0.5 text-[0.625rem] text-gray-500 dark:bg-white/[0.08] dark:text-gray-400"
											>
												{$i18n.t('Recommended')}
											</span>
										{/if}
									</button>
								{/each}
							</div>

							{#if question.allow_other ?? allowOther}
								<label class="block space-y-1 py-1">
									{#if hasOptions}
										<span class="text-xs text-gray-700 dark:text-gray-300">{$i18n.t('Other')}</span>
									{/if}
									<textarea
										aria-label={hasOptions ? $i18n.t('Other') : question.question}
										dir={direction}
										rows={hasOptions ? 2 : 4}
										class="block w-full min-w-0 resize-y rounded-lg border border-gray-200 bg-transparent px-2.5 py-2 text-sm text-gray-900 placeholder:text-gray-500 focus:border-gray-400 dark:border-gray-700 dark:text-gray-100"
										placeholder={$i18n.t('Type your answer')}
										value={selectedAnswer?.type === 'other' ? selectedAnswer.text : ''}
										on:input={(event) => setTextAnswer(event.currentTarget.value)}
									></textarea>
								</label>
							{/if}
						</div>
					</div>
				{/key}
			{/if}

			<div class="flex items-center justify-between gap-2">
				<div class="flex items-center gap-1.5">
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal transition disabled:opacity-60 text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
						on:click={cancel}
					>
						{$i18n.t('Cancel')}
					</button>
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-normal transition disabled:opacity-60 text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
						disabled={questionIndex === 0}
						on:click={() => (questionIndex -= 1)}
					>
						{$i18n.t('Previous')}
					</button>
				</div>
				{#if questionIndex < questions.length - 1}
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
						disabled={!hasAskUserAnswer(selectedAnswer)}
						on:click={advance}
					>
						{$i18n.t('Next')}
					</button>
				{:else}
					<button
						type="button"
						class="flex h-7 shrink-0 items-center justify-center gap-1.5 rounded-lg bg-gray-900 px-2.5 text-xs font-normal text-white transition hover:bg-black disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white disabled:cursor-not-allowed"
						disabled={!result}
						on:click={() => submit(result)}
					>
						{$i18n.t('Submit answers')}
					</button>
				{/if}
			</div>
		</div>
	</section>
{/if}
