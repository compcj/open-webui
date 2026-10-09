export type CompactionMode = 'off' | 'legacy' | 'v2';

export interface ContextUsage {
	tokens: number;
	estimated_tokens?: number;
	threshold?: number | null;
	percent?: number | null;
	source?: string;
	mode?: CompactionMode;
	model?: string;
	branch_id?: string;
	context_window?: number;
	effective_context_window?: number;
	input_budget?: number;
	remaining_tokens?: number;
	before_tokens?: number;
	after_tokens?: number;
}

export const getCompactionMode = (features: Record<string, unknown> = {}): CompactionMode => {
	const mode = features.context_compaction_mode;
	return mode === 'off' || mode === 'legacy' || mode === 'v2'
		? mode
		: features.enable_context_compaction
			? 'legacy'
			: 'off';
};

export const resolveCompactionWindow = (
	modelId: string | null,
	models: any[],
	fallback = 262144
): { value: number; source: string | null } => {
	const seen = new Set<string>();
	while (modelId && !seen.has(modelId)) {
		seen.add(modelId);
		const model = models.find((entry) => entry.id === modelId);
		const info = model?.info ?? model;
		const value = info?.meta?.context_window;
		if (Number.isSafeInteger(value) && value > 0) return { value, source: modelId };
		modelId = info?.base_model_id;
	}
	return { value: fallback, source: null };
};

export const selectContextUsage = (
	mode: CompactionMode,
	model: string,
	backend: any,
	legacy: any,
	branchId?: string | null
) => {
	if (mode === 'off') return null;
	if (
		backend &&
		(!backend.mode || backend.mode === mode) &&
		(!backend.model || backend.model === model) &&
		(!branchId || backend.branch_id === branchId)
	) {
		return backend;
	}
	return mode === 'legacy' ? legacy : null;
};
