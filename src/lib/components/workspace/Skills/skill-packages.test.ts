import { afterEach, describe, expect, it, vi } from 'vitest';
import { exportSkillBundle, importSkillBundles } from '$lib/apis/skills';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('ZIP-compatible .skill package API', () => {
	it.each([undefined, 'historical-version'])(
		'exports a single .skill archive with selected version %s',
		async (version) => {
			const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
			const fetchMock = vi
				.fn()
				.mockResolvedValue(new Response(bytes, { headers: { 'content-type': 'application/zip' } }));
			vi.stubGlobal('fetch', fetchMock);
			const blob = await exportSkillBundle('token', 'skill', ['skill-a'], version);
			const [url, options] = fetchMock.mock.calls[0];
			const params = new URL(url, 'http://localhost').searchParams;
			expect(params.get('format')).toBe('skill');
			expect(params.getAll('ids')).toEqual(['skill-a']);
			expect(params.get('version_id')).toBe(version ?? null);
			expect(new Headers(options.headers).get('authorization')).toBe('Bearer token');
			expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
		}
	);
	it('preserves multi-package filenames and optimistic versions through preview and import', async () => {
		const fetchMock = vi.fn().mockImplementation(async () => Response.json([]));
		vi.stubGlobal('fetch', fetchMock);
		const files = [new File(['PK'], 'first.skill'), new File(['PK'], 'second.zip')];
		const decisions = [
			{ action: 'replace', id: 'skill-a', expected_version_id: 'v1' },
			{ action: 'create', id: 'skill-b' }
		];
		await importSkillBundles('token', files);
		await importSkillBundles('token', files, decisions);
		expect(fetchMock.mock.calls[0][0]).toMatch(/\/skills\/import\/preview$/);
		expect(fetchMock.mock.calls[1][0]).toMatch(/\/skills\/import$/);
		for (const [, options] of fetchMock.mock.calls)
			expect(options.body.getAll('files').map((file: File) => file.name)).toEqual([
				'first.skill',
				'second.zip'
			]);
		expect(JSON.parse(fetchMock.mock.calls[1][1].body.get('decisions'))).toEqual(decisions);
	});
});
