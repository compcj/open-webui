import type { ImageGenerationEngineOption } from '$lib/apis/images';

// null means the catalog is not available yet; preserve the preference until it is.
export const resolveImageGenerationEngineId = (
	preference: unknown,
	engines: ImageGenerationEngineOption[] | null
): string =>
	typeof preference === 'string' &&
	(engines === null || engines.some((engine) => engine.id === preference))
		? preference
		: '';
