import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./ToolOutputFile.svelte', import.meta.url), 'utf8');
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => {
		if (node.type === 'FunctionDeclaration' && node.id.name === 'blobForPreview')
			return [source.slice(node.start, node.end)];
		const tracked = node.declarations?.find((entry: any) => entry.id.name === 'previewIsCurrent');
		return tracked ? ['const ' + source.slice(tracked.start, tracked.end) + ';'] : [];
	})
	.join('\n');

const createCard = new Function(
	'getFileContentById',
	transpileModule(
		`
	let previewKey = 'owner|document', fileId = 'document', name = 'doc.txt', item = {}, disposed = false, unavailable = false;
	${declarations}
	return { read: () => blobForPreview('owner|document'), switchOwner: () => {previewKey = 'viewer|document';} };
`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText
);

it('discards a pending document when the displayed account changes and rejects late reads', async () => {
	let finish!: (value: string) => void;
	const fetchDocument = vi.fn().mockReturnValue(
		new Promise((resolve) => {
			finish = resolve;
		})
	);
	const card = createCard(fetchDocument);
	const pending = card.read();
	card.switchOwner();
	finish('original document');
	expect(await pending).toBeNull();
	expect(await card.read()).toBeNull();
	expect(fetchDocument).toHaveBeenCalledTimes(1);
});
