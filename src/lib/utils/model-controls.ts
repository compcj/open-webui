import type { ModelControl } from '$lib/apis';

export type ControlSelections = Record<string, Record<string, string>>;

export function supportsModelControls(model: any): boolean {
	return (
		!!model &&
		!model.direct &&
		!model.pipe &&
		Object.keys(model.info?.params?.model_controls ?? {}).length > 0
	);
}

/** Presence, including {}, marks the new format as authoritative. */
export function migrateControlSelections(
	current: ControlSelections | undefined | null,
	legacy: Record<string, string> | undefined | null,
	models: any[]
): ControlSelections {
	if (current != null) return structuredClone(current);
	const result: ControlSelections = {};
	for (const model of models) {
		if (!supportsModelControls(model)) continue;
		const effort = legacy?.[model.id];
		if (!effort) continue;
		for (const [key, control] of Object.entries(model.info.params.model_controls) as [
			string,
			ModelControl
		][]) {
			const option = Object.entries(control.options).find(
				([, option]) => option.params?.reasoning_effort === effort
			);
			if (option) result[model.id] = { ...result[model.id], [key]: option[0] };
		}
	}
	return result;
}

export function resolveControlSelections(
	chat: ControlSelections,
	remembered: ControlSelections
): ControlSelections {
	const result = structuredClone(remembered);
	for (const [modelId, choices] of Object.entries(chat)) {
		result[modelId] = Object.keys(choices).length ? { ...result[modelId], ...choices } : {};
	}
	return result;
}

/** Only public scalar reasoning parameters are available to describe effective defaults. */
export function effectiveControlLabel(
	control: ModelControl,
	advanced: Record<string, any>,
	fallback: string,
	modelDefault?: string
): string {
	const effort = advanced?.custom_params?.reasoning_effort ?? advanced?.reasoning_effort;
	if (typeof effort === 'string' && effort.trim()) {
		const matching = Object.values(control.options).find(
			(option) => option.params?.reasoning_effort === effort
		);
		if (
			Object.values(control.options).some(
				(option) => typeof option.params?.reasoning_effort === 'string'
			)
		)
			return matching?.label ?? effort;
	}
	const defaultOption = control.options[control.default ?? ''];
	if (defaultOption) return defaultOption.label;
	if (
		modelDefault &&
		Object.values(control.options).some(
			(option) => typeof option.params?.reasoning_effort === 'string'
		)
	)
		return (
			Object.values(control.options).find(
				(option) => option.params?.reasoning_effort === modelDefault
			)?.label ?? modelDefault
		);
	return fallback;
}

export function controlsForRequest(
	chat: Record<string, any>,
	personal: Record<string, any>,
	models: any[],
	legacyChat?: Record<string, string>,
	legacyPersonal?: Record<string, string>
): ControlSelections {
	return resolveControlSelections(
		migrateControlSelections(chat.model_controls, legacyChat, models),
		migrateControlSelections(personal.model_controls, legacyPersonal, models)
	);
}

/** Chat advanced parameters override personal values even across custom/ordinary fields. */
export function mergeAdvancedParams(
	personal: Record<string, any>,
	chat: Record<string, any>
): Record<string, any> {
	const nonNull = (values: Record<string, any>) =>
		Object.fromEntries(Object.entries(values).filter(([, value]) => value != null));
	const result = { ...nonNull(personal), ...nonNull(chat) };
	const custom = { ...nonNull(personal.custom_params ?? {}) };
	for (const [key, value] of Object.entries(nonNull(chat))) {
		if (key !== 'custom_params' && Object.hasOwn(custom, key)) custom[key] = value;
	}
	Object.assign(custom, nonNull(chat.custom_params ?? {}));
	if (Object.keys(custom).length) result.custom_params = custom;
	return result;
}

/** Private saved chat preferences belong only to the owner. Collaborators use their own settings. */
export function actorChatPreferences(chat: any, actorId: string | undefined) {
	const owned = !!actorId && chat?.user_id === actorId;
	return {
		params: owned ? structuredClone(chat?.chat?.params ?? {}) : {},
		variables: owned ? structuredClone(chat?.variables ?? {}) : {},
		legacyReasoning: owned ? structuredClone(chat?.chat?.reasoning_effort_by_model ?? {}) : {}
	};
}

/** Adapt imported/exported legacy model forms before the editor changes their metadata. */
export function migrateModelConfiguration<T extends Record<string, any>>(model: T): T {
	const result = structuredClone(model);
	const params = result.params ?? {};
	const meta = result.meta ?? {};
	const values = Array.isArray(meta.available_reasoning_effort)
		? ([
				...new Set(
					meta.available_reasoning_effort
						.filter(
							(value: unknown): value is string => typeof value === 'string' && !!value.trim()
						)
						.map((value: string) => value.trim())
				)
			] as string[])
		: [];
	if (!Object.hasOwn(params, 'model_controls') && values.length) {
		const defaultEffort =
			params.custom_params?.reasoning_effort ??
			params.reasoning_effort ??
			meta.default_reasoning_effort;
		if (
			typeof defaultEffort === 'string' &&
			defaultEffort.trim() &&
			!values.includes(defaultEffort.trim())
		)
			values.push(defaultEffort.trim());
		const options = Object.fromEntries(
			values.map((value, index) => [
				`option${index + 1}`,
				{ label: value, params: { reasoning_effort: value } }
			])
		);
		params.model_controls = {
			reasoning: {
				label: 'Reasoning effort',
				options,
				...(typeof defaultEffort === 'string' && values.includes(defaultEffort.trim())
					? { default: `option${values.indexOf(defaultEffort.trim()) + 1}` }
					: {})
			}
		};
	}
	delete meta.available_reasoning_effort;
	delete meta.default_reasoning_effort;
	return { ...result, params, meta };
}
