export type CitationSource = {
	source?: { id?: string; name?: string; [key: string]: any };
	document?: string[];
	metadata?: { source?: string; name?: string; [key: string]: any }[];
	distances?: number[];
};

export type Citation = {
	id: string;
	source: NonNullable<CitationSource['source']> & { name: string };
	document: string[];
	metadata: NonNullable<CitationSource['metadata']>;
	distances: number[];
};

// Keep the card list and inline label list in the same first-seen source order.
export const getCitations = (sources: CitationSource[] | null | undefined): Citation[] => {
	const citations = new Map<string, Citation>();
	for (const source of sources ?? []) {
		for (const [index, document] of (source.document ?? []).entries()) {
			const metadata = source.metadata?.[index] ?? {};
			const id = metadata.source || source.source?.id || 'N/A';
			const distance = source.distances?.[index];
			const existing = citations.get(id);
			if (existing) {
				existing.document.push(document);
				existing.metadata.push(metadata);
				if (distance !== undefined) existing.distances.push(distance);
				continue;
			}

			let sourceInfo: Citation['source'] = { ...source.source, name: source.source?.name || id };
			if (metadata.name) sourceInfo = { ...sourceInfo, name: metadata.name };
			if (id.startsWith('http://') || id.startsWith('https://')) {
				sourceInfo = { ...sourceInfo, name: id, url: id };
			}
			citations.set(id, {
				id,
				source: sourceInfo,
				document: [document],
				metadata: [metadata],
				distances: distance !== undefined ? [distance] : []
			});
		}
	}
	return [...citations.values()];
};

export const getCitationSourceTitles = (
	sources: CitationSource[] | null | undefined,
	citationsEnabled = true
): string[] => {
	if (!citationsEnabled) return [];
	return getCitations(sources).map(
		(citation) => citation.metadata[0]?.name || citation.source.name || citation.id
	);
};
