import { describe, expect, it } from 'vitest';
import {
	createToolFeatureSaveQueue,
	getContextToolAvailability,
	getToolFeatureModelId,
	resolveToolFeatureState,
	updateToolFeaturePreference
} from './tool-feature-preferences';

describe('getToolFeatureModelId', () => {
	it('uses a non-empty @ model override', () => {
		expect(getToolFeatureModelId(['selected'], 'mentioned')).toBe('mentioned');
	});

	it('uses one non-empty selected model', () => {
		expect(getToolFeatureModelId(['model-a'])).toBe('model-a');
	});

	it('excludes empty, missing, and multiple model selections', () => {
		expect(getToolFeatureModelId([])).toBeNull();
		expect(getToolFeatureModelId([''])).toBeNull();
		expect(getToolFeatureModelId(['model-a', 'model-b'])).toBeNull();
		expect(getToolFeatureModelId(['model-a', 'model-a'])).toBeNull();
	});
});

describe('resolveToolFeatureState', () => {
	const contextDefaults = { knowledge: false, memory: false, notes: false };
	const defaults = { ...contextDefaults, web_search: true, image_generation: false };
	const available = {
		knowledge: true,
		memory: true,
		notes: true,
		web_search: true,
		image_generation: true
	};

	it('keeps model preferences and features independent', () => {
		const preferences = {
			'model-a': { web_search: false, image_generation: true },
			'model-b': { web_search: true, image_generation: false }
		};

		expect(
			resolveToolFeatureState({ modelId: 'model-a', preferences, defaults, available })
		).toEqual({ ...contextDefaults, web_search: false, image_generation: true });
		expect(
			resolveToolFeatureState({ modelId: 'model-b', preferences, defaults, available })
		).toEqual({ ...contextDefaults, web_search: true, image_generation: false });
	});

	it('distinguishes explicit false from missing and ignores malformed values', () => {
		const preferences = {
			explicit: { web_search: false },
			missing: {},
			malformed: { web_search: 'false', image_generation: 1 }
		} as never;

		expect(
			resolveToolFeatureState({ modelId: 'explicit', preferences, defaults, available })
		).toEqual({ ...contextDefaults, web_search: false, image_generation: false });
		expect(
			resolveToolFeatureState({ modelId: 'missing', preferences, defaults, available })
		).toEqual(defaults);
		expect(
			resolveToolFeatureState({ modelId: 'malformed', preferences, defaults, available })
		).toEqual(defaults);
	});

	it('ignores preferences without a model and lets boolean draft overrides win', () => {
		const preferences = { 'model-a': { web_search: false, image_generation: false } };

		expect(resolveToolFeatureState({ modelId: null, preferences, defaults, available })).toEqual(
			defaults
		);
		expect(
			resolveToolFeatureState({
				modelId: 'model-a',
				preferences,
				defaults,
				available,
				overrides: { web_search: true, image_generation: true }
			})
		).toEqual({ ...contextDefaults, web_search: true, image_generation: true });
	});

	it('disables unavailable capabilities without changing remembered preferences', () => {
		const preferences = { 'model-a': { web_search: true, image_generation: true } };

		expect(
			resolveToolFeatureState({
				modelId: 'model-a',
				preferences,
				defaults,
				available: { ...available, web_search: false }
			})
		).toEqual({ ...contextDefaults, web_search: false, image_generation: true });
		expect(preferences).toEqual({ 'model-a': { web_search: true, image_generation: true } });
	});
});

describe('context tool availability', () => {
	const models = [{ info: { meta: {} } }];
	const features = { enable_memories: true, enable_notes: true };
	const admin = { role: 'admin' };

	it('uses existing category and feature gates without a knowledge management permission', () => {
		expect(getContextToolAvailability(models, features, admin)).toEqual({
			knowledge: true,
			memory: true,
			notes: true
		});
		expect(getContextToolAvailability(models, features, { role: 'user' })).toEqual({
			knowledge: true,
			memory: false,
			notes: false
		});
		expect(getContextToolAvailability(models, {}, admin)).toEqual({
			knowledge: true,
			memory: false,
			notes: false
		});
	});

	it('requires a resolved model and intersects capabilities across selected models', () => {
		for (const selected of [[], [undefined], [...models, undefined]]) {
			expect(getContextToolAvailability(selected, features, admin)).toEqual({
				knowledge: false,
				memory: false,
				notes: false
			});
		}
		expect(
			getContextToolAvailability(
				[
					...models,
					{ info: { meta: { capabilities: { memory: false }, builtinTools: { notes: false } } } }
				],
				features,
				admin
			)
		).toEqual({ knowledge: true, memory: false, notes: false });
	});
});

describe('updateToolFeaturePreference', () => {
	it('immutably changes only one feature for one model', () => {
		const preferences = {
			'model-a': { web_search: true, image_generation: true },
			'model-b': { web_search: false }
		};
		const updated = updateToolFeaturePreference(preferences, 'model-a', 'web_search', false);

		expect(updated).toEqual({
			'model-a': { web_search: false, image_generation: true },
			'model-b': { web_search: false }
		});
		expect(updated).not.toBe(preferences);
		expect(updated['model-a']).not.toBe(preferences['model-a']);
		expect(updated['model-b']).toBe(preferences['model-b']);
	});

	it('creates missing maps and ignores null or empty model ids', () => {
		expect(updateToolFeaturePreference(null, 'model-a', 'web_search', false)).toEqual({
			'model-a': { web_search: false }
		});
		expect(updateToolFeaturePreference(undefined, null, 'web_search', true)).toEqual({});
		expect(updateToolFeaturePreference(undefined, '', 'web_search', true)).toEqual({});
	});
});

describe('createToolFeatureSaveQueue', () => {
	it('runs saves serially in enqueue order', async () => {
		const enqueue = createToolFeatureSaveQueue();
		const events: string[] = [];
		let releaseFirst!: () => void;
		const firstGate = new Promise<void>((resolve) => {
			releaseFirst = resolve;
		});

		const first = enqueue(
			async () => {
				events.push('first:start');
				await firstGate;
				events.push('first:end');
			},
			() => undefined
		);
		const second = enqueue(
			async () => {
				events.push('second');
			},
			() => undefined
		);

		await Promise.resolve();
		expect(events).toEqual(['first:start']);
		releaseFirst();
		await Promise.all([first, second]);
		expect(events).toEqual(['first:start', 'first:end', 'second']);
	});

	it('reports a failure and continues with later saves', async () => {
		const enqueue = createToolFeatureSaveQueue();
		const error = new Error('save failed');
		const errors: unknown[] = [];
		const events: string[] = [];

		await Promise.all([
			enqueue(
				async () => {
					throw error;
				},
				(caught) => errors.push(caught)
			),
			enqueue(
				async () => {
					events.push('recovered');
				},
				(caught) => errors.push(caught)
			)
		]);

		expect(errors).toEqual([error]);
		expect(events).toEqual(['recovered']);
	});

	it('captures each save callback when it is enqueued', async () => {
		const enqueue = createToolFeatureSaveQueue();
		const values: string[] = [];
		let save = async () => {
			values.push('first');
		};

		const first = enqueue(save, () => undefined);
		save = async () => {
			values.push('second');
		};
		const second = enqueue(save, () => undefined);

		await Promise.all([first, second]);
		expect(values).toEqual(['first', 'second']);
	});
});
