import { IMAGES_API_BASE_URL } from '$lib/constants';

export type ImageGenerationEngineOption = { id: string; name: string };

export type ImageEngineConfig<Engine extends string = 'openai' | 'gemini' | 'grok' | 'comfyui'> = {
	engine: Engine;
	model: string;
	tool_description_suffix: string;
	base_url: string;
	api_key: string;
	api_version: string;
	params: Record<string, unknown>;
	size: string;
	steps: number | null;
	gemini_endpoint_method: 'predict' | 'generateContent';
	comfyui_workflow: string;
	comfyui_workflow_nodes: { type: string; key: string; node_ids: string[] }[];
};

export type ImageEditEngineConfig = ImageEngineConfig<ImageEngineConfig['engine'] | 'disabled'>;

export type ImageGenerationEngine = ImageGenerationEngineOption &
	ImageEngineConfig & { edit?: ImageEditEngineConfig | null };

export const getImageGenerationEngines = async (
	token: string = ''
): Promise<ImageGenerationEngineOption[]> => {
	const res = await fetch(`${IMAGES_API_BASE_URL}/engines`, {
		headers: {
			Accept: 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	});
	if (!res.ok) {
		const error = await res.json().catch(() => null);
		throw error?.detail ?? 'Failed to load image generation engines';
	}
	return res.json();
};

export const getConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateConfig = async (token: string = '', config: object) => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			if ('detail' in err) {
				error = Array.isArray(err.detail)
					? err.detail
							.map((item: { msg?: string }) => item.msg ?? 'Invalid configuration')
							.join('\n')
					: err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const verifyConfigUrl = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/config/url/verify`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getImageGenerationConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/image/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateImageGenerationConfig = async (token: string = '', config: object) => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/image/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({ ...config })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getImageGenerationModels = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/models`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const imageGenerations = async (token: string = '', prompt: string) => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/generations`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			prompt: prompt
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				if (Array.isArray(err.detail)) {
					error = err.detail.map((e: { msg?: string }) => e.msg || JSON.stringify(e)).join(', ');
				} else {
					error = err.detail;
				}
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const imageEdits = async (
	token: string = '',
	images: string | string[],
	prompt: string,
	model?: string,
	size?: string,
	n?: number,
	background?: string
) => {
	let error = null;

	const res = await fetch(`${IMAGES_API_BASE_URL}/edit`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			form_data: {
				image: images,
				prompt,
				...(model && { model }),
				...(size && { size }),
				...(n && { n }),
				...(background && { background })
			}
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				if (Array.isArray(err.detail)) {
					error = err.detail.map((e: { msg?: string }) => e.msg || JSON.stringify(e)).join(', ');
				} else {
					error = err.detail;
				}
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
