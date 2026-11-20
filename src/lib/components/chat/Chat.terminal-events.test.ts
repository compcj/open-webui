import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'terminalEventHandler');

const createChat = (options: { terminalContext?: any; files?: any[]; inline?: boolean } = {}) => {
	const showFileNavDir = { set: vi.fn() };
	const refreshFiles = vi.fn();
	const displayTerminalFile = vi.fn();
	const handler = new Function(
		'deps',
		transpileModule(
			`const { showFileNavDir, terminalTabs, terminalContext, history, displayTerminalFile, $settings } = deps;
			const $selectedTerminalId = 'terminal-a', $chatId = 'chat-a';
			const ${source.slice(declaration.start, declaration.end)};
			return terminalEventHandler;`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)({
		showFileNavDir,
		displayTerminalFile,
		$settings: { terminalFileDisplay: options.inline ? 'inline' : 'sidebar' },
		terminalTabs: { refreshFiles },
		terminalContext: options.terminalContext ?? null,
		history: { messages: { user: { files: options.files ?? [] } } }
	});
	return {
		runCommand: () => handler('terminal:run_command', {}, { parentId: 'user' }),
		display: (path: string) =>
			handler('terminal:display_file', { path, page: 3 }, { parentId: 'user' }),
		displayTerminalFile,
		showFileNavDir,
		refreshFiles
	};
};

describe('terminal command events', () => {
	it('requests an existing-browser refresh without opening or navigating a browser', () => {
		const chat = createChat();
		chat.runCommand();
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
		expect(chat.refreshFiles).toHaveBeenCalledOnce();
	});

	it('keeps a selected file preview untouched', () => {
		const chat = createChat({
			terminalContext: { terminalId: 'terminal-a', file: { path: '/work/note.md' } }
		});
		chat.runCommand();
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
		expect(chat.refreshFiles).not.toHaveBeenCalled();
	});

	it('keeps filesystem attachment commands scoped to their existing flow', () => {
		const chat = createChat({
			files: [{ type: 'filesystem', terminal_id: 'terminal-b', path: '/other/note.md' }]
		});
		chat.runCommand();
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
		expect(chat.refreshFiles).not.toHaveBeenCalled();
	});
});

describe('displaying terminal files', () => {
	it('sends each model file to its chat and terminal, including its target page', () => {
		const chat = createChat();
		chat.display('/work/a.pdf');
		chat.display('/work/b.pdf');
		expect(chat.displayTerminalFile.mock.calls).toEqual(
			['/work/a.pdf', '/work/b.pdf'].map((path) => [
				{ chatId: 'chat-a', terminalId: 'terminal-a', path, page: 3, refreshOnly: false }
			])
		);
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
	});

	it('keeps inline files in the message', () => {
		const chat = createChat({ inline: true });
		chat.display('/work/a.pdf');
		expect(chat.displayTerminalFile).not.toHaveBeenCalled();
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
	});

	it('refreshes only the matching attached preview without requesting a directory', () => {
		const chat = createChat({
			files: [{ type: 'filesystem', terminal_id: 'terminal-a' }],
			terminalContext: { file: { path: '/work/a.pdf' } }
		});
		chat.display('/work/b.pdf');
		chat.display('/work/a.pdf');
		expect(chat.displayTerminalFile.mock.calls).toEqual([
			[
				{
					chatId: 'chat-a',
					terminalId: 'terminal-a',
					path: '/work/a.pdf',
					page: 3,
					refreshOnly: true
				}
			]
		]);
		expect(chat.showFileNavDir.set).not.toHaveBeenCalled();
	});
});

const tabsSource = readFileSync(new URL('./ChatTabs.svelte', import.meta.url), 'utf8');
const createTabs = (state: { viewReady?: boolean; terminalFilesAvailable?: boolean } = {}) => {
	const declaration: any = parse(tabsSource)
		.instance!.content.body.flatMap(
			(node: any) => node.declarations ?? node.declaration?.declarations ?? []
		)
		.find((node: any) => node.id?.name === 'refreshFiles');
	expect(declaration, 'ChatTabs must expose a refresh-only operation').toBeDefined();
	const tabs = {
		activeId: 'chat',
		tabs: [
			{ id: 'browser', kind: 'files', terminalId: 'terminal-a', directory: '/work/project/' },
			{ id: 'other', kind: 'files', terminalId: 'terminal-b', directory: '/other/' },
			{ id: 'editor', kind: 'file', terminalId: 'terminal-a', path: '/work/note.md' },
			{ id: 'unmounted', kind: 'files', terminalId: 'terminal-a' }
		]
	};
	const fileViews = Object.fromEntries(
		['browser', 'other', 'editor'].map((id) => [id, { refreshDirectory: vi.fn() }])
	);
	const showTabs = { set: vi.fn() };
	const refreshFiles = new Function(
		'deps',
		transpileModule(
			`const {tabs, fileViews, showTabs, viewReady, terminalFilesAvailable} = deps;
			const $selectedTerminalId = 'terminal-a';
			const ${tabsSource.slice(declaration.start, declaration.end)};
			return refreshFiles;`,
			{ compilerOptions: { target: ScriptTarget.ES2022 } }
		).outputText
	)({ tabs, fileViews, showTabs, viewReady: true, terminalFilesAvailable: true, ...state });
	return { tabs, fileViews, showTabs, refreshFiles };
};

describe('refreshing existing terminal browsers', () => {
	it('refreshes only matching directory tabs without changing their path or focus', () => {
		const view = createTabs();
		const before = structuredClone(view.tabs);
		view.refreshFiles();
		expect(view.fileViews.browser.refreshDirectory).toHaveBeenCalledOnce();
		expect(view.fileViews.other.refreshDirectory).not.toHaveBeenCalled();
		expect(view.fileViews.editor.refreshDirectory).not.toHaveBeenCalled();
		expect(view.tabs).toEqual(before);
		expect(view.showTabs.set).not.toHaveBeenCalled();
	});

	it('does not create or reopen a closed browser', () => {
		const view = createTabs();
		view.tabs.tabs = [];
		view.refreshFiles();
		expect(view.tabs.tabs).toEqual([]);
		expect(view.showTabs.set).not.toHaveBeenCalled();
		for (const browser of Object.values(view.fileViews)) {
			expect(browser.refreshDirectory).not.toHaveBeenCalled();
		}
	});

	it.each([{ viewReady: false }, { terminalFilesAvailable: false }])(
		'skips a view that is not ready or accessible: %j',
		(state) => {
			const view = createTabs(state);
			view.refreshFiles();
			for (const browser of Object.values(view.fileViews)) {
				expect(browser.refreshDirectory).not.toHaveBeenCalled();
			}
		}
	);
});
