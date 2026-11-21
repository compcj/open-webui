export type AskUserOption = {
	label: string;
	description?: string;
};

export type AskUserQuestion = {
	id: string;
	header: string;
	question: string;
	options?: AskUserOption[];
	allow_other?: boolean;
};

export type AskUserAnswer =
	| { type: 'option'; option_index: number; label: string; description: string }
	| { type: 'other'; text: string };

export type AskUserResult = { status: 'answered'; answers: Record<string, AskUserAnswer> };

export type AskUserPrompt = {
	id?: string;
	show: boolean;
	questions: AskUserQuestion[];
	allowOther: boolean;
	timeoutMs: number | null;
	onConfirm: (result: AskUserResult) => void;
	onCancel: () => void;
};

export function hasAskUserAnswer(answer: AskUserAnswer | undefined) {
	return answer?.type === 'option' || (answer?.type === 'other' && answer.text.trim() !== '');
}

export function getAskUserResult(
	questions: AskUserQuestion[],
	answers: Record<string, AskUserAnswer>
): AskUserResult | null {
	if (!questions.length || !questions.every(({ id }) => hasAskUserAnswer(answers[id]))) return null;
	return {
		status: 'answered',
		answers: Object.fromEntries(questions.map(({ id }) => [id, answers[id]]))
	};
}
