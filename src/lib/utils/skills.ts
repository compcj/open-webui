import JSZip from 'jszip';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';

import { formatSkillName, slugify } from '$lib/utils';

export const OPENCLAW_MAX_FILES = 256;
export const OPENCLAW_MAX_FILE_SIZE = 1024 * 1024; // 1 MiB
export const OPENCLAW_MAX_TOTAL_SIZE = 8 * 1024 * 1024; // 8 MiB

export type ParsedSkillMarkdown = {
	frontmatter: Record<string, any>;
	body: string;
};

export type OpenclawSource = {
	type: 'file' | 'zip' | 'url' | 'clawhub' | string;
	url?: string;
	slug?: string;
	version?: string;
	[key: string]: any;
};

export type SkillFileEntry = {
	content: string;
	encoding: 'utf-8' | 'base64';
};

export type OpenclawMeta = {
	frontmatter?: Record<string, any>;
	files?: Record<string, SkillFileEntry>;
	source?: OpenclawSource;
	config?: Record<string, any>;
	env?: Record<string, string>;
	[key: string]: any;
};

export type SkillLike = {
	id?: string;
	name?: string;
	description?: string;
	content?: string;
	meta?: {
		tags?: any[];
		openclaw?: OpenclawMeta;
		[key: string]: any;
	};
	[key: string]: any;
};

export type SkillGating = {
	bins: string[];
	anyBins: string[];
	env: string[];
	config: string[];
	os: string[];
	always: boolean;
	emoji?: string;
	install: any[];
};

export const parseSkillMarkdown = (text: string): ParsedSkillMarkdown | null => {
	if (typeof text !== 'string') return null;

	const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---/);
	if (!match) return null;

	try {
		const frontmatter = parseYaml(match[1]);
		if (!frontmatter || typeof frontmatter !== 'object' || Array.isArray(frontmatter)) {
			return null;
		}

		const body = text.slice(match[0].length).replace(/^([ \t]*\r?\n)+/, '');
		return { frontmatter, body };
	} catch {
		return null;
	}
};

export const openclawToSkill = (
	parsed: ParsedSkillMarkdown,
	fallbackName: string,
	source?: OpenclawSource
): SkillLike => {
	const { name: frontmatterName, description, ...rest } = parsed?.frontmatter ?? {};
	const rawName =
		typeof frontmatterName === 'string' && frontmatterName.trim() !== ''
			? frontmatterName
			: fallbackName;

	return {
		name: formatSkillName(rawName),
		id: slugify(rawName),
		description: description ?? '',
		content: parsed?.body ?? '',
		meta: {
			tags: [],
			openclaw: {
				frontmatter: rest,
				config: {},
				env: {},
				...(source ? { source } : {})
			}
		}
	};
};

export const skillToOpenclawMarkdown = (skill: SkillLike): string => {
	const frontmatter = {
		name: slugify(skill?.id || skill?.name || ''),
		description: skill?.description ?? '',
		...(skill?.meta?.openclaw?.frontmatter ?? {})
	};

	return `---\n${stringifyYaml(frontmatter)}---\n\n${(skill?.content ?? '').replace(/(\r?\n)+$/, '')}\n`;
};

