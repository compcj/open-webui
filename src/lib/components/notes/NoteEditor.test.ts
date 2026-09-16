import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { describe, expect, it, vi } from 'vitest';

// Exercise the actual title handler without mounting the full editor/socket stack.
const source = readFileSync(new URL('./NoteEditor.svelte', import.meta.url), 'utf8');
const ast = parse(source);
const declaration = ast.instance?.content.body
	.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'generateTitleHandler');
const handlerSource = source.slice(declaration.init.start, declaration.init.end);

const setup = (generateTitle: ReturnType<typeof vi.fn>) => {
	const note = { title: 'Original title', data: { content: { md: 'Note content' } } };
	const toast = { error: vi.fn() };
	const changeDebounceHandler = vi.fn();
	const run = new Function(
		'note',
		'generateTitle',
		'toast',
		'changeDebounceHandler',
		`const localStorage = { token: 'synthetic-token' };
		const selectedModelId = 'test-model';
		const $i18n = { t: (text) => text };
		const tick = async () => {};
		const console = { error: () => {} };
		let titleGenerating = false;
		return async () => {
			await (${handlerSource})();
			return titleGenerating;
		};`
	)(note, generateTitle, toast, changeDebounceHandler);
	return { note, toast, run, changeDebounceHandler };
};

describe('note title generation through the title task', () => {
	it('uses the dedicated title API with the note content', async () => {
		const generateTitle = vi.fn().mockResolvedValue(' New title ');
		const { note, run, changeDebounceHandler } = setup(generateTitle);
		expect(await run()).toBe(false);
		expect(note.title).toBe('New title');
		expect(generateTitle).toHaveBeenCalledWith('synthetic-token', 'test-model', [
			{ role: 'user', content: 'Note content' }
		]);
		expect(changeDebounceHandler).toHaveBeenCalledOnce();
	});

	it('restores the old title and exits loading when title generation fails', async () => {
		const { note, toast, run } = setup(vi.fn().mockRejectedValue(new Error('Request failed')));
		expect(await run()).toBe(false);
		expect(note.title).toBe('Original title');
		expect(toast.error).toHaveBeenCalledWith('Failed to generate title');
	});

	it('keeps the title when the server disables title generation', async () => {
		const { note, run } = setup(vi.fn().mockResolvedValue(null));
		expect(await run()).toBe(false);
		expect(note.title).toBe('Original title');
	});
});
