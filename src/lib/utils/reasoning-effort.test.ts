import { describe, expect, it } from 'vitest';
import {
	STANDARD_REASONING_EFFORTS,
	getAvailableReasoningEffort,
	mergeRequestParamsWithReasoningEffort,
	normalizeAvailableReasoningEffort,
	resolveReasoningEffortOverride,
	sanitizeReasoningEffortByModel,
	toggleAvailableReasoningEffort
} from './reasoning-effort';

describe('normalizeAvailableReasoningEffort', () => {
	it('returns an empty list for missing or invalid values', () => {
		expect(normalizeAvailableReasoningEffort(undefined)).toEqual([]);
		expect(normalizeAvailableReasoningEffort(null)).toEqual([]);
		expect(normalizeAvailableReasoningEffort('medium')).toEqual([]);
		expect(normalizeAvailableReasoningEffort({})).toEqual([]);
	});

	it('trims, drops blanks, and dedupes while preserving order', () => {
		expect(normalizeAvailableReasoningEffort([' low ', 'medium', 'low', '', '  ', 'high'])).toEqual([
			'low',
			'medium',
			'high'
		]);
	});

	it('ignores non-string entries', () => {
		expect(normalizeAvailableReasoningEffort(['low', 1, null, { value: 'high' }, 'high'])).toEqual([
			'low',
			'high'
		]);
	});
});

describe('toggleAvailableReasoningEffort', () => {
	it('adds a new value', () => {
		expect(toggleAvailableReasoningEffort(['low'], 'high')).toEqual(['low', 'high']);
	});

	it('removes an existing value', () => {
		expect(toggleAvailableReasoningEffort(['low', 'high'], 'low')).toEqual(['high']);
	});

	it('ignores blank custom values', () => {
		expect(toggleAvailableReasoningEffort(['low'], '   ')).toEqual(['low']);
	});
});

describe('resolveReasoningEffortOverride', () => {
	it('returns undefined when there is no override or it is not in the available list', () => {
		expect(resolveReasoningEffortOverride(undefined, ['low', 'high'])).toBeUndefined();
		expect(resolveReasoningEffortOverride('', ['low', 'high'])).toBeUndefined();
		expect(resolveReasoningEffortOverride('medium', ['low', 'high'])).toBeUndefined();
		expect(resolveReasoningEffortOverride('high', [])).toBeUndefined();
	});

	it('returns the override when it is in the available list', () => {
		expect(resolveReasoningEffortOverride('high', ['low', 'high'])).toBe('high');
	});
});

describe('mergeRequestParamsWithReasoningEffort', () => {
	it('leaves params unchanged when there is no override', () => {
		const params = { temperature: 0.2, reasoning_effort: 'medium' };
		expect(mergeRequestParamsWithReasoningEffort(params, undefined)).toEqual(params);
		expect(mergeRequestParamsWithReasoningEffort(params, '')).toEqual(params);
	});

	it('applies the override last so it wins over settings and chat params', () => {
		expect(
			mergeRequestParamsWithReasoningEffort(
				{ temperature: 0.2, reasoning_effort: 'medium' },
				'high'
			)
		).toEqual({ temperature: 0.2, reasoning_effort: 'high' });
	});
});

describe('getAvailableReasoningEffort', () => {
	it('reads a non-empty list from model.info.meta', () => {
		expect(
			getAvailableReasoningEffort({
				info: { meta: { available_reasoning_effort: [' low ', 'high', 'high'] } }
			})
		).toEqual(['low', 'high']);
	});

	it('returns an empty list when the model has no available options', () => {
		expect(getAvailableReasoningEffort(undefined)).toEqual([]);
		expect(getAvailableReasoningEffort({ info: { meta: {} } })).toEqual([]);
	});
});

describe('sanitizeReasoningEffortByModel', () => {
	it('keeps only overrides that are still in each model available list', () => {
		expect(
			sanitizeReasoningEffortByModel(
				{ a: 'high', b: 'medium', c: 'low' },
				{ a: ['low', 'high'], b: ['low'] }
			)
		).toEqual({ a: 'high' });
	});

	it('returns an empty object for missing maps', () => {
		expect(sanitizeReasoningEffortByModel(undefined, {})).toEqual({});
	});
});

describe('STANDARD_REASONING_EFFORTS', () => {
	it('includes the common OpenAI reasoning effort values', () => {
		expect(STANDARD_REASONING_EFFORTS).toEqual(['none', 'minimal', 'low', 'medium', 'high', 'xhigh']);
	});
});
