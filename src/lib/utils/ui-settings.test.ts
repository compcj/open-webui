import { describe, expect, it } from 'vitest';
import { applyUiSettingsPatch, changedUiSettings } from './ui-settings';

describe('UI setting patches', () => {
	it('sends only changed top-level fields, preserving explicit null resets', () => {
		expect(
			changedUiSettings(
				{ theme: 'dark', params: { temperature: 1 }, memory: true },
				{ theme: 'dark', params: { temperature: 1 }, memory: null }
			)
		).toEqual({ memory: null });
	});
	it('omits undefined fields and replaces nested values without recursive merging', () => {
		expect(
			changedUiSettings({ params: { a: 1, b: 2 } }, { params: { a: 1 }, theme: undefined })
		).toEqual({ params: { a: 1 } });
		expect(
			applyUiSettingsPatch(
				{ theme: 'dark', params: { a: 1, b: 2 } },
				{ params: { a: 3 }, theme: null }
			)
		).toEqual({ params: { a: 3 } });
	});
});
