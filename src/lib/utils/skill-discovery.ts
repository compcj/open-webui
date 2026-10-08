/** Ignore responses when the request or terminal/account/chat context has changed. */
export const createSkillDiscoveryLoader = <T>(
	readContext: () => string,
	publish: (items: T[]) => void
) => {
	let revision = 0;
	return async (fetchItems: () => Promise<T[]>): Promise<T[]> => {
		const request = ++revision;
		const context = readContext();
		const items = await fetchItems();
		if (request !== revision || context !== readContext()) return [];
		publish(items);
		return items;
	};
};

export const terminalDiscoveryConfig = (
	config: Record<string, unknown>,
	direct: boolean,
	enabled: boolean
) => {
	const result = { ...config };
	if (direct) delete result.auto_discover_skills;
	else result.auto_discover_skills = enabled;
	return result;
};

export const filterDiscoverySkills = <
	T extends { id: string; name?: string; description?: string }
>(
	items: T[],
	query: string
): T[] => {
	const search = query.trim().toLowerCase();
	return items.filter(
		(skill) =>
			!skill.id.startsWith('terminal:') &&
			(!search ||
				`${skill.name ?? ''} ${skill.description ?? ''} ${skill.id}`.toLowerCase().includes(search))
	);
};
