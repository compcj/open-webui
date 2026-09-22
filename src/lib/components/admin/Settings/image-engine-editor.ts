import type {
	ImageEditEngineConfig,
	ImageEngineConfig,
	ImageGenerationEngine
} from '$lib/apis/images';

export function createImageEngineConfig(): ImageEngineConfig {
	return {
		engine: 'openai',
		model: '',
		tool_description_suffix: '',
		base_url: '',
		api_key: '',
		api_version: '',
		params: {},
		size: '',
		steps: null,
		gemini_endpoint_method: 'generateContent',
		comfyui_workflow: '',
		comfyui_workflow_nodes: []
	};
}

export function prepareImageEngineProfiles(
	engines: ImageGenerationEngine[],
	drafts: Record<string, { generation: string; edit: string }>
): ImageGenerationEngine[] {
	const ids = new Set<string>();
	const names = new Set<string>();

	return engines.map((profile) => {
		const id = profile.id.trim();
		const name = profile.name.trim();
		if (!name) {
			throw new Error('Engine alias is required.');
		}
		if (names.has(name.toLocaleLowerCase())) {
			throw new Error('Engine aliases must be unique.');
		}
		if (!id || id.toLocaleLowerCase() === 'default' || ids.has(id.toLocaleLowerCase())) {
			throw new Error('Additional engine IDs must be unique and cannot be default.');
		}
		names.add(name.toLocaleLowerCase());
		ids.add(id.toLocaleLowerCase());

		const generation = prepareConfig(profile, drafts[id]?.generation, false);
		const edit =
			profile.edit?.engine === 'disabled'
				? { ...createImageEngineConfig(), engine: 'disabled' as const }
				: profile.edit
					? prepareConfig(profile.edit, drafts[id]?.edit, true)
					: null;
		return { id, name, ...generation, edit };
	});
}

function parseObject(value: string, errorKey: string): Record<string, unknown> {
	try {
		const parsed = JSON.parse(value.trim() || '{}');
		if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
			return parsed;
		}
	} catch (error) {
		// Use a stable message key for the caller's localized toast.
	}
	throw new Error(errorKey);
}

function prepareConfig<Engine extends ImageEditEngineConfig['engine']>(
	config: ImageEngineConfig<Engine>,
	paramsDraft: string | undefined,
	isEdit: boolean
): ImageEngineConfig<Engine> {
	const params = parseObject(
		paramsDraft ?? JSON.stringify(config.params ?? {}),
		isEdit
			? 'Edit additional parameters must be a valid JSON object.'
			: 'Additional parameters must be a valid JSON object.'
	);
	if (config.comfyui_workflow?.trim()) {
		parseObject(
			config.comfyui_workflow,
			isEdit
				? 'Edit ComfyUI workflow must be a valid JSON object.'
				: 'ComfyUI workflow must be a valid JSON object.'
		);
	}

	return {
		engine: config.engine,
		model: config.model ?? '',
		tool_description_suffix: (config.tool_description_suffix ?? '').trim(),
		base_url: config.base_url ?? '',
		api_key: config.api_key ?? '',
		api_version: config.api_version ?? '',
		params,
		size: config.size ?? '',
		steps: config.steps == null ? null : Number(config.steps),
		gemini_endpoint_method: isEdit ? 'generateContent' : config.gemini_endpoint_method,
		comfyui_workflow: config.comfyui_workflow ?? '',
		comfyui_workflow_nodes: (config.comfyui_workflow_nodes ?? []).map((node) => ({
			type: node.type.trim(),
			key: node.key.trim(),
			node_ids: node.node_ids.map((id) => id.trim()).filter(Boolean)
		}))
	};
}
