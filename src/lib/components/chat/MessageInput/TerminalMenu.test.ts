import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./TerminalMenu.svelte', import.meta.url), 'utf8');
type Declaration = { id: { name: string }; start: number; end: number };
const selectors = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: Declaration) => node.id.name === 'toggleTerminal')
	.map((node: Declaration) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const createMenu = new Function(
	'deps',
	transpileModule(
		`
	const { $config, $settings, window, showTabs, directTerminals } = deps;
	const settings = {set() {}}, refreshTerminalServersStore = async () => {};
	const closeMenu = () => {}, chatId = 'chat';
	let $selectedTerminalId = null;
	const selectedTerminalId = { set(value) { $selectedTerminalId = value; } };
	const selectTerminal = (store, value) => store.set(value);
	${selectors}
	return { toggleTerminal };
`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText
);

describe.each(['system', 'https://terminal.test'])('selection of %s', (id) => {
	it.each([
		[undefined, true, true, false],
		[false, true, true, false],
		[true, false, true, true],
		[true, true, false, false]
	])(
		'global %s, legacy %s, desktop %s opens the files tab %s',
		async (policy, legacy, desktop, opens) => {
			const showTabs = { set: vi.fn() };
			const menu = createMenu({
				$config: { features: { show_files_on_terminal_select: policy } },
				$settings: { showFilesOnTerminalSelect: legacy },
				directTerminals: id === 'system' ? [] : [{ url: id }],
				window: { matchMedia: () => ({ matches: desktop }) },
				showTabs
			});
			await menu.toggleTerminal(id);
			expect(showTabs.set.mock.calls).toEqual(opens ? [['files']] : []);
			showTabs.set.mockClear();
			await menu.toggleTerminal(id);
			expect(showTabs.set).not.toHaveBeenCalled();
		}
	);
});
