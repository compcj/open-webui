import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./FilePreview.svelte', import.meta.url), 'utf8');
const names = new Set(['saveEdit', 'saveCodeFile', 'saveIdentity']);
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
 let editing = true, saving = false, editContent = 'same draft', codeValue = 'same draft';
 const readOnly = false, fileCodeEditorRef = null;
 ${declarations}
 return { saveEdit, saveCodeFile, replace: () => { apiKey = 'new-token'; chatId = 'new-chat'; resourceVersion++; editing = true; saving = true; }, state: () => ({editing, saving}) };
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
	it.each(['saveEdit', 'saveCodeFile'])(
		'does not clear a newer context save after late %s completion',
		async (method) => {
			const preview = setup();
			const pending = preview[method]();
			preview.replace();
			preview.complete();
			await pending;
			expect(preview.state()).toEqual({ editing: true, saving: true });
		}
	);
	it('finishes the active edit when its captured context is still current', async () => {
		const preview = setup();
		const pending = preview.saveEdit();
		preview.complete();
		await pending;
		expect(preview.state()).toEqual({ editing: false, saving: false });
	});
});
