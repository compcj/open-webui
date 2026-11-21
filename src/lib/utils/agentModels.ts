/** A backend-verified alias is valid only while one live model advertises it. */
export function resolveAgentModelId(
	id: string,
	items: readonly { id: string; legacy_id?: string | null }[]
): string {
	if (!id.startsWith('agent:')) return id;
	const matches = new Set(
		items.filter((item) => item.id === id || item.legacy_id === id).map((item) => item.id)
	);
	return matches.size === 1 ? [...matches][0] : id;
}
