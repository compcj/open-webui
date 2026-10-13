import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./PortPreview.svelte', import.meta.url), 'utf8');
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

describe('terminal port iframe isolation', () => {
	it.each([
		{},
		{ terminalPreviewAllowSameOrigin: true },
		{ terminalPreviewAllowSameOrigin: false }
	])('never grants same-origin permission for remembered settings %j', (settings) => {
		const tokens = sandbox(settings).split(/\s+/);
		expect(tokens).toContain('allow-scripts');
		expect(tokens).not.toContain('allow-same-origin');
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
