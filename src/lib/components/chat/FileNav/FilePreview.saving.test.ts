import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./FilePreview.svelte', import.meta.url), 'utf8');
const names = new Set(['saveCodeFile', 'saveIdentity']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => (node.declaration ?? node).declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');
function setup() {
	let complete!: () => void;
	const onSave = vi.fn(() => new Promise<void>((resolve) => (complete = resolve)));
	const body = `
 let selectedFile = '/work/readme.md', baseUrl = '/api/v1/terminals/system', apiKey = 'old-token', chatId = 'old-chat', resourceVersion = 1;
 let target = {selector: baseUrl, key: apiKey, context: {chatId, modelId: null}};
 let saving = false, codeValue = 'same draft';
 const readOnly = false, fileCodeEditorRef = null, isMarkdown = false, showRaw = false, searchTarget = null;
 ${declarations}
 return { saveCodeFile, replace: () => { apiKey = 'new-token'; chatId = 'new-chat'; target = {...target, key: apiKey, context: {chatId, modelId:null}}; resourceVersion++; saving = true; }, state: () => saving };
 `;
	return {
		...new Function(
			'onSave',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(onSave),
		complete: () => complete(),
		onSave
	};
}
describe('file preview save completion identity', () => {
	it('does not clear a newer context save after a late save completion', async () => {
		const preview = setup();
		const pending = preview.saveCodeFile();
		preview.replace();
		preview.complete();
		await pending;
		expect(preview.state()).toBe(true);
	});
	it('finishes the active edit when its captured context is still current', async () => {
		const preview = setup();
		const pending = preview.saveCodeFile();
		preview.complete();
		await pending;
		expect(preview.state()).toBe(false);
	});
});
