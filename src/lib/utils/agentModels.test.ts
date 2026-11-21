import { describe, expect, it } from 'vitest';
import { resolveAgentModelId } from './agentModels';

describe('Agent model migration', () => {
	it('keeps offline and ambiguous IDs unchanged', () => {
		const old = 'agent:terminal:codex:model.v1';
		const model = { id: 'agent:terminal:codex:model-v1-a123', legacy_id: old };
		expect(resolveAgentModelId(old, [model])).toBe(model.id);
		expect(resolveAgentModelId(old, [])).toBe(old);
		expect(resolveAgentModelId(old, [model, { id: old }])).toBe(old);
	});
});
