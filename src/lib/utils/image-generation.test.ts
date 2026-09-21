import { describe, expect, it } from 'vitest';
import { resolveImageGenerationEngineId } from './image-generation';

describe('resolveImageGenerationEngineId', () => {
	const engines = [
		{ id: 'studio', name: 'Studio' },
		{ id: 'draft', name: 'Fast drafts' }
	];

	it('restores the account selection using its stable ID even after a rename', () => {
		expect(resolveImageGenerationEngineId('studio', engines)).toBe('studio');
		expect(resolveImageGenerationEngineId('studio', [{ id: 'studio', name: 'Renamed' }])).toBe(
			'studio'
		);
	});

	it('uses the default for absent, malformed or deleted selections', () => {
		for (const value of [undefined, null, '', 42, {}, 'deleted']) {
			expect(resolveImageGenerationEngineId(value, engines)).toBe('');
		}
		expect(resolveImageGenerationEngineId('studio', [])).toBe('');
	});

	it('preserves a remembered selection while the catalog is loading or unavailable', () => {
		expect(resolveImageGenerationEngineId('studio', null)).toBe('studio');
		expect(resolveImageGenerationEngineId(undefined, null)).toBe('');
	});

	it('resolves account settings independently without mutating the catalog', () => {
		const before = structuredClone(engines);
		expect(resolveImageGenerationEngineId('studio', engines)).toBe('studio');
		expect(resolveImageGenerationEngineId('draft', engines)).toBe('draft');
		expect(resolveImageGenerationEngineId('', engines)).toBe('');
		expect(engines).toEqual(before);
	});
});
