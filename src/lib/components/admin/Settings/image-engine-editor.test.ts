import { describe, expect, it } from 'vitest';

import { prepareImageProfiles } from './image-engine-editor';

const profile = {
	id: 'fixed-id',
	name: ' Studio ',
	generation_model: 'generation:model-a',
	edit_model: 'editor:model-b',
	tool_description_suffix: ' Draw\ncarefully ',
	edit_tool_description_suffix: ' Edit\ncarefully '
};

describe('paired image model editor', () => {
	it('keeps stable model references and trims both descriptions without mutating the form', () => {
		const [saved] = prepareImageProfiles([profile]);
		expect(saved).toEqual({
			...profile,
			name: 'Studio',
			tool_description_suffix: 'Draw\ncarefully',
			edit_tool_description_suffix: 'Edit\ncarefully'
		});
		expect(profile.tool_description_suffix).toBe(' Draw\ncarefully ');
	});
	it.each([null, 'disabled'])(
		'preserves the system editor or editing opt-out (%s)',
		(edit_model) => {
			expect(prepareImageProfiles([{ ...profile, edit_model }])[0].edit_model).toBe(edit_model);
		}
	);
	it('rejects missing targets, duplicate aliases and reserved IDs', () => {
		expect(() => prepareImageProfiles([{ ...profile, generation_model: '' }])).toThrow(
			'Select a model'
		);
		expect(() =>
			prepareImageProfiles([profile, { ...profile, id: 'other', name: 'studio' }])
		).toThrow('Engine aliases must be unique.');
		expect(() => prepareImageProfiles([{ ...profile, id: 'default' }])).toThrow(
			'Additional engine IDs must be unique'
		);
	});
});
