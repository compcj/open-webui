import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('../FileNav.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'bulkDelete');
const body = transpileModule(
	`
	let { selectedTerminal, chatId, currentPath, selectedEntries, deleteEntry, loadDir } = state;
	const selectedEntriesWritable = true;
	const toast = { success: () => {}, error: () => {} };
	const $i18n = { t: text => text };
	const invalidateTreeCache = () => {};
	const clearSelection = () => {};
	const ${source.slice(declaration.start, declaration.end)};
	return { bulkDelete, switchChat: id => { chatId = id; } };
`,
	{ compilerOptions: { target: ScriptTarget.ES2022 } }
).outputText;

describe('file browser bulk deletion context', () => {
	it('keeps the original chat for every delete and does not refresh a different chat', async () => {
		let finishFirst!: (result: boolean) => void;
		const deleteEntry = vi
			.fn()
			.mockImplementationOnce(
				() =>
					new Promise((resolve) => {
						finishFirst = resolve;
					})
			)
			.mockResolvedValue(true);
		const loadDir = vi.fn();
		const view = new Function('state', body)({
			selectedTerminal: { url: '/terminal', key: 'token' },
			chatId: 'chat-a',
			currentPath: '/work/',
			selectedEntries: new Set(['/work/a.txt', '/work/b.txt']),
			deleteEntry,
			loadDir
		});
		const pending = view.bulkDelete();
		view.switchChat('chat-b');
		finishFirst(true);
		await pending;
		expect(deleteEntry.mock.calls).toEqual([
			['/terminal', 'token', '/work/a.txt', 'chat-a'],
			['/terminal', 'token', '/work/b.txt', 'chat-a']
		]);
		expect(loadDir).not.toHaveBeenCalled();
	});
});

const directoryDeclaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'loadDir');
const directoryBody = transpileModule(
	`
	let { selectedTerminal, chatId, listFiles, setCwd } = state;
	let currentPath = '/start/', savedPath = currentPath, directoryController = null;
	let loading = false, error = null, selectedFile = null, selectedFileWritable = true, previewPort = null;
	let entries = [], currentWritable = true, treeCache = new Map();
	const clampToFileRoot = path => path;
	const restoreTreeState = () => {}, resetTreeState = () => {}, clearFilePreview = () => {};
	const clearSelection = () => {}, pushNavHistory = () => {}, saveTreeState = () => {};
	const normalizeEntries = entries => entries;
	const ${source.slice(directoryDeclaration.start, directoryDeclaration.end)};
	return { loadDir, switchChat: id => { chatId = id; entries = [{ name: 'current-chat' }]; },
		get: () => ({ entries, currentPath }) };
`,
	{ compilerOptions: { target: ScriptTarget.ES2022 } }
).outputText;

describe('file browser directory request ownership', () => {
	it('does not apply an old listing or set its cwd on a different chat', async () => {
		let resolve!: (value: any) => void;
		const listFiles = vi.fn(
			(..._args: any[]) =>
				new Promise((done) => {
					resolve = done;
				})
		);
		const setCwd = vi.fn();
		const view = new Function('state', directoryBody)({
			selectedTerminal: { url: '/terminal', key: 'token' },
			chatId: 'chat-a',
			listFiles,
			setCwd
		});
		const pending = view.loadDir('/old-chat/');
		view.switchChat('chat-b');
		resolve({ entries: [{ name: 'old-chat' }] });
		await pending;
		expect(setCwd).not.toHaveBeenCalled();
		expect(view.get().entries).toEqual([{ name: 'current-chat' }]);
	});
	it('aborts a previous directory navigation and rejects its late completion', async () => {
		const resolvers: ((value: any) => void)[] = [];
		const listFiles = vi.fn((..._args: any[]) => new Promise((resolve) => resolvers.push(resolve)));
		const setCwd = vi.fn();
		const view = new Function('state', directoryBody)({
			selectedTerminal: { url: '/terminal', key: 'token' },
			chatId: 'chat-a',
			listFiles,
			setCwd
		});
		const first = view.loadDir('/first/');
		const second = view.loadDir('/second/');
		expect(listFiles.mock.calls[0][4]?.aborted).toBe(true);
		resolvers[1]({ entries: [{ name: 'second' }] });
		await second;
		resolvers[0]({ entries: [{ name: 'first' }] });
		await first;
		expect(view.get().entries).toEqual([{ name: 'second' }]);
		expect(setCwd).toHaveBeenCalledOnce();
		expect(setCwd).toHaveBeenCalledWith('/terminal', 'token', '/second/', 'chat-a');
	});
});

