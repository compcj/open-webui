import { EditorState, Text } from '@codemirror/state';
import { Chunk } from '@codemirror/merge';
import { describe, expect, it } from 'vitest';
import { copyChunk, keepBothChunk, changedLineCounts, diffEditorConfig } from './diffEditor';
import { pairedLinePosition } from './diffEditorAlignment';

const docs = (original: string, proposed: string) => {
	const a = Text.of(original.split('\n')),
		b = Text.of(proposed.split('\n'));
	return { a, b, chunks: Chunk.build(a, b, diffEditorConfig) };
};
const apply = (doc: Text, change: any) =>
	EditorState.create({ doc }).update({ changes: change }).state.doc.toString();

describe('revision comparison changes', () => {
	it.each([
		['old', 'new', 'old\nnew'],
		['prefix\nold\nsuffix', 'prefix\nnew\nsuffix', 'prefix\nold\nnew\nsuffix'],
		['old\n', 'new\n', 'old\nnew\n'],
		['旧版本', '新版本', '旧版本\n新版本']
	])('keeps original then proposed for %j / %j', (original, proposed, expected) => {
		const { a, b, chunks } = docs(original, proposed);
		const change = keepBothChunk(a, b, chunks[0]);
		expect(change).not.toBeNull();
		expect(apply(b, change)).toBe(expected);
		expect(a.toString()).toBe(original);
	});
	it.each([
		['', 'new'],
		['old', ''],
		['old', 'old\nnew']
	])('does not duplicate pure insertion or deletion: %j / %j', (original, proposed) => {
		const { a, b, chunks } = docs(original, proposed);
		expect(keepBothChunk(a, b, chunks[0])).toBeNull();
	});
	it.each([
		['old', 'new'],
		['same\nold', 'same\nnew\n'],
		['', 'new'],
		['old\n', '']
	])('copies complete chunks in either direction: %j / %j', (original, proposed) => {
		const { a, b, chunks } = docs(original, proposed);
		expect(apply(a, copyChunk(a, b, chunks[0], 'to-working'))).toBe(proposed);
		expect(apply(b, copyChunk(a, b, chunks[0], 'to-proposal'))).toBe(original);
	});
	it('counts line changes and maps unaffected rows after an insertion', () => {
		const { a, b, chunks } = docs('one\ntwo\nthree', 'one\ninserted\ntwo\nthree');
		expect(changedLineCounts(a, b, chunks)).toEqual({ removed: 0, added: 1 });
		expect(pairedLinePosition(a, b, chunks, a.line(3).from)).toBe(b.line(4).from);
	});
});
