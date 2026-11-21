import type { ImageProfile } from '$lib/apis/images';

// Translation markers for the validation errors displayed by Images.svelte.
// t('Engine alias is required.')
// t('Engine aliases must be unique.')
// t('Additional engine IDs must be unique and cannot be default.')
// t('Select a model')
export function prepareImageProfiles(profiles: ImageProfile[]): ImageProfile[] {
	const ids = new Set<string>();
	const names = new Set<string>();
	return profiles.map((profile) => {
		const id = profile.id.trim();
		const name = profile.name.trim();
		if (!name) throw new Error('Engine alias is required.');
		if (!id || id.toLowerCase() === 'default' || ids.has(id.toLowerCase()))
			throw new Error('Additional engine IDs must be unique and cannot be default.');
		if (names.has(name.toLowerCase())) throw new Error('Engine aliases must be unique.');
		if (!profile.generation_model) throw new Error('Select a model');
		ids.add(id.toLowerCase());
		names.add(name.toLowerCase());
		return {
			id,
			name,
			generation_model: profile.generation_model,
			edit_model: profile.edit_model || null,
			tool_description_suffix: (profile.tool_description_suffix ?? '').trim(),
			edit_tool_description_suffix: (profile.edit_tool_description_suffix ?? '').trim()
		};
	});
}
