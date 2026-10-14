import { describe, expect, it } from 'vitest';
import { resolveToolSchemaCompatibility } from './model-tool-schema';
import { migrateModelConfiguration } from './model-controls';

describe('manual tool schema compatibility', () => {
	it('keeps Gemini names off unless explicitly selected', () => {
		expect(
			resolveToolSchemaCompatibility('google/gemini-test', [{ id: 'google/gemini-test' }])
		).toEqual({ value: 'default', source: null });
	});

	it('enables an opaque alias and displays inherited manual choices', () => {
		const models = [
			{ id: 'preset', info: { base_model_id: 'opaque' } },
			{ id: 'opaque', info: { meta: { tool_schema_compatibility: 'gemini' } } }
		];
		expect(resolveToolSchemaCompatibility('preset', models)).toEqual({
			value: 'gemini',
			source: 'opaque'
		});
	});

	it.each(['default', 'invalid', true])('does not revive inherited choices for %s', (mode) => {
		expect(
			resolveToolSchemaCompatibility('preset', [
				{ id: 'preset', meta: { tool_schema_compatibility: mode }, base_model_id: 'base' },
				{ id: 'base', meta: { tool_schema_compatibility: 'gemini' } }
			])
		).toEqual({ value: 'default', source: 'preset' });
	});

	it('ends missing and cyclic inheritance at the default', () => {
		expect(resolveToolSchemaCompatibility('missing', [])).toEqual({
			value: 'default',
			source: null
		});
		expect(
			resolveToolSchemaCompatibility('a', [
				{ id: 'a', base_model_id: 'b' },
				{ id: 'b', base_model_id: 'a' }
			])
		).toEqual({ value: 'default', source: null });
	});

	it('does not follow direct descriptors into server configurations', () => {
		expect(
			resolveToolSchemaCompatibility('direct', [
				{ id: 'direct', direct: true, info: { base_model_id: 'private' } },
				{ id: 'private', meta: { tool_schema_compatibility: 'gemini' } }
			])
		).toEqual({ value: 'default', source: null });
	});

	it.each([null, 'default', 'gemini'] as const)(
		'preserves %s across model import/export',
		(mode) => {
			const input = {
				id: 'opaque',
				name: 'Model',
				meta: { tool_schema_compatibility: mode },
				params: {}
			};
			const exported = JSON.stringify(migrateModelConfiguration(input));
			expect(migrateModelConfiguration(JSON.parse(exported)).meta.tool_schema_compatibility).toBe(
				mode
			);
			expect(input.meta.tool_schema_compatibility).toBe(mode);
		}
	);
});