const fileDeclarations = parse(source).instance!.content.body;
const fileHandlers = new Set(['openEntry', 'clearFilePreview', 'openComparison']);
const fileDeclarationsSource = fileDeclarations
	.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => fileHandlers.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const contextDeclarationsSource = fileDeclarations
	.flatMap((node: any) => node.declarations ?? [])
	.filter(
		(node: any) =>
			node.id?.name.startsWith('prevTerminal') || ['prevChatId', 'mounted'].includes(node.id?.name)
	)
	.map((node: any) => `let ${source.slice(node.start, node.end)};`)
	.join('\n');
const contextBlock: any = fileDeclarations.find(
	(node: any) =>
		node.type === 'LabeledStatement' &&
		source.slice(node.start, node.end).includes('const terminal = getTerminal();')
);
const fileBody = transpileModule(
	`
	let { selectedTerminal, chatId, readFile } = state;
	let terminal = selectedTerminal;
	let $selectedTerminalId = '/terminal', $terminalServers = [], $settings = {};
	const getTerminal = () => terminal;
	let terminalContextRevision = 0;
	let currentPath = '/work/', selectedFile = null, selectedFileWritable = true, fileLoading = false;
	let fileContent = null, documentTargetPage = null, filePreviewController = null;
	let fileImageUrl = null, fileVideoUrl = null, fileAudioUrl = null, filePdfData = null;
	let fileSqliteData = null, fileDocxData = null, fileOfficeHtml = null, fileOfficeSlides = null;
	let currentSlide = 0, excelSheetNames = [], selectedExcelSheet = '', excelWorkbook = null;
	let fileSearchTarget = null, directoryController = null, previewPort = null;
	let comparison = null, pinnedComparison = null;
	const document = { activeElement: null };
	class HTMLElement {}
	const entryPath = (parent, entry) => parent + entry.name;
	const loadDir = async () => {}, pushNavHistory = () => {};
	const normalizeDocumentTargetPage = () => null;
	const isImage = () => false, isVideo = () => false, isAudio = () => false;
	const isPdf = () => false, isSqlite = () => false, isOffice = () => false;
	const getFileExt = path => path.split('.').pop();
	${contextDeclarationsSource}
	${fileDeclarationsSource}
	const updateContext = () => ${source.slice(contextBlock.body.start, contextBlock.body.end)};
	updateContext();
	return {
		openEntry,
		switchChat: id => { chatId = id; updateContext(); fileContent = 'current chat'; },
		switchContext: next => {
			terminal = next.terminal ?? terminal;
			$selectedTerminalId = next.selectedId ?? $selectedTerminalId;
			chatId = next.chatId ?? chatId;
			updateContext();
		},
		startDirectoryRead: () => { directoryController = new AbortController(); return directoryController.signal; },
		startComparison: () => {
			pinnedComparison = { name: 'a.txt', path: '/a.txt' };
			openComparison([pinnedComparison, { name: 'b.txt', path: '/b.txt' }]);
		},
		get: () => ({ fileContent, selectedFile, comparison, pinnedComparison, terminalContextRevision })
	};
`,
	{ compilerOptions: { target: ScriptTarget.ES2022 } }
).outputText;
describe('file browser source ownership', () => {
	it('aborts an old HTML read and ignores its late completion after switching chats', async () => {
		let resolve!: (value: string) => void;
		const readFile = vi.fn(
			(..._args: any[]) =>
				new Promise((done) => {
					resolve = done;
				})
		);
		const view = new Function('state', fileBody)({
			selectedTerminal: { url: '/terminal', key: 'token' },
			chatId: 'chat-a',
			readFile
		});
		const pending = view.openEntry({ name: 'index.html', type: 'file' });
		view.switchChat('chat-b');
		expect(readFile.mock.calls[0][4]?.aborted).toBe(true);
		resolve('<html>old chat</html>');
		await pending;
		expect(view.get().fileContent).toBe('current chat');
	});
});

const expandedDeclaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'fetchExpandedDir');
const expandedBody = transpileModule(
	`
	let { selectedTerminal, chatId, listFiles } = state;
	let terminalContextRevision = 0, directoryController = new AbortController();
	let loadingDirs = new Set(), treeCache = new Map();
	const asDirectoryPath = path => path;
	const normalizeEntries = entries => entries;
	const saveTreeState = () => {};
	const ${source.slice(expandedDeclaration.start, expandedDeclaration.end)};
	return { fetchExpandedDir, switchChat: () => { chatId = 'chat-b'; terminalContextRevision++; directoryController.abort(); },
		get: () => treeCache };
`,
	{ compilerOptions: { target: ScriptTarget.ES2022 } }
).outputText;

describe('expanded directory request ownership', () => {
	it('cancels expansion reads and rejects old tree entries after a chat switch', async () => {
		let resolve!: (value: any) => void;
		const listFiles = vi.fn(
			(..._args: any[]) =>
				new Promise((done) => {
					resolve = done;
				})
		);
		const view = new Function('state', expandedBody)({
			selectedTerminal: { url: '/terminal', key: 'token' },
			chatId: 'chat-a',
			listFiles
		});
		const pending = view.fetchExpandedDir('/work/sub/');
		view.switchChat();
		expect(listFiles.mock.calls[0][4]?.aborted).toBe(true);
		resolve({ entries: [{ name: 'old-tree' }] });
		await pending;
		expect(view.get().size).toBe(0);
	});
});

const originalConnection = { url: '/terminal', key: 'synthetic-original-key', system: false };
describe('file browser terminal context invalidation', () => {
	it.each([
		[
			'credentials at the same URL',
			{ terminal: { ...originalConnection, key: 'synthetic-new-key' } }
		],
		['connection kind at the same URL', { terminal: { ...originalConnection, system: true } }],
		['URL', { terminal: { ...originalConnection, url: '/replacement-terminal' } }],
		['selected terminal ID', { selectedId: 'replacement-id' }],
		['chat ID', { chatId: 'chat-b' }]
	])('cancels pending reads and clears comparisons when changing %s', async (_label, next) => {
		let resolve!: (value: string) => void;
		const readFile = vi.fn(
			(..._args: any[]) =>
				new Promise<string>((done) => {
					resolve = done;
				})
		);
		const view = new Function('state', fileBody)({
			selectedTerminal: originalConnection,
			chatId: 'chat-a',
			readFile
		});
		const pending = view.openEntry({ name: 'index.html', type: 'file' });
		const directorySignal = view.startDirectoryRead();
		view.startComparison();
		expect(view.get().comparison.terminal).toEqual(originalConnection);
		const revision = view.get().terminalContextRevision;
		view.switchContext(next);
		expect(readFile.mock.calls[0]).toEqual([
			'/terminal',
			'synthetic-original-key',
			'/work/index.html',
			'chat-a',
			expect.any(AbortSignal)
		]);
		expect(readFile.mock.calls[0][4].aborted).toBe(true);
		expect(directorySignal.aborted).toBe(true);
		expect(view.get()).toMatchObject({
			comparison: null,
			pinnedComparison: null,
			selectedFile: null,
			fileContent: null,
			terminalContextRevision: revision + 1
		});
		resolve('<html>old credentials</html>');
		await pending;
		expect(view.get().fileContent).toBeNull();
	});

	it('keeps pending reads and comparisons for an unchanged connection identity', async () => {
		let resolve!: (value: string) => void;
		const readFile = vi.fn(
			(..._args: any[]) =>
				new Promise<string>((done) => {
					resolve = done;
				})
		);
		const view = new Function('state', fileBody)({
			selectedTerminal: originalConnection,
			chatId: 'chat-a',
			readFile
		});
		const pending = view.openEntry({ name: 'index.html', type: 'file' });
		const directorySignal = view.startDirectoryRead();
		view.startComparison();
		const comparison = view.get().comparison;
		const revision = view.get().terminalContextRevision;
		view.switchContext({ terminal: { ...originalConnection } });
		expect(readFile.mock.calls[0][4].aborted).toBe(false);
		expect(directorySignal.aborted).toBe(false);
		expect(view.get().comparison).toBe(comparison);
		expect(view.get().terminalContextRevision).toBe(revision);
		resolve('<html>current credentials</html>');
		await pending;
		expect(view.get().fileContent).toBe('<html>current credentials</html>');
	});
});
