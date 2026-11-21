import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import { describe, expect, it, vi } from 'vitest';
import AskUserCard from './AskUserCard.svelte';
import { getPendingAskUserFromMessage } from './Messages/structuredOutput';
import { getAskUserResult, type AskUserQuestion } from './askUser';

vi.mock('$lib/stores', async () => {
	const { writable } = await import('svelte/store');
	return { settings: writable({}) };
});

const logQuestion = { id: 'log', header: 'Log', question: 'Paste the command output.' };
const choice = {
	id: 'action',
	header: 'Action',
	question: 'How should we proceed?',
	options: ['Retry', 'Wait', 'Inspect', 'Skip'].map((label) => ({ label, description: '' }))
};

const renderCard = (questions: AskUserQuestion[]) =>
	render(AskUserCard, {
		props: { show: true, questions },
		context: new Map([['i18n', writable({ t: (key: string) => key })]])
	}).body;

describe('ask_user prompts', () => {
	it.each([logQuestion, { ...logQuestion, options: [] }])(
		'shows a multiline field directly for a fill-in question',
		(question) => {
			const html = renderCard([question]);
			expect(html).toContain('<textarea');
			expect(html).not.toContain('Other');
			expect(html).not.toContain('Recommended');
		}
	);

	it('offers a custom text area alongside all four presets', () => {
		const html = renderCard([choice]);
		for (const { label } of choice.options) expect(html).toContain(label);
		expect(html).toContain('Other');
		expect(html).toContain('<textarea');
	});

	it('keeps an unanswered fill-in question on the current page', () => {
		const html = renderCard([logQuestion, choice]);
		expect(html).toMatch(/<button[^>]*disabled[^>]*>Next<\/button>/);
	});

	it('respects an external input request that disables typed answers', () => {
		expect(renderCard([{ ...logQuestion, options: [], allow_other: false }])).not.toContain(
			'<textarea'
		);
	});

	it('restores a saved builtin question with a custom answer even when its old flags are false', () => {
		const args = { allow_other: false, questions: [{ ...choice, allow_other: false }] };
		const call = {
			type: 'function_call',
			name: 'ask_user',
			status: 'pending',
			id: 'call-a',
			arguments: JSON.stringify(args)
		};
		const pending = getPendingAskUserFromMessage({
			id: 'reply',
			role: 'assistant',
			output: [call]
		});
		expect(pending?.prompt.questions[0].allow_other).toBe(true);
		expect(pending?.prompt.allowOther).toBe(true);
		expect(pending?.prompt.id).toBeTruthy();
		expect(JSON.parse(call.arguments)).toEqual(args);
		const nextReply = getPendingAskUserFromMessage({
			id: 'next',
			role: 'assistant',
			output: [call]
		});
		expect(nextReply?.prompt.id).not.toEqual(pending?.prompt.id);
	});

	it('only upgrades old custom-answer flags for the builtin', () => {
		const call = {
			type: 'function_call',
			name: 'ask_user',
			status: 'pending',
			call_id: 'native',
			native: {},
			arguments: { allow_other: false, questions: [{ ...choice, allow_other: false }] }
		};
		const message = { id: 'agent', role: 'assistant', output: [call] };
		const pending = getPendingAskUserFromMessage(message);
		expect(pending?.prompt.allowOther).toBe(false);
		expect(pending?.prompt.questions[0].allow_other).toBe(false);
		const external = { ...call, native: undefined, is_ask_user: false };
		const externalPrompt = getPendingAskUserFromMessage({ ...message, output: [external] })?.prompt;
		expect(externalPrompt?.allowOther).toBe(false);
		expect(externalPrompt?.questions[0].allow_other).toBe(false);
	});

	it('preserves a pasted log, a numeric string and a chosen option in the same reply', () => {
		const text = '  错误：示例\n    at run()\n\n';
		const number = { ...logQuestion, id: 'number' };
		const questions = [choice, logQuestion, number];
		const answers = {
			action: { type: 'option' as const, option_index: 3, ...choice.options[3] },
			log: { type: 'other' as const, text },
			number: { type: 'other' as const, text: '00123' }
		};
		expect(getAskUserResult(questions, answers)).toEqual({ status: 'answered', answers });
		const edited = { ...answers, action: { type: 'other' as const, text: '  Just report  ' } };
		expect(getAskUserResult(questions, edited)?.answers.action).toEqual(edited.action);
		expect(
			getAskUserResult(questions, { ...edited, log: { type: 'other', text: ' \n\t' } })
		).toBeNull();
		expect(getAskUserResult(questions, { log: answers.log })).toBeNull();
	});
});
