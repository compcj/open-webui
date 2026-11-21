import { describe, expect, it } from 'vitest';
import {
	activateTab,
	createTabsState,
	mainChatTabId,
	openTab,
	openTabInSplit,
	getLayoutRects,
	setMainChatTab,
	splitTab,
	type Tab
} from './tabs';

const sideTab = (id: string): Tab => ({ id, kind: 'chat', label: id });

it('adds background file tabs without changing the selected tabs, pane or navigation history', () => {
	const file: Tab = { id: 'report', kind: 'file', label: 'Report' };
	const split = openTabInSplit(setMainChatTab(createTabsState(), true), sideTab('side'));
	const initial = activateTab(split, mainChatTabId);
	const next = openTab(initial, file, false);
	expect(next.tabs).toContainEqual(file);
	expect(next.activeId).toBe(mainChatTabId);
	expect(next.activeGroupId).toBe(initial.activeGroupId);
	expect(next.groups.map((group) => group.activeId)).toEqual(
		initial.groups.map((group) => group.activeId)
	);
	expect(next.layout).toEqual(initial.layout);
	expect(next.history).toEqual(initial.history);

	const otherPane = activateTab(next, 'side');
	expect(openTab(otherPane, file, false)).toBe(otherPane);
	expect(openTab(otherPane, file).activeId).toBe(file.id);
});

describe('opening a tab in the side panel', () => {
	it('creates a right pane and keeps the main chat visible', () => {
		const initial = openTab(setMainChatTab(createTabsState(), true), {
			id: 'files',
			kind: 'files',
			label: 'Files'
		});
		const state = openTabInSplit(initial, { ...sideTab('side-1'), initialPrompt: 'Hello' });
		const main = state.groups.find((group) => group.tabIds.includes(mainChatTabId))!;
		const side = state.groups.find((group) => group.tabIds.includes('side-1'))!;
		expect(state.groups).toHaveLength(2);
		expect(main.activeId).toBe(mainChatTabId);
		expect(main.tabIds).toContain('files');
		expect(state.activeId).toBe('side-1');
		expect(getLayoutRects(state.layout).groups[side.id].left).toBe(0.5);
		expect(state.tabs.find((tab) => tab.id === 'side-1')?.initialPrompt).toBe('Hello');
	});

	it('reuses the existing side pane when the main chat is focused', () => {
		let state = openTabInSplit(setMainChatTab(createTabsState(), true), sideTab('side-1'));
		const sideGroupId = state.activeGroupId;
		const layout = state.layout;
		state = openTabInSplit(activateTab(state, mainChatTabId), sideTab('side-2'));
		expect(state.groups).toHaveLength(2);
		expect(state.layout).toEqual(layout);
		expect(state.activeGroupId).toBe(sideGroupId);
		expect(state.groups.find((group) => group.id === sideGroupId)?.tabIds).toEqual([
			'side-1',
			'side-2'
		]);
		expect(state.groups.find((group) => group.tabIds.includes(mainChatTabId))?.activeId).toBe(
			mainChatTabId
		);
	});

	it('uses the focused side pane when there are multiple splits', () => {
		let state = openTabInSplit(setMainChatTab(createTabsState(), true), sideTab('side-1'));
		state = openTab(state, sideTab('side-2'));
		state = splitTab(state, 'side-2', state.activeGroupId, 'bottom');
		const sideGroupId = state.activeGroupId;
		const layout = state.layout;
		state = openTabInSplit(state, sideTab('side-3'));
		expect(state.groups).toHaveLength(3);
		expect(state.layout).toEqual(layout);
		expect(state.activeGroupId).toBe(sideGroupId);
		expect(state.activeId).toBe('side-3');
	});

	it('opens normally when the main chat is outside the tab layout', () => {
		const state = openTabInSplit(createTabsState(), sideTab('side-1'));
		expect(state.groups).toHaveLength(1);
		expect(state.activeId).toBe('side-1');
	});
});
