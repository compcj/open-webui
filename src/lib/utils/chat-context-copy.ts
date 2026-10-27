// Only these known metadata envelopes are projected. Tool arguments, content,
// citation text and signed reasoning are opaque and must survive byte-for-byte.
const derived = new Set([
	'temporary_context',
	'context_scope',
	'contextSummary',
	'context_usage',
	'context_compaction',
	'chat_context_checkpoint',
	'context_checkpoint',
	'checkpoint_id',
	'selection_id',
	'selection_ids',
	'tool_result_selections',
	'tool_result_selection',
	'tool_result_accounting',
	'tool_budget_accounting',
	'_temporary_execution_id',
	'_context_original_key',
	'execution_ledger',
	'event_seq',
	'events_dropped_through',
	'raw_cursor',
	'cursor',
	'_tool_result_refs',
	'_context_message_id',
	'context_checkpoint_id',
	'tool_result_ledger'
]);
function clean(object: any) {
	if (!object || typeof object !== 'object' || Array.isArray(object)) return;
	for (const key of derived) delete object[key];
}
function message(object: any) {
	clean(object);
	clean(object?.meta);
	clean(object?.metadata);
	for (const item of object?.output ?? []) {
		clean(item);
		delete item.tool_result;
		clean(item?.meta);
		clean(item?.metadata);
	}
	if (Array.isArray(object?.statusHistory))
		object.statusHistory = object.statusHistory.filter(
			(entry: any) => !['context_compaction', 'tool_result_compaction'].includes(entry?.action)
		);
}
export function projectRawChat<T>(source: T): T {
	const copy = structuredClone(source) as any;
	const chat = copy?.chat ?? copy;
	for (const envelope of [copy, chat])
		if (typeof envelope?.id === 'string' && /^(temporary|local):/.test(envelope.id))
			delete envelope.id;
	clean(copy);
	clean(chat);
	clean(chat?.meta);
	clean(chat?.metadata);
	clean(chat?.history);
	for (const item of Object.values(chat?.history?.messages ?? {})) message(item);
	for (const item of chat?.messages ?? []) message(item);
	return copy;
}
