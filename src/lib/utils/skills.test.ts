import { describe, expect, it } from 'vitest';
import JSZip from 'jszip';

import {
	parseSkillMarkdown,
	openclawToSkill,
	skillToOpenclawMarkdown,
	extractSkillFromZip,
	buildSkillZip,
	extractGating,
	extractInstallSpecs,
	getOpenclaw,
	base64ToBlob
} from './skills';

describe('parseSkillMarkdown', () => {
	it('parses simple frontmatter and body', () => {
		const text = '---\nname: my-skill\ndescription: Does things\n---\n\n# Hello\n\nDo stuff.\n';
		const parsed = parseSkillMarkdown(text);
		expect(parsed).not.toBeNull();
		expect(parsed?.frontmatter).toEqual({ name: 'my-skill', description: 'Does things' });
		expect(parsed?.body).toBe('# Hello\n\nDo stuff.\n');
	});

	it('preserves nested openclaw metadata', () => {
		const text =
			'---\nname: gemini\nmetadata:\n  openclaw:\n    requires:\n      bins:\n        - gemini\n    emoji: "♊️"\n---\nbody\n';
		const parsed = parseSkillMarkdown(text);
		expect(parsed?.frontmatter?.metadata?.openclaw?.requires?.bins).toEqual(['gemini']);
		expect(parsed?.frontmatter?.metadata?.openclaw?.emoji).toBe('♊️');
	});

	it('parses folded multi-line descriptions', () => {
		const text =
			'---\nname: x\ndescription: >\n  A longer description\n  spanning two lines.\n---\nbody\n';
		const parsed = parseSkillMarkdown(text);
		expect(parsed?.frontmatter?.description).toBe('A longer description spanning two lines.\n');
	});

	it('handles CRLF line endings', () => {
		const parsed = parseSkillMarkdown('---\r\nname: crlf\r\n---\r\n\r\nbody\r\n');
		expect(parsed?.frontmatter).toEqual({ name: 'crlf' });
		expect(parsed?.body).toBe('body\r\n');
	});

	it('returns null without frontmatter', () => {
		expect(parseSkillMarkdown('# Just markdown\n')).toBeNull();
		expect(parseSkillMarkdown('')).toBeNull();
	});

	it('returns null for invalid YAML or non-object frontmatter', () => {
		expect(parseSkillMarkdown('---\n: [unclosed\n---\nbody\n')).toBeNull();
		expect(parseSkillMarkdown('---\njust a string\n---\nbody\n')).toBeNull();
		expect(parseSkillMarkdown('---\n- a\n- list\n---\nbody\n')).toBeNull();
	});
});

describe('openclawToSkill', () => {
	it('extracts name/description and keeps remaining keys in frontmatter', () => {
		const parsed = parseSkillMarkdown(
			'---\nname: web-scraper\ndescription: Scrapes\nlicense: MIT\nallowed-tools: Bash(curl:*)\n---\nDo it.\n'
		);
		const skill = openclawToSkill(parsed!, 'fallback');
		expect(skill.name).toBe('Web Scraper');
		expect(skill.id).toBe('web-scraper');
		expect(skill.description).toBe('Scrapes');
		expect(skill.content).toBe('Do it.\n');
		expect(skill.meta?.openclaw?.frontmatter).toEqual({
			license: 'MIT',
			'allowed-tools': 'Bash(curl:*)'
		});
		expect(skill.meta?.openclaw?.config).toEqual({});
		expect(skill.meta?.openclaw?.env).toEqual({});
		expect(skill.meta?.openclaw?.source).toBeUndefined();
	});

	it('falls back to the provided name when frontmatter has no name', () => {
		const parsed = parseSkillMarkdown('---\ndescription: No name\n---\nbody\n');
		const skill = openclawToSkill(parsed!, 'my file');
		expect(skill.name).toBe('My File');
		expect(skill.id).toBe('my-file');
	});

	it('slugifies the id and records the source', () => {
		const parsed = parseSkillMarkdown('---\nname: Déjà Vu Skill!\n---\nbody\n');
		const skill = openclawToSkill(parsed!, 'x', { type: 'url', url: 'https://example.com' });
		expect(skill.id).toBe('deja-vu-skill');
		expect(skill.meta?.openclaw?.source).toEqual({ type: 'url', url: 'https://example.com' });
	});
});

describe('skillToOpenclawMarkdown', () => {
	it('round-trips frontmatter fidelity through openclawToSkill', () => {
		const original =
			'---\nname: gemini\ndescription: Gemini CLI\nallowed-tools: Bash(gemini:*)\nmetadata:\n  openclaw:\n    requires:\n      bins:\n        - gemini\n      env:\n        - GEMINI_API_KEY\n    os:\n      - linux\n---\n\nUse the CLI.\n';
		const parsed = parseSkillMarkdown(original)!;
		const skill = openclawToSkill(parsed, 'fallback', { type: 'file' });

		const markdown = skillToOpenclawMarkdown(skill);
		const reparsed = parseSkillMarkdown(markdown);

		expect(reparsed?.frontmatter?.name).toBe('gemini');
		expect(reparsed?.frontmatter?.description).toBe('Gemini CLI');
		expect(reparsed?.frontmatter?.['allowed-tools']).toBe('Bash(gemini:*)');
		expect(reparsed?.frontmatter?.metadata?.openclaw?.requires).toEqual({
			bins: ['gemini'],
			env: ['GEMINI_API_KEY']
		});
		expect(reparsed?.frontmatter?.metadata?.openclaw?.os).toEqual(['linux']);
		expect(reparsed?.body).toBe('Use the CLI.\n');
	});
});

