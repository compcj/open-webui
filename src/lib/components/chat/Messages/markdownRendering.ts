import { marked, type MarkedExtension } from 'marked';
import { replaceOutsideCode, replaceTokens, processResponseContent } from '$lib/utils';
import markedExtension from '$lib/utils/marked/extension';
import markedKatexExtension from '$lib/utils/marked/katex-extension';
import { disableSingleTilde } from '$lib/utils/marked/strikethrough-extension';
import { mentionExtension } from '$lib/utils/marked/mention-extension';
import colonFenceExtension from '$lib/utils/marked/colon-fence-extension';
import footnoteExtension from '$lib/utils/marked/footnote-extension';
import citationExtension from '$lib/utils/marked/citation-extension';

// Share the renderer's extensions and preprocessing with attachment deduplication.
const options = { throwOnError: false };
marked.use(markedKatexExtension(options));
marked.use(markedExtension(options));
marked.use(citationExtension());
marked.use(footnoteExtension());
marked.use(colonFenceExtension(options));
marked.use(disableSingleTilde as MarkedExtension);
marked.use({
	extensions: [
		mentionExtension({ triggerChar: '@' }),
		mentionExtension({ triggerChar: '#' }),
		mentionExtension({ triggerChar: '$' })
	]
});

export { marked };

export const prepareMarkdownContent = (content: string, modelName?: string, userName?: string) =>
	replaceTokens(processResponseContent(content), modelName, userName);

export const formatMessageContent = (content: string, citationsEnabled = true) =>
	citationsEnabled
		? content
		: replaceOutsideCode(content, (segment) =>
				segment.replace(/\s*(\[(?:\d+(?:#[^,\]\s]+)?(?:,\s*\d+(?:#[^,\]\s]+)?)*)\])+/g, '')
			);
