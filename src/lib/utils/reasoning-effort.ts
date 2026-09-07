export const STANDARD_REASONING_EFFORTS = [
	'none',
	'minimal',
	'low',
	'medium',
	'high',
	'xhigh'
] as const;

export function normalizeAvailableReasoningEffort(values: unknown): string[] {
	if (!Array.isArray(values)) {
		return [];
	}

	const seen = new Set<string>();
	const result: string[] = [];

	for (const value of values) {
		if (typeof value !== 'string') {
			continue;
		}

		const trimmed = value.trim();
		if (!trimmed || seen.has(trimmed)) {
			continue;
		}

		seen.add(trimmed);
		result.push(trimmed);
	}

	return result;
}

export function toggleAvailableReasoningEffort(current: unknown, value: string): string[] {
	const normalized = normalizeAvailableReasoningEffort(current);
	const trimmed = value.trim();
	if (!trimmed) {
		return normalized;
	}

	if (normalized.includes(trimmed)) {
		return normalized.filter((item) => item !== trimmed);
	}

	return [...normalized, trimmed];
}

export function resolveReasoningEffortOverride(
	override: string | undefined | null,
	available: unknown
): string | undefined {
	if (!override) {
		return undefined;
	}

	const list = normalizeAvailableReasoningEffort(available);
	return list.includes(override) ? override : undefined;
}

export function mergeRequestParamsWithReasoningEffort<T extends Record<string, unknown>>(
	params: T,
	override: string | undefined | null
): T {
	if (!override) {
		return { ...params };
	}

	return {
		...params,
		reasoning_effort: override
	};
}

export function getAvailableReasoningEffort(model: {
	info?: { meta?: { available_reasoning_effort?: unknown } };
} | null | undefined): string[] {
	return normalizeAvailableReasoningEffort(model?.info?.meta?.available_reasoning_effort);
}

export function sanitizeReasoningEffortByModel(
	byModel: Record<string, string> | null | undefined,
	availableByModel: Record<string, string[]>
): Record<string, string> {
	if (!byModel) {
		return {};
	}

	const result: Record<string, string> = {};
	for (const [modelId, value] of Object.entries(byModel)) {
		const resolved = resolveReasoningEffortOverride(value, availableByModel[modelId] ?? []);
		if (resolved) {
			result[modelId] = resolved;
		}
	}
	return result;
}

export function fillReasoningEffortFromLastUsed(
	chatMap: Record<string, string> | null | undefined,
	lastUsed: Record<string, string> | null | undefined,
	modelIds: string[],
	availableByModel: Record<string, string[]>
): Record<string, string> {
	const next = { ...(chatMap ?? {}) };

	for (const modelId of modelIds) {
		if (!modelId) {
			continue;
		}

		const available = availableByModel[modelId] ?? [];
		if (resolveReasoningEffortOverride(next[modelId], available)) {
			continue;
		}

		delete next[modelId];
		const filled = resolveReasoningEffortOverride(lastUsed?.[modelId], available);
		if (filled) {
			next[modelId] = filled;
		}
	}

	return next;
}
