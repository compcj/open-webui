import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./TerminalMenu.svelte', import.meta.url), 'utf8');
type Declaration = { id: { name: string }; start: number; end: number };
const selectors = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: Declaration) =>
		['selectTerminal', 'selectSystem', 'selectDirect'].includes(node.id.name)
	)
	.map((node: Declaration) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
const createMenu = new Function(
	'deps',
	transpileModule(
		`
	const { $config, $settings, window, showTabs } = deps;
	const settings = {set() {}}, refreshTerminalServersStore = async () => {};
	let show = true, $selectedTerminalId = null;
	const selectedTerminalId = { set(value) { $selectedTerminalId = value; } };
	${selectors}
	return { selectSystem, selectDirect };
`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText
);

describe.each(['selectSystem', 'selectDirect'])('%s', (method) => {
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
				window: { matchMedia: () => ({ matches: desktop }) },
				showTabs
			});
			const terminal = { id: 'system', url: 'https://terminal.test' };
			await menu[method](terminal);
			expect(showTabs.set.mock.calls).toEqual(opens ? [['files']] : []);
			showTabs.set.mockClear();
			await menu[method](terminal);
			expect(showTabs.set).not.toHaveBeenCalled();
		}
	);
});
