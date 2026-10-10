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
	tool_budget_enabled?: boolean;
	tool_result_limit?: number;
	tool_batch_limit?: number;
	tool_result_percent?: number;
	tool_batch_percent?: number;
	last_tool_batch?: {
		batch_id: string;
		original_tokens: number;
		processed_tokens: number;
		results: ToolResultStatus[];
	};
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

export interface ToolResultStatus {
	action?: 'tool_result_compaction';
	mode: 'v2';
	model: string;
	batch_id: string;
	tool: string;
	result_id?: string;
	method?: 'summary' | 'excerpt' | 'original' | 'read';
	original_tokens?: number;
	processed_tokens?: number;
	result_limit?: number;
	batch_limit?: number;
	allocated_tokens?: number;
	estimate_source?: string;
	branch_id?: string;
	source_message_id?: string;
	operation_id?: string;
	output_id?: string;
	call_id?: string;
	reused?: boolean;
	error?: boolean;
	done: boolean;
}

export const isValidToolBudgetPercent = (single: number, batch: number): boolean =>
	Number.isInteger(single) &&
	Number.isInteger(batch) &&
	single >= 1 &&
	single <= batch &&
	batch <= 100;

// Old chats retain their records; only positive provenance hides a misplaced one.
export const isVisibleToolResultStatus = (
	status: any,
	messageId: string,
	messages: Record<string, any> = {}
): boolean => {
	if (status?.source_message_id && status.source_message_id !== messageId) return false;
	if (!status?.source_message_id && (status?.output_id || status?.call_id)) {
		const owners = Object.entries(messages)
			.filter(([, message]) => {
				const output = Array.isArray(message?.output) ? message.output : [];
				const sidecars = message?.tool_results ?? message?.meta?.tool_results ?? [];
				const items = [
					...output.flatMap((item: any, index: number) =>
						['function_call_output', 'open_webui:code_interpreter'].includes(item?.type)
							? [{ ...item, id: item.id || `output-${index}` }]
							: []
					),
					...(Array.isArray(sidecars) ? sidecars : [])
				];
				return items.some(
					(item: any) =>
						(!status.output_id || item?.id === status.output_id) &&
						(!status.call_id || (item?.call_id || item?.id) === status.call_id)
				);
			})
			.map(([id]) => id);
		if (owners.length && !owners.includes(messageId)) return false;
	}
	if (status?.error) return true;
	if (status?.reused || ['original', 'read'].includes(status?.method)) return false;
	return ['summary', 'excerpt'].includes(status?.method) || (!status?.done && !status?.method);
};

export const mergeCompactionStatus = (history: any[], status: any): any[] => {
	const result = [...history];
	const index =
		status?.action === 'tool_result_compaction' && status?.operation_id
			? result.findIndex(
					(entry) =>
						entry?.action === 'tool_result_compaction' &&
						entry.operation_id === status.operation_id &&
						entry.source_message_id === status.source_message_id
				)
			: -1;
	if (index < 0) result.push(status);
	else if (!(result[index]?.done && !status?.done)) result[index] = { ...result[index], ...status };
	return result;
};
