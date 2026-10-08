import equal from 'fast-deep-equal';

/** UI API patches replace top-level values; omitted keys preserve, null resets. */
export const changedUiSettings = (
	current: Record<string, any>,
	updated: Record<string, any>
): Record<string, any> =>
	Object.fromEntries(
		Object.entries(updated).filter(
			([key, value]) => value !== undefined && (value === null || !equal(current[key], value))
		)
	);

export const applyUiSettingsPatch = (
	current: Record<string, any>,
	patch: Record<string, any>
): Record<string, any> => {
	const result = { ...current };
	for (const [key, value] of Object.entries(patch)) {
		if (value === null) delete result[key];
		else if (value !== undefined) result[key] = value;
	}
	return result;
};