const normalizeZipPath = (path: string): string => path.replace(/\\/g, '/').replace(/^\.\//, '');

const isIgnoredZipPath = (path: string): boolean => {
	const parts = normalizeZipPath(path).split('/');
	return parts.some(
		(part) => part === '.DS_Store' || part.startsWith('__MACOSX') || part === '.clawhub'
	);
};

export const extractSkillFromZip = async (
	data: Blob | ArrayBuffer
): Promise<{
	skillMarkdown: string;
	files: Record<string, SkillFileEntry>;
	skipped: number;
	dirName: string;
}> => {
	if (typeof Blob !== 'undefined' && data instanceof Blob) {
		data = await data.arrayBuffer();
	}

	const zip = await JSZip.loadAsync(data);
	const entries = Object.values(zip.files).filter((entry) => !entry.dir);

	// Prefer a root-level SKILL.md, otherwise the shallowest */SKILL.md
	const skillMdEntry =
		entries.find((entry) => normalizeZipPath(entry.name) === 'SKILL.md') ??
		entries
			.filter(
				(entry) =>
					normalizeZipPath(entry.name).endsWith('/SKILL.md') && !isIgnoredZipPath(entry.name)
			)
			.sort(
				(a, b) =>
					normalizeZipPath(a.name).split('/').length - normalizeZipPath(b.name).split('/').length
			)[0];

	if (!skillMdEntry) {
		throw new Error('SKILL.md not found in archive');
	}

	const skillMdPath = normalizeZipPath(skillMdEntry.name);
	const dirName = skillMdPath.includes('/')
		? skillMdPath.slice(0, skillMdPath.lastIndexOf('/'))
		: '';
	const prefix = dirName ? `${dirName}/` : '';

	const skillMarkdown = await skillMdEntry.async('string');

	const files: Record<string, SkillFileEntry> = {};
	let skipped = 0;
	let totalSize = 0;

	const decoder = new TextDecoder('utf-8', { fatal: true });

	for (const entry of entries) {
		const path = normalizeZipPath(entry.name);
		if (path === skillMdPath) continue;
		if (prefix && !path.startsWith(prefix)) continue;

		const relativePath = prefix ? path.slice(prefix.length) : path;
		if (!relativePath || isIgnoredZipPath(relativePath)) continue;

		const bytes = await entry.async('uint8array');

		if (
			Object.keys(files).length >= OPENCLAW_MAX_FILES ||
			bytes.byteLength > OPENCLAW_MAX_FILE_SIZE ||
			totalSize + bytes.byteLength > OPENCLAW_MAX_TOTAL_SIZE
		) {
			skipped += 1;
			continue;
		}

		totalSize += bytes.byteLength;

		try {
			files[relativePath] = { content: decoder.decode(bytes), encoding: 'utf-8' };
		} catch {
			files[relativePath] = { content: await entry.async('base64'), encoding: 'base64' };
		}
	}

	return { skillMarkdown, files, skipped, dirName };
};

export const buildSkillZip = async (skill: SkillLike): Promise<Blob> => {
	const zip = new JSZip();
	const id = slugify(skill?.id || skill?.name || 'skill') || 'skill';
	const folder = zip.folder(id) ?? zip;

	folder.file('SKILL.md', skillToOpenclawMarkdown(skill));

	const files = skill?.meta?.openclaw?.files ?? {};
	for (const [path, entry] of Object.entries(files)) {
		if (entry?.encoding === 'base64') {
			folder.file(path, entry.content, { base64: true });
		} else {
			folder.file(path, entry?.content ?? '');
		}
	}

	return zip.generateAsync({ type: 'blob' });
};

export const getOpenclaw = (skillOrMeta: any): OpenclawMeta | null => {
	const openclaw = skillOrMeta?.meta?.openclaw ?? skillOrMeta?.openclaw ?? null;
	return openclaw && typeof openclaw === 'object' ? openclaw : null;
};

const asStringArray = (value: any): string[] =>
	Array.isArray(value)
		? value.filter((item) => typeof item === 'string' && item.trim() !== '')
		: [];

export const extractGating = (skillOrMeta: any): SkillGating | null => {
	const openclaw = getOpenclaw(skillOrMeta);
	if (!openclaw) return null;

	const gating = openclaw?.frontmatter?.metadata?.openclaw;
	if (!gating || typeof gating !== 'object') return null;

	const requires = gating?.requires ?? {};

	return {
		bins: asStringArray(requires?.bins),
		anyBins: asStringArray(requires?.anyBins),
		env: asStringArray(requires?.env),
		config: asStringArray(requires?.config),
		os: asStringArray(gating?.os),
		always: gating?.always === true,
		...(typeof gating?.emoji === 'string' && gating.emoji !== '' ? { emoji: gating.emoji } : {}),
		install: Array.isArray(gating?.install) ? gating.install : []
	};
};

export const extractInstallSpecs = (skillOrMeta: any): any[] => {
	return extractGating(skillOrMeta)?.install ?? [];
};

export const base64ToBlob = (b64: string, type = 'application/zip'): Blob => {
	const binary = atob(b64);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) {
		bytes[i] = binary.charCodeAt(i);
	}
	return new Blob([bytes], { type });
};
