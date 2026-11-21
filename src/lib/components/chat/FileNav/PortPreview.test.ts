import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('../Tabs/Browser.svelte', import.meta.url), 'utf8');
const ast = parse(source);
const findIframe = (node: any): any => {
	if (node?.name === 'iframe') return node;
	for (const value of Object.values(node ?? {})) {
		if (!value || typeof value !== 'object') continue;
		for (const child of Array.isArray(value) ? value : [value]) {
			const result = findIframe(child);
			if (result) return result;
		}
	}
};
const attribute = findIframe(ast.html).attributes.find(
	(attribute: any) => attribute.name === 'sandbox'
);
const sandbox = (settings: Record<string, unknown>) =>
	attribute.value
		.map((chunk: any) =>
			chunk.type === 'Text'
				? chunk.data
				: new Function(
						'$settings',
						`return (${source.slice(chunk.expression.start, chunk.expression.end)});`
					)(settings)
		)
		.join('');

describe('terminal browser Proxy iframe isolation', () => {
	it.each([
		{},
		{ terminalPreviewAllowSameOrigin: true },
		{ terminalPreviewAllowSameOrigin: false }
	])('never grants same-origin permission for remembered settings %j', (settings) => {
		const tokens = sandbox(settings).split(/\s+/);
		expect(tokens.sort()).toEqual(['allow-downloads', 'allow-forms', 'allow-scripts']);
		expect(tokens).not.toContain('allow-same-origin');
	});

	it('navigates through the browser panel when history crosses a port ticket boundary', () => {
		const declarations = ast
			.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
			.filter((node: any) => ['localhostPort', 'move'].includes(node.id?.name))
			.map((node: any) => `const ${source.slice(node.start, node.end)};`)
			.join('\n');
		const navigate = vi.fn();
		const frameCommand = vi.fn();
		new Function(
			'navigate',
			'frameCommand',
			transpileModule(
				`const history = ['http://localhost:3000/', 'http://localhost:3001/'];
				const session = { mode: 'proxy' }, previewPort = 3001, frameReady = true;
				let historyIndex = 1;
				${declarations}
				move(-1);`,
				{ compilerOptions: { target: ScriptTarget.ES2022 } }
			).outputText
		)(navigate, frameCommand);
		expect(navigate).toHaveBeenCalledWith('http://localhost:3000/', false);
		expect(frameCommand).not.toHaveBeenCalled();
	});
});

describe('terminal HTML preview isolation', () => {
	it('does not grant same-origin permission through general artifact preferences', () => {
		const htmlSource = readFileSync(new URL('./FilePreview.svelte', import.meta.url), 'utf8');
		const htmlAttribute = findIframe(parse(htmlSource).html).attributes.find(
			(attribute: any) => attribute.name === 'sandbox'
		);
		const tokens = htmlAttribute.value
			.map((chunk: any) =>
				chunk.type === 'Text'
					? chunk.data
					: new Function(
							'$settings',
							'allowScripts',
							`return (${htmlSource.slice(chunk.expression.start, chunk.expression.end)});`
						)({ iframeSandboxAllowSameOrigin: true }, true)
			)
			.join('')
			.split(/\s+/);
		expect(tokens).not.toContain('allow-same-origin');
	});
});
