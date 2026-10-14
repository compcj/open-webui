export type ToolSchemaCompatibility = 'default' | 'gemini';

export const normalizeDirectToolSchemaConfig = (
	value: unknown
): Record<string, ToolSchemaCompatibility> =>
	value && typeof value === 'object' && !Array.isArray(value)
		? Object.fromEntries(
				Object.entries(value).filter(
					([id, mode]) => id.trim() && (mode === 'default' || mode === 'gemini')
				)
			)
		: {};

interface ModelInfo {
	meta?: { tool_schema_compatibility?: unknown };
	base_model_id?: string | null;
}

export interface ToolSchemaModel extends ModelInfo {
	id: string;
	direct?: boolean;
	info?: ModelInfo;
}

export const resolveToolSchemaCompatibility = (
	modelId: string | null,
	models: ToolSchemaModel[]
): { value: ToolSchemaCompatibility; source: string | null } => {
	const seen = new Set<string>();
	while (modelId && !seen.has(modelId)) {
		seen.add(modelId);
		const model = models.find((entry) => entry.id === modelId);
		const info = model?.info ?? model;
		const mode = info?.meta?.tool_schema_compatibility;
		if (mode != null) {
			return { value: mode === 'gemini' ? 'gemini' : 'default', source: modelId };
		}
		if (model?.direct) break;
		modelId = info?.base_model_id ?? null;
	}
	return { value: 'default', source: null };
};
