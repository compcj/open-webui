export type ContextToolFeature = 'knowledge' | 'memory' | 'notes';

export type ContextToolState = Record<ContextToolFeature, boolean>;

export type ToolFeature = 'web_search' | 'image_generation' | ContextToolFeature;

export type ToolFeaturePreferences = Partial<Record<ToolFeature, boolean>>;

export type ToolFeaturesByModel = Record<string, ToolFeaturePreferences>;

const TOOL_FEATURES: ToolFeature[] = [
	'web_search',
	'image_generation',
	'knowledge',
	'memory',
	'notes'
];

type ContextToolModel = {
	info?: {
		meta?: {
			capabilities?: { builtin_tools?: boolean; memory?: boolean };
			builtinTools?: Partial<ContextToolState>;
		};
	};
};

// Shared by the request builder and composer so hidden tools cannot remain active.
// The personal memory master switch is applied separately, preserving the selection.
export function getContextToolAvailability(
	models: (ContextToolModel | null | undefined)[],
	features?: { enable_memories?: boolean; enable_notes?: boolean },
	user?: {
		role?: string;
		permissions?: { features?: { memories?: boolean; notes?: boolean } };
	} | null
): ContextToolState {
	const modelAllows = (feature: ContextToolFeature) =>
		models.length > 0 &&
		models.every(
			(model) =>
				model &&
				model.info?.meta?.capabilities?.builtin_tools !== false &&
				model.info?.meta?.builtinTools?.[feature] !== false &&
				(feature !== 'memory' || model.info?.meta?.capabilities?.memory !== false)
		);
	return {
		knowledge: modelAllows('knowledge'),
		memory: Boolean(
			modelAllows('memory') &&
			features?.enable_memories &&
			(user?.role === 'admin' || user?.permissions?.features?.memories)
		),
		notes: Boolean(
			modelAllows('notes') &&
			features?.enable_notes &&
			(user?.role === 'admin' || user?.permissions?.features?.notes)
		)
	};
}

export function getToolFeatureModelId(
	modelIds: string[],
	atModelId?: string | null
): string | null {
	if (atModelId) {
		return atModelId;
	}

	return modelIds.length === 1 && modelIds[0] ? modelIds[0] : null;
}

export function resolveToolFeatureState(options: {
	modelId: string | null;
	preferences?: ToolFeaturesByModel | null;
	defaults: Record<ToolFeature, boolean>;
	available: Record<ToolFeature, boolean>;
	overrides?: ToolFeaturePreferences | null;
}): Record<ToolFeature, boolean> {
	const state = {} as Record<ToolFeature, boolean>;
	const preference = options.modelId ? options.preferences?.[options.modelId] : undefined;

	for (const feature of TOOL_FEATURES) {
		const override = options.overrides?.[feature];
		const remembered = preference?.[feature];
		const enabled =
			typeof override === 'boolean'
				? override
				: typeof remembered === 'boolean'
					? remembered
					: options.defaults[feature];
		state[feature] = options.available[feature] && enabled;
	}

	return state;
}

export function updateToolFeaturePreference(
	preferences: ToolFeaturesByModel | null | undefined,
	modelId: string | null,
	feature: ToolFeature,
	enabled: boolean
): ToolFeaturesByModel {
	if (!modelId) {
		return preferences ?? {};
	}

	return {
		...(preferences ?? {}),
		[modelId]: {
			...(preferences?.[modelId] ?? {}),
			[feature]: enabled
		}
	};
}

export function createToolFeatureSaveQueue(): (
	save: () => Promise<unknown>,
	onError: (error: unknown) => void
) => Promise<void> {
	let tail = Promise.resolve();

	return (save, onError) => {
		const queued = tail.then(async () => {
			try {
				await save();
			} catch (error) {
				onError(error);
			}
		});
		tail = queued.catch(() => undefined);
		return queued;
	};
}

// Chat controls all replace the full UI settings object, so they share one queue.
export const enqueueChatSettingsSave = createToolFeatureSaveQueue();
