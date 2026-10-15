import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./ResponseMessage.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'feedbackHandler');

describe('rating preservation while generating feedback tags', () => {
	it('merges tags into the latest saved annotation and message', async () => {
		let completeTags!: (value: string[]) => void;
		let tagsStarted!: () => void;
		const started = new Promise<void>((resolve) => {
			tagsStarted = resolve;
		});
		const generateTags = vi.fn(() => {
			tagsStarted();
			return new Promise<string[]>((resolve) => {
				completeTags = resolve;
			});
		});
		const updateFeedbackById = vi.fn(async (..._args: any[]) => ({ id: 'feedback' }));
		const saves: any[] = [];
		const body = transpileModule(
			`
			const { generateTags, updateFeedbackById, saves } = dependencies;
			let message = { id: 'reply', parentId: 'prompt', model: 'model-a', content: 'answer', annotation: {} };
			const chatId = 'chat-a', localStorage = { token: 'token' };
			const history = { messages: { prompt: { childrenIds: ['reply'] } } };
			const $models = [{ id: 'model-a', info: {} }];
			const getChatById = async () => ({ id: chatId });
			const createMessagesList = () => [];
			const createNewFeedback = async () => ({ id: 'feedback' });
			const saveMessage = (_id, value) => { message = structuredClone(value); saves.push(message); };
			const tick = async () => {};
			const toast = { error: () => {} };
			const console = { log: () => {}, warn: () => {}, error: () => {} };
			let showRateComment = false, feedbackLoading = false;
			const ${source.slice(declaration.start, declaration.end)};
			return { feedbackHandler, setLatest: patch => { message = { ...message, ...patch }; } };
		`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText;
		const view = new Function('dependencies', body)({ generateTags, updateFeedbackById, saves });
		const pending = view.feedbackHandler(1);
		await started;
		view.setLatest({
			content: 'latest answer',
			annotation: { rating: -1, details: 'newer details' }
		});
		completeTags(['tag']);
		await pending;
		expect(saves.at(-1)).toMatchObject({
			content: 'latest answer',
			annotation: { rating: -1, details: 'newer details', tags: ['tag'] },
			feedbackId: 'feedback'
		});
		expect(updateFeedbackById.mock.calls.at(-1)?.[2].data).toMatchObject({
			rating: -1,
			details: 'newer details',
			tags: ['tag']
		});
	});
});
