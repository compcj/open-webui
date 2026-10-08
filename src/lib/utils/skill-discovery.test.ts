import { describe, expect, it, vi } from 'vitest';
import {
	createSkillDiscoveryLoader,
	terminalDiscoveryConfig,
	filterDiscoverySkills
} from './skill-discovery';

describe('skill discovery boundaries', () => {
	it('does not publish a response from a previously selected terminal', async () => {
		let context = 'terminal-a';
		let release!: (value: string[]) => void;
		const publish = vi.fn();
		const loader = createSkillDiscoveryLoader(() => context, publish);
		const pending = loader(
			() =>
				new Promise<string[]>((resolve) => {
					release = resolve;
				})
		);
		context = 'terminal-b';
		release(['terminal:a']);
		expect(await pending).toEqual([]);
		expect(publish).not.toHaveBeenCalled();
	});
	it('ignores an earlier response when a newer discovery request wins', async () => {
		let release!: (value: string[]) => void;
		const publish = vi.fn();
		const loader = createSkillDiscoveryLoader(() => 'terminal-a', publish);
		const pending = loader(
			() =>
				new Promise<string[]>((resolve) => {
					release = resolve;
				})
		);
		expect(await loader(async () => ['terminal:new'])).toEqual(['terminal:new']);
		release(['terminal:old']);
		expect(await pending).toEqual([]);
		expect(publish).toHaveBeenCalledTimes(1);
		expect(publish).toHaveBeenCalledWith(['terminal:new']);
	});
	it('keeps admin terminal policy explicit and prevents personal connections granting it', () => {
		const original = { other: 'kept', auto_discover_skills: true };
		expect(terminalDiscoveryConfig(original, false, false)).toEqual({
			other: 'kept',
			auto_discover_skills: false
		});
		expect(terminalDiscoveryConfig(original, true, true)).toEqual({ other: 'kept' });
		expect(original.auto_discover_skills).toBe(true);
	});
	it('searches workspace names and descriptions and never includes terminal IDs', () => {
		const items = [
			{ id: 'one', name: 'Writer', description: 'Draft notes' },
			{ id: 'terminal:x', name: 'Writer' }
		];
		expect(filterDiscoverySkills(items, ' notes ')).toEqual([items[0]]);
		expect(filterDiscoverySkills(items, '')).toEqual([items[0]]);
	});
});
