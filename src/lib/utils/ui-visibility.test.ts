import { describe, expect, it } from 'vitest';
import { CONTROL_LABELS, isControlVisible } from './ui-visibility';

describe('knowledge write display visibility', () => {
	it('registers the fork write category so builtin tool enumeration can retain it', () => {
		expect(Object.keys(CONTROL_LABELS)).toContain('model_editor.tools.knowledge_write');
	});
	it('defaults to visible and respects explicit non-admin hiding', () => {
		expect(isControlVisible('model_editor.tools.knowledge_write', { user: { role: 'user' } })).toBe(
			true
		);
		expect(
			isControlVisible('model_editor.tools.knowledge_write', {
				user: { role: 'user' },
				config: { ui: { visibility: { 'model_editor.tools.knowledge_write': false } } }
			})
		).toBe(false);
	});
});
