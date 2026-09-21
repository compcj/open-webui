import { describe, expect, it } from 'vitest';

import type { ImageGenerationEngine } from '$lib/apis/images';
import { createImageEngineConfig, prepareImageEngineProfiles } from './image-engine-editor';

const profile = (): ImageGenerationEngine => ({
	id: 'profile-1',
	name: 'Studio',
	...createImageEngineConfig(),
	edit: {
		...createImageEngineConfig(),
		engine: 'gemini',
		model: 'edit-model',
		params: { generationConfig: { imageConfig: { aspectRatio: '4:3' } } }
	}
});

describe('paired image engine editor', () => {
	it('creates independent generation and editing settings', () => {
		const generation = createImageEngineConfig();
		const edit = createImageEngineConfig();
		generation.params.quality = 'high';
		generation.comfyui_workflow_nodes.push({ type: 'prompt', key: 'text', node_ids: ['1'] });

		expect(edit.params).toEqual({});
		expect(edit.comfyui_workflow_nodes).toEqual([]);
		expect(generation.tool_description_suffix).toBe('');
		expect(edit.tool_description_suffix).toBe('');
	});

	it('trims separate multiline tool descriptions through profile serialization', () => {
		const source = profile();
		source.tool_description_suffix = '  Generate\nwith care  ';
		if (source.edit) source.edit.tool_description_suffix = '\n Edit\nwith reference \n';
		const result = prepareImageEngineProfiles([source], {});
		expect(result[0].tool_description_suffix).toBe('Generate\nwith care');
		expect(result[0].edit?.tool_description_suffix).toBe('Edit\nwith reference');
		expect(source.tool_description_suffix).toBe('  Generate\nwith care  ');
	});

	it('loads missing legacy suffixes as empty and preserves default edit selection', () => {
		const source = { ...profile(), edit: null };
		delete (source as Partial<ImageGenerationEngine>).tool_description_suffix;
		const result = prepareImageEngineProfiles([source as ImageGenerationEngine], {});
		expect(result[0].tool_description_suffix).toBe('');
		expect(result[0].edit).toBeNull();
	});

	it('normalizes both provider settings without mutating the editor drafts', () => {
		const source = profile();
		source.name = ' Studio ';
		source.params = { old: true };
		const result = prepareImageEngineProfiles([source], {
			'profile-1': {
				generation: '{"quality":"high"}',
				edit: '{"generationConfig":{"imageConfig":{"aspectRatio":"16:9"}}}'
			}
		});

		expect(result[0].name).toBe('Studio');
		expect(result[0].params).toEqual({ quality: 'high' });
		expect(result[0].edit?.engine).toBe('gemini');
		expect(result[0].edit?.params).toEqual({
			generationConfig: { imageConfig: { aspectRatio: '16:9' } }
		});
		expect(source.params).toEqual({ old: true });
		expect(source.edit?.params).toEqual({
			generationConfig: { imageConfig: { aspectRatio: '4:3' } }
		});
		expect(result[0].edit?.params).not.toBe(result[0].params);
	});

	it('retains legacy profiles that use the default edit settings', () => {
		const source = { ...profile(), edit: null };
		const result = prepareImageEngineProfiles([source], {
			'profile-1': { generation: '{}', edit: '{invalid' }
		});

		expect(result[0].edit).toBeNull();
	});

	it('uses generateContent for Gemini editing even when a stored profile says predict', () => {
		const source = profile();
		if (source.edit) source.edit.gemini_endpoint_method = 'predict';
		const result = prepareImageEngineProfiles([source], {});

		expect(result[0].edit?.gemini_endpoint_method).toBe('generateContent');
		expect(source.edit?.gemini_endpoint_method).toBe('predict');
	});

	it('rejects invalid edit JSON without silently replacing the draft', () => {
		const drafts = { 'profile-1': { generation: '{}', edit: '{invalid' } };
		expect(() => prepareImageEngineProfiles([profile()], drafts)).toThrow(
			'Edit additional parameters must be a valid JSON object.'
		);
		expect(drafts['profile-1'].edit).toBe('{invalid');
	});

	it('rejects invalid edit workflows and duplicate aliases', () => {
		const source = profile();
		source.edit = { ...createImageEngineConfig(), engine: 'comfyui', comfyui_workflow: '[]' };
		expect(() => prepareImageEngineProfiles([source], {})).toThrow(
			'Edit ComfyUI workflow must be a valid JSON object.'
		);

		const duplicate = { ...profile(), id: 'profile-2', name: 'studio' };
		expect(() => prepareImageEngineProfiles([profile(), duplicate], {})).toThrow(
			'Engine aliases must be unique.'
		);
	});
});
