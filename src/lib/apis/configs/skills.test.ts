import { afterEach, describe, expect, it, vi } from 'vitest';
import { getSkillsConfig, setSkillsConfig } from './index';
afterEach(() => {
	vi.unstubAllGlobals();
});
describe('admin skill discovery config API', () => {
	it('reads the allowlist with authenticated GET', async () => {
		const fetcher = vi.fn(
			async () => new Response(JSON.stringify({ AUTO_DISCOVERY_SKILL_IDS: [] }))
		);
		vi.stubGlobal('fetch', fetcher);
		expect(await getSkillsConfig('synthetic-token')).toEqual({ AUTO_DISCOVERY_SKILL_IDS: [] });
		expect(fetcher).toHaveBeenCalledWith(
			expect.stringContaining('/configs/skills'),
			expect.objectContaining({
				method: 'GET',
				headers: expect.objectContaining({ Authorization: 'Bearer synthetic-token' })
			})
		);
	});
	it('writes only the selected IDs with POST', async () => {
		const fetcher = vi.fn(
			async () => new Response(JSON.stringify({ AUTO_DISCOVERY_SKILL_IDS: ['writer'] }))
		);
		vi.stubGlobal('fetch', fetcher);
		await setSkillsConfig('synthetic-token', { AUTO_DISCOVERY_SKILL_IDS: ['writer'] });
		expect(fetcher).toHaveBeenCalledWith(
			expect.stringContaining('/configs/skills'),
			expect.objectContaining({
				method: 'POST',
				body: JSON.stringify({ AUTO_DISCOVERY_SKILL_IDS: ['writer'] })
			})
		);
	});
	it('surfaces permission failures', async () => {
		vi.stubGlobal(
			'fetch',
			async () => new Response(JSON.stringify({ detail: 'Admin required' }), { status: 403 })
		);
		await expect(getSkillsConfig('synthetic-token')).rejects.toEqual('Admin required');
	});
});
