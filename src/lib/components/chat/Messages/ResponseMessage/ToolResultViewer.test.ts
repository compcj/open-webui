import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

// Execute the real page loader with synthetic authenticated API responses.
const source = readFileSync(new URL('./ToolResultViewer.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'loadPage');
const createViewer = (getChatToolResult: any) => {
	const body = `
		const localStorage = { token: 'synthetic-token' };
		const chatId = 'chat', branchId = 'current', resultId = 'raw';
		let page = null, activeQuery = 'needle', loading = false, error = '', requestVersion = 0;
		const ${source.slice(declaration.start, declaration.end)};
		return { loadPage, state: () => ({ page, loading, error }), close: () => { requestVersion += 1; } };
	`;
	return new Function(
		'getChatToolResult',
		transpileModule(body, {
			compilerOptions: { target: ScriptTarget.ES2022 }
		}).outputText
	)(getChatToolResult);
};

describe('original tool result page loader', () => {
	it('loads exact pages with branch scope and the same active query on pagination', async () => {
		const raw = { text: '<script>exact original</script>', next_cursor: 'next' };
		const read = vi.fn(async () => raw);
		const viewer = createViewer(read);
		await viewer.loadPage();
		expect(viewer.state()).toEqual({ page: raw, loading: false, error: '' });
		await viewer.loadPage('next');
		expect(read).toHaveBeenLastCalledWith('synthetic-token', 'chat', 'raw', 'current', {
			query: 'needle',
			cursor: 'next'
		});
	});
	it('displays errors without leaving a loading state', async () => {
		const viewer = createViewer(async () => {
			throw 'Source changed';
		});
		await viewer.loadPage();
		expect(viewer.state()).toEqual({ page: null, loading: false, error: 'Source changed' });
	});
	it('ignores a stale response after the viewer is closed or a newer request wins', async () => {
		let complete: (value: any) => void = () => {};
		const viewer = createViewer(
			() =>
				new Promise((resolve) => {
					complete = resolve;
				})
		);
		const pending = viewer.loadPage();
		viewer.close();
		complete({ text: 'stale original' });
		await pending;
		expect(viewer.state().page).toBeNull();
	});
});
