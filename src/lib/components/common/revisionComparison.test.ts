import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { describe, expect, it, vi } from 'vitest';

// Execute the actual editor handlers without rendering CodeMirror or starting a backend.
const editor = (file: string, initial: Record<string, any>) => {
	const source = readFileSync(new URL(file, import.meta.url), 'utf8');
	const ast = parse(source);
	const handlers = new Set(['compareRevision', 'applyComparison']);
	const declarations = ast
		.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
		.filter((node: any) => handlers.has(node.id?.name))
		.map((node: any) => `const ${source.slice(node.start, node.end)};`)
		.join('\n');
	const state = {
		...initial,
		comparison: null,
		showComparison: false,
		$i18n: { t: (text: string) => text }
	};
	const code = transpileModule(
		`${Object.keys(state)
			.map((name) => `let ${name} = state[${JSON.stringify(name)}];`)
			.join('\n')}
		${declarations}
		return { compareRevision, applyComparison,
			set: patch => { ${Object.keys(state)
				.map(
					(name) =>
						`if (${JSON.stringify(name)} in patch) ${name} = patch[${JSON.stringify(name)}];`
				)
				.join('\n')} },
			get: () => ({ ${Object.keys(state).join(', ')} }) };`,
		{ compilerOptions: { target: ScriptTarget.ES2022 } }
	).outputText;
	return new Function('state', code)(state);
};

const skillEditor = () => {
	let buffer = 'draft';
	return editor('../workspace/Skills/SkillFiles.svelte', {
		readOnly: false,
		loading: false,
		loadError: '',
		loadedPath: 'SKILL.md',
		objectUrl: '',
		value: buffer,
		loadKey: 'skill:v1:0',
		requestId: 1,
		preview: true,
		files: [{ path: 'SKILL.md' }],
		flush: vi.fn(),
		codeEditor: {
			getValue: () => buffer,
			setValue: (content: string) => {
				buffer = content;
			}
		}
	});
};

describe('skill file comparison adoption', () => {
	it('applies a proposal to the unchanged draft', () => {
		const view = skillEditor();
		view.compareRevision();
		view.applyComparison('adopted');
		expect(view.get().value).toBe('adopted');
		expect(view.get().codeEditor.getValue()).toBe('adopted');
	});
	it('rejects an editor buffer changed after opening the comparison', () => {
		const view = skillEditor();
		view.compareRevision();
		view.get().codeEditor.setValue('newer unsaved draft');
		expect(() => view.applyComparison('stale proposal')).toThrow('The file has changed.');
		expect(view.get().codeEditor.getValue()).toBe('newer unsaved draft');
	});
	it.each([
		{ loadedPath: 'other.md' },
		{ loadKey: 'skill:v2:0' },
		{ requestId: 2 },
		{ readOnly: true },
		{ files: [] }
	])('rejects a changed resource: %j', (patch) => {
		const view = skillEditor();
		view.compareRevision();
		view.set(patch);
		expect(() => view.applyComparison('stale proposal')).toThrow('The file has changed.');
	});
});

describe('model comparison adoption', () => {
	const create = () =>
		editor('../workspace/Models/ModelEditor.svelte', {
			loaded: true,
			readOnly: false,
			historical: false,
			loading: false,
			promotingVersion: false,
			id: 'model',
			model: { id: 'model', version_id: 'v1' },
			system: 'draft',
			showSystemPrompt: false
		});
	it('adopts unchanged model prompt', () => {
		const view = create();
		view.compareRevision();
		view.applyComparison('proposal');
		expect(view.get().system).toBe('proposal');
	});
	it.each([
		{ system: 'changed' },
		{ id: 'other' },
		{ model: { id: 'model', version_id: 'v2' } },
		{ historical: true }
	])('rejects stale draft or resource: %j', (patch) => {
		const view = create();
		view.compareRevision();
		view.set(patch);
		expect(() => view.applyComparison('proposal')).toThrow('The model has changed.');
	});
});

describe('prompt comparison adoption', () => {
	const create = () =>
		editor('../workspace/Prompts/PromptEditor.svelte', {
			ready: true,
			readOnly: false,
			loading: false,
			historyLoading: false,
			prompt: { id: 'prompt', version_id: 'v1' },
			selectedVersionId: 'v1',
			content: 'draft',
			command: 'prompt'
		});
	it('adopts unchanged prompt', () => {
		const view = create();
		view.compareRevision();
		view.applyComparison('proposal');
		expect(view.get().content).toBe('proposal');
	});
	it.each([
		{ content: 'changed' },
		{ selectedVersionId: 'v2' },
		{ prompt: { id: 'other', version_id: 'v1' } },
		{ prompt: { id: 'prompt', version_id: 'v2' } }
	])('rejects stale draft or resource: %j', (patch) => {
		const view = create();
		view.compareRevision();
		view.set(patch);
		expect(() => view.applyComparison('proposal')).toThrow('The prompt has changed.');
	});
});
