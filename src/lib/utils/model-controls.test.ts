import { describe, expect, it } from 'vitest';
import type { ModelParams } from '$lib/apis';
import {
	migrateControlSelections,
	resolveControlSelections,
	effectiveControlLabel,
	supportsModelControls,
	mergeAdvancedParams,
	migrateModelConfiguration
} from './model-controls';
const model = {
	id: 'm',
	info: {
		params: {
			model_controls: {
				reasoning: {
					label: 'Reasoning',
					default: 'custom',
					options: {
						low: { label: 'Low', params: { reasoning_effort: 'low' } },
						custom: { label: 'Custom', params: { reasoning_effort: 'very high/custom' } }
					}
				}
			}
		}
	}
};
describe('model controls compatibility', () => {
	it('maps legacy custom efforts to approved IDs', () => {
		expect(migrateControlSelections(undefined, { m: 'very high/custom' }, [model])).toEqual({
			m: { reasoning: 'custom' }
		});
		expect(migrateControlSelections(undefined, { m: 'removed' }, [model])).toEqual({});
	});
	it('new maps are authoritative including reset and empty maps', () => {
		expect(migrateControlSelections({}, { m: 'low' }, [model])).toEqual({});
		expect(migrateControlSelections({ m: {} }, { m: 'low' }, [model])).toEqual({ m: {} });
		expect(resolveControlSelections({ m: {} }, { m: { reasoning: 'low' } })).toEqual({ m: {} });
	});
	it('chat selections take priority over remembered controls', () => {
		expect(
			resolveControlSelections({ m: { reasoning: 'custom' } }, { m: { reasoning: 'low' } })
		).toEqual({ m: { reasoning: 'custom' } });
	});
	it('default label reflects advanced effort and then control default', () => {
		const control = model.info.params.model_controls.reasoning;
		expect(effectiveControlLabel(control, { reasoning_effort: 'low' }, 'Default')).toBe('Low');
		expect(
			effectiveControlLabel(control, { custom_params: { reasoning_effort: 'medium' } }, 'Default')
		).toBe('medium');
		expect(effectiveControlLabel(control, {}, 'Default')).toBe('Custom');
	});
	it('keeps legacy direct and pipe behavior', () => {
		expect(supportsModelControls(model)).toBe(true);
		expect(supportsModelControls({ ...model, direct: true })).toBe(false);
		expect(supportsModelControls({ ...model, pipe: {} })).toBe(false);
	});
});

it('merges chat advanced values ahead of personal custom values', () => {
	expect(
		mergeAdvancedParams(
			{ custom_params: { reasoning_effort: 'low', other: 1 } },
			{ reasoning_effort: 'high' }
		).custom_params
	).toEqual({ reasoning_effort: 'high', other: 1 });
	expect(
		mergeAdvancedParams(
			{ reasoning_effort: 'low' },
			{ custom_params: { reasoning_effort: 'high' } }
		).custom_params
	).toEqual({ reasoning_effort: 'high' });
	expect(
		mergeAdvancedParams({ reasoning_effort: 'low' }, { reasoning_effort: null }).reasoning_effort
	).toBe('low');
});

it('migrates model imports before editing and preserves authoritative new resets', () => {
	const old = {
		params: { reasoning_effort: 'custom effort' } as ModelParams,
		meta: { available_reasoning_effort: ['low', 'custom effort'], context_window: 100 }
	};
	expect(migrateModelConfiguration(old).params.model_controls?.reasoning.default).toBe('option2');
	expect(migrateModelConfiguration(old).meta.available_reasoning_effort).toBeUndefined();
	expect(
		migrateModelConfiguration({ ...old, params: { model_controls: {} } }).params.model_controls
	).toEqual({});
	expect(old.meta.available_reasoning_effort).toEqual(['low', 'custom effort']);
});
it('shows the inherited model/global reasoning default when the control has no default', () => {
	const control = { ...model.info.params.model_controls.reasoning, default: undefined };
	expect(effectiveControlLabel(control, {}, 'Default', 'low')).toBe('Low');
});

it('remembers unselected controls for a model while chat choices override matching controls', () => {
	expect(
		resolveControlSelections(
			{ m: { reasoning: 'high' } },
			{ m: { reasoning: 'low', tone: 'brief' } }
		)
	).toEqual({ m: { reasoning: 'high', tone: 'brief' } });
});