describe('extractSkillFromZip', () => {
	const makeZip = async () => {
		const zip = new JSZip();
		zip.file('gemini-skill/SKILL.md', '---\nname: gemini\ndescription: G\n---\nbody\n');
		zip.file('gemini-skill/scripts/run.sh', '#!/bin/sh\necho hi\n');
		zip.file('gemini-skill/assets/logo.png', new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0xff]));
		zip.file('__MACOSX/gemini-skill/._SKILL.md', 'junk');
		zip.file('gemini-skill/__MACOSX_stuff.txt', 'junk');
		zip.file('gemini-skill/.DS_Store', 'junk');
		zip.file('gemini-skill/.clawhub/origin.json', '{}');
		return zip.generateAsync({ type: 'uint8array' });
	};

	it('extracts markdown and files relative to the SKILL.md directory', async () => {
		const data = await makeZip();
		const result = await extractSkillFromZip(data.buffer as ArrayBuffer);

		expect(result.dirName).toBe('gemini-skill');
		expect(result.skillMarkdown).toContain('name: gemini');
		expect(result.skipped).toBe(0);

		expect(Object.keys(result.files).sort()).toEqual(['assets/logo.png', 'scripts/run.sh']);
		expect(result.files['scripts/run.sh']).toEqual({
			content: '#!/bin/sh\necho hi\n',
			encoding: 'utf-8'
		});
		expect(result.files['assets/logo.png'].encoding).toBe('base64');
	});

	it('rejects archives without SKILL.md', async () => {
		const zip = new JSZip();
		zip.file('README.md', 'nope');
		const data = await zip.generateAsync({ type: 'uint8array' });
		await expect(extractSkillFromZip(data.buffer as ArrayBuffer)).rejects.toThrow(
			'SKILL.md not found in archive'
		);
	});

	it('round-trips through buildSkillZip', async () => {
		const parsed = parseSkillMarkdown(
			'---\nname: round-trip\ndescription: RT\nlicense: MIT\n---\nDo things.\n'
		)!;
		const skill = openclawToSkill(parsed, 'fallback');
		skill.meta!.openclaw!.files = {
			'scripts/run.sh': { content: 'echo ok\n', encoding: 'utf-8' },
			'assets/bin.dat': { content: btoa('\x00\xff\x01'), encoding: 'base64' }
		};

		const blob = await buildSkillZip(skill);
		const result = await extractSkillFromZip(blob);

		expect(result.dirName).toBe('round-trip');
		const reparsed = parseSkillMarkdown(result.skillMarkdown);
		expect(reparsed?.frontmatter?.name).toBe('round-trip');
		expect(reparsed?.frontmatter?.license).toBe('MIT');
		expect(reparsed?.body).toBe('Do things.\n');
		expect(result.files['scripts/run.sh']).toEqual({ content: 'echo ok\n', encoding: 'utf-8' });
		expect(result.files['assets/bin.dat'].encoding).toBe('base64');
	});
});

describe('extractGating / extractInstallSpecs', () => {
	const skill = {
		meta: {
			openclaw: {
				frontmatter: {
					metadata: {
						openclaw: {
							requires: {
								bins: ['gemini'],
								anyBins: ['a', 'b'],
								env: ['GEMINI_API_KEY'],
								config: ['a.b']
							},
							os: ['linux', 'darwin'],
							always: true,
							emoji: '♊️',
							install: [{ kind: 'brew', formula: 'gemini-cli', label: 'Install Gemini CLI (brew)' }]
						}
					}
				}
			}
		}
	};

	it('extracts gating from a skill or its meta', () => {
		const expected = {
			bins: ['gemini'],
			anyBins: ['a', 'b'],
			env: ['GEMINI_API_KEY'],
			config: ['a.b'],
			os: ['linux', 'darwin'],
			always: true,
			emoji: '♊️',
			install: [{ kind: 'brew', formula: 'gemini-cli', label: 'Install Gemini CLI (brew)' }]
		};
		expect(extractGating(skill)).toEqual(expected);
		expect(extractGating(skill.meta)).toEqual(expected);
	});

	it('extracts install specs', () => {
		expect(extractInstallSpecs(skill)).toEqual([
			{ kind: 'brew', formula: 'gemini-cli', label: 'Install Gemini CLI (brew)' }
		]);
	});

	it('returns null / empty without openclaw meta', () => {
		expect(extractGating({})).toBeNull();
		expect(extractGating({ meta: {} })).toBeNull();
		expect(extractGating({ meta: { openclaw: { frontmatter: {} } } })).toBeNull();
		expect(extractInstallSpecs({})).toEqual([]);
		expect(getOpenclaw({ meta: { openclaw: { env: {} } } })).toEqual({ env: {} });
	});
});

describe('base64ToBlob', () => {
	it('decodes base64 into a blob', async () => {
		const blob = base64ToBlob(btoa('hello'));
		expect(blob.type).toBe('application/zip');
		expect(await blob.text()).toBe('hello');
	});
});
