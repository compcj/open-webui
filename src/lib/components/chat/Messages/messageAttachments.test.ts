import { describe, expect, it, vi } from 'vitest';
import { getUnrenderedMessageFiles } from './messageAttachments';

const image = { type: 'image', url: '/api/v1/files/image-1/content' };
const other = { type: 'image', url: '/api/v1/files/image-2/content' };

describe('unrendered message attachments', () => {
	it.each([
		`![Generated](${image.url})`,
		`![Generated][result]\n\n[result]: ${image.url}`,
		`[![Generated](${image.url})](https://example.com)`,
		`> **![Generated](${image.url})**`
	])('omits actual Markdown image previews: %s', (content) => {
		expect(getUnrenderedMessageFiles([image, other], content)).toEqual([other]);
	});

	it.each([
		`[Download](${image.url})`,
		`\`![Generated](${image.url})\``,
		`\`\`\`markdown\n![Generated](${image.url})\n\`\`\``,
		`![Generated](${image.url}`,
		JSON.stringify({ images: [image] })
	])('keeps attachments when their URL is not rendered as an image: %s', (content) => {
		expect(getUnrenderedMessageFiles([image], content)).toEqual([image]);
	});

	it('keeps images when Markdown rendering is disabled', () => {
		expect(getUnrenderedMessageFiles([image], `![Generated](${image.url})`, false)).toEqual([
			image
		]);
	});

	it('matches image MIME attachments without hiding ordinary downloads', () => {
		const png = { ...image, type: 'file', content_type: 'image/png' };
		const pdf = {
			type: 'file',
			url: '/api/v1/files/report/content',
			content_type: 'application/pdf'
		};
		expect(getUnrenderedMessageFiles([png, pdf], `![Generated](${image.url})`)).toEqual([pdf]);
	});

	it('matches absolute and relative URLs only on the current origin', () => {
		vi.stubGlobal('location', { href: 'https://chat.example.com/c/chat-1' });
		try {
			expect(
				getUnrenderedMessageFiles([image], `![Generated](https://chat.example.com${image.url})`)
			).toEqual([]);
			expect(
				getUnrenderedMessageFiles([image], `![Generated](https://other.example.com${image.url})`)
			).toEqual([image]);
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('does not combine reference definitions from separate structured messages', () => {
		expect(
			getUnrenderedMessageFiles([image], ['![Generated][result]', `[result]: ${image.url}`])
		).toEqual([image]);
	});

	it('leaves persisted files unchanged and tolerates missing files', () => {
		const files = Object.freeze([Object.freeze(image)]);
		expect(getUnrenderedMessageFiles([...files], `![Generated](${image.url})`)).toEqual([]);
		expect(files).toEqual([image]);
		expect(getUnrenderedMessageFiles(undefined, '')).toEqual([]);
	});

	it.each(['$IMAGE$', '$$\nIMAGE\n$$', '\\(IMAGE\\)'])(
		'keeps the attachment when image syntax is inside math: %s',
		(template) => {
			const content = template.replace('IMAGE', `![Generated](${image.url})`);
			expect(getUnrenderedMessageFiles([image], content)).toEqual([image]);
		}
	);

	it('uses the same citation-disabled formatting as the response renderer', () => {
		expect(
			getUnrenderedMessageFiles([image], `![1](${image.url})`, true, {
				citationsEnabled: false
			})
		).toEqual([image]);
	});

	it('uses the same token substitution as the response renderer', () => {
		expect(
			getUnrenderedMessageFiles([image], '![Generated]({{char}})', true, {
				modelName: image.url
			})
		).toEqual([]);
	});
});
