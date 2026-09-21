import { decode } from 'html-entities';
import { marked, prepareMarkdownContent, formatMessageContent } from './markdownRendering';

type Attachment = { type: string; url: string; content_type?: string };

const isImage = (file: Attachment) =>
	file.type === 'image' || (file.content_type ?? '').startsWith('image/');

function imageUrl(url: string): string {
	const decoded = decode(url);
	try {
		return new URL(decoded, globalThis.location?.href).href;
	} catch {
		return decoded;
	}
}

// Keep persisted files intact for subsequent turns; only omit duplicate previews.
export function getUnrenderedMessageFiles<T extends Attachment>(
	files: T[] | null | undefined,
	content: string | string[],
	renderMarkdown = true,
	{
		citationsEnabled = true,
		modelName,
		userName
	}: { citationsEnabled?: boolean; modelName?: string; userName?: string } = {}
): T[] {
	const attachments = (files ?? []).filter((file) => ['image', 'file'].includes(file.type));
	if (!renderMarkdown || !attachments.some(isImage)) return attachments;

	const embeddedImages = new Set<string>();
	for (const text of Array.isArray(content) ? content : [content]) {
		const prepared = prepareMarkdownContent(
			formatMessageContent(text, citationsEnabled),
			modelName,
			userName
		);
		marked.walkTokens(marked.lexer(prepared), (token) => {
			if (token.type === 'image') embeddedImages.add(imageUrl(token.href));
		});
	}
	return attachments.filter((file) => !isImage(file) || !embeddedImages.has(imageUrl(file.url)));
}
