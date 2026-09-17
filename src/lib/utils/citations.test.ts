import { describe, expect, it } from 'vitest';
import { getCitations, getCitationSourceTitles } from './citations';

const search = (...urls: string[]) => ({
	source: { id: 'search_web', name: 'search_web' },
	document: urls.map((url) => `Snippet for ${url}`),
	metadata: urls.map((url) => ({ source: url, name: 'Same title', url }))
});

describe('citation numbering', () => {
	it('keeps different URLs with the same title as separate numbered sources', () => {
		const sources = [search('https://example.test/one', 'https://example.test/two')];
		expect(getCitationSourceTitles(sources)).toEqual(['Same title', 'Same title']);
		expect(getCitations(sources).map((citation) => citation.source.url)).toEqual([
			'https://example.test/one',
			'https://example.test/two'
		]);
	});

	it('reuses a searched URL after fetch and keeps subsequent card indexes aligned', () => {
		const sources = [
			search('https://example.test/one', 'https://example.test/two'),
			{
				source: { id: 'https://example.test/one', name: 'https://example.test/one' },
				document: ['Fetched preview'],
				metadata: [{ source: 'https://example.test/one', name: 'https://example.test/one' }]
			},
			search('https://example.test/three')
		];
		const citations = getCitations(sources);
		expect(getCitationSourceTitles(sources)).toEqual(['Same title', 'Same title', 'Same title']);
		expect(citations.map((citation) => citation.id)).toEqual([
			'https://example.test/one',
			'https://example.test/two',
			'https://example.test/three'
		]);
		expect(citations[0].document).toEqual([
			'Snippet for https://example.test/one',
			'Fetched preview'
		]);
		expect(citations[2].source.url).toBe('https://example.test/three');
	});

	it('uses the backend identity fallback and insertion order for files mixed with web sources', () => {
		const sources = [
			{
				source: { id: 'file-1', name: 'manual.txt', type: 'file' },
				document: ['File evidence'],
				metadata: [{ source: '', name: 'manual.txt' }],
				distances: [0]
			},
			search('https://example.test/one')
		];
		expect(getCitations(sources).map((citation) => citation.id)).toEqual([
			'file-1',
			'https://example.test/one'
		]);
		expect(getCitationSourceTitles(sources)).toEqual(['manual.txt', 'Same title']);
		expect(getCitations(sources)[0].distances).toEqual([0]);
	});

	it('preserves input sources and metadata while grouping previews', () => {
		const sources = [search('https://example.test/one'), search('https://example.test/one')];
		const original = structuredClone(sources);
		expect(getCitations(sources)[0].metadata).toHaveLength(2);
		expect(sources).toEqual(original);
	});

	it('uses the source ID as the card title when no display name is provided', () => {
		const sources = [{ source: { id: 'file-1' }, document: ['Evidence'], metadata: [{}] }];
		expect(getCitations(sources)[0].source.name).toBe('file-1');
		expect(getCitationSourceTitles(sources)).toEqual(['file-1']);
	});

	it('does not provide numbered citation labels when citations are disabled', () => {
		expect(getCitationSourceTitles([search('https://example.test/one')], false)).toEqual([]);
	});

	it('accepts absent and empty sources', () => {
		expect(getCitations(null)).toEqual([]);
		expect(getCitations([{}])).toEqual([]);
		expect(getCitationSourceTitles(undefined)).toEqual([]);
	});
});
