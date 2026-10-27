import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'isTerminalAvailable');
const code = transpileModule(
	`
 const $config = { features: { enable_tool_servers: true, enable_direct_integrations: enabled } };
 const $terminalServers = [{ id: 'system' }];
 const $settings = { terminalServers: [{ url: 'https://terminal.example', enabled: true }] };
 const ${source.slice(declaration.start, declaration.end)};
 return isTerminalAvailable;
`,
	{ compilerOptions: { target: ScriptTarget.ES2022 } }
).outputText;

describe('saved direct terminal runtime gate', () => {
	it.each([false, undefined])(
		'excludes personal connections when Direct Integrations is %s',
		(enabled) => {
			const available = new Function('enabled', code)(enabled);
			expect(available('https://terminal.example')).toBe(false);
			expect(available('system')).toBe(true);
		}
	);
	it('keeps enabled personal connection configuration usable when opted in', () => {
		expect(new Function('enabled', code)(true)('https://terminal.example')).toBe(true);
	});
});
