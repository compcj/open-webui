import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./NotebookView.svelte', import.meta.url), 'utf8');
const handlers = new Set(['startSession', 'stopSession', 'runCell', 'runAll']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => handlers.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const create = (createNotebookSession: any = vi.fn(async () => ({ id: 'kernel-a' }))) => {
	const executeNotebookCell = vi.fn(async () => ({ status: 'ok', outputs: [] }));
	const stopNotebookSession = vi.fn(async () => true);
	const body = transpileModule(
		`
		const { createNotebookSession, executeNotebookCell, stopNotebookSession } = dependencies;
		let baseUrl = '/terminal', apiKey = 'token', chatId = 'chat-a', filePath = '/work/a.ipynb';
		const connection = { baseUrl, apiKey, chatId, filePath };
		let disposed = false, readOnly = false, kernelStarting = false, kernelReady = false;
		let kernelError = null, sessionId = null, runningCell = null, runAllActive = false;
		let cells = [{ cell_type: 'code', source: 'print(1)', outputs: [] }];
		let editedSources = {}, editingCell = {}, lang = 'python';
		const toStr = s => Array.isArray(s) ? s.join('') : (s ?? '');
		const highlightCell = () => {};
		${declarations}
		return { startSession, runCell, runAll, stopSession,
			dispose: () => { disposed = true; if (sessionId) void stopSession(); },
			setReadOnly: value => { readOnly = value; },
			get: () => ({ cells, kernelReady, sessionId }) };
	`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText;
	return {
		view: new Function('dependencies', body)({
			createNotebookSession,
			executeNotebookCell,
			stopNotebookSession
		}),
		createNotebookSession,
		executeNotebookCell,
		stopNotebookSession
	};
};

describe('notebook lifecycle and chat binding', () => {
	it('uses the captured chat through creation, execution and cleanup', async () => {
		const { view, createNotebookSession, executeNotebookCell, stopNotebookSession } = create();
		await view.runCell(0);
		await view.stopSession();
		expect(createNotebookSession).toHaveBeenCalledWith(
			'/terminal',
			'token',
			'/work/a.ipynb',
			'chat-a'
		);
		expect(executeNotebookCell).toHaveBeenCalledWith(
			'/terminal',
			'token',
			'kernel-a',
			0,
			'print(1)',
			'chat-a'
		);
		expect(stopNotebookSession).toHaveBeenCalledWith('/terminal', 'token', 'kernel-a', 'chat-a');
	});
	it('stops a kernel created after the old file/chat view was destroyed', async () => {
		let resolve!: (value: any) => void;
		const { view, executeNotebookCell, stopNotebookSession } = create(
			vi.fn(
				() =>
					new Promise((done) => {
						resolve = done;
					})
			)
		);
		const pending = view.runCell(0);
		view.dispose();
		resolve({ id: 'late-kernel' });
		await pending;
		expect(stopNotebookSession).toHaveBeenCalledWith('/terminal', 'token', 'late-kernel', 'chat-a');
		expect(executeNotebookCell).not.toHaveBeenCalled();
	});
	it('does not execute after write access is removed', async () => {
		const { view, executeNotebookCell, createNotebookSession } = create();
		view.setReadOnly(true);
		await view.runCell(0);
		expect(createNotebookSession).not.toHaveBeenCalled();
		expect(executeNotebookCell).not.toHaveBeenCalled();
	});
});
