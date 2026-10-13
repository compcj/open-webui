import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('../Skills.svelte', import.meta.url), 'utf8');
const tree = parse(source);
const findUrlAction = (node: any): any => {
	if (!node || typeof node !== 'object') return;
	if (
		node.type === 'ObjectExpression' &&
		node.properties.some(
			(property: any) =>
				property.key?.name === 'id' && property.value?.value === 'skills-import-url'
		)
	)
		return node;
	for (const value of Object.values(node)) {
		for (const child of Array.isArray(value) ? value : [value]) {
			const result = findUrlAction(child);
			if (result) return result;
		}
	}
};
const action = findUrlAction(tree.instance);
const visible = action.properties.find((property: any) => property.key?.name === 'visible').value;
const canSeeUrlImport = new Function('$user', `return ${source.slice(visible.start, visible.end)}`);

describe('skill URL import action', () => {
	it.each([
		[{ role: 'admin' }, true],
		[{ role: 'user', permissions: { workspace: { skills_import: true } } }, true],
		[{ role: 'user', permissions: { workspace: { skills: true } } }, false],
		[{ role: 'user' }, false]
	])('matches the backend import permission for %j', (user, expected) => {
		expect(canSeeUrlImport(user)).toBe(expected);
	});
});
