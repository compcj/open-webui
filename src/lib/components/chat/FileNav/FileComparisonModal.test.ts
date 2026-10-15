import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./FileComparisonModal.svelte', import.meta.url), 'utf8');
const handlers = new Set(['cancel', 'close', 'read']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => handlers.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const create = (readComparisonSource: any) => {
	const onClose = vi.fn();
	const body = transpileModule(
		`
		const { readComparisonSource, onClose } = dependencies;
		const sourceFiles = [{ name: 'a', path: '/a', size: 1 }, { name: 'b', path: '/b', size: 2 }];
		const connection = { baseUrl: '/terminal-a', apiKey: 'token-a', chatId: 'chat-a' };
		let controller = null, generation = 0, closed = false;
		const $i18n = { t: text => text };
		${declarations}
		return { read, close };
	`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText;
	return { view: new Function('dependencies', body)({ readComparisonSource, onClose }), onClose };
};

describe('file comparison request ownership', () => {
	it('reads both sources with the captured terminal and chat', async () => {
		const read = vi.fn(async (_url: string, _key: string, path: string) =>
			path === '/a' ? 'left' : 'right'
		);
		const { view } = create(read);
		await expect(view.read()).resolves.toEqual({
			initialContent: 'left',
			proposedContent: 'right'
		});
		expect(read.mock.calls).toEqual([
			['/terminal-a', 'token-a', '/a', expect.any(AbortSignal), 'chat-a', 1],
			['/terminal-a', 'token-a', '/b', expect.any(AbortSignal), 'chat-a', 2]
		]);
	});
	it('aborts both reads and refuses their late completion when closed', async () => {
		const resolvers: ((value: string) => void)[] = [];
		const read = vi.fn(
			(..._args: any[]) => new Promise<string>((resolve) => resolvers.push(resolve))
		);
		const { view, onClose } = create(read);
		const pending = view.read();
		view.close();
		expect(read.mock.calls[0][3].aborted).toBe(true);
		expect(read.mock.calls[1][3].aborted).toBe(true);
		resolvers.forEach((resolve) => resolve('late content'));
		await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
		expect(onClose).toHaveBeenCalledOnce();
	});
	it('aborts the other source when one source fails', async () => {
		let otherSignal: AbortSignal | undefined;
		const read = vi.fn((_url: string, _key: string, path: string, signal: AbortSignal) => {
			if (path === '/a') return Promise.reject(new Error('Permission denied.'));
			otherSignal = signal;
			return new Promise(() => {});
		});
		const { view } = create(read);
		await expect(view.read()).rejects.toThrow('/a: Permission denied.');
		expect(otherSignal?.aborted).toBe(true);
	});
});
