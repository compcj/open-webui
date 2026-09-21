import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import * as svelte from 'svelte';
// @ts-expect-error Svelte's compiler runtime does not publish type declarations.
import * as server from 'svelte/internal/server';
import { render } from 'svelte/server';
import { get, writable } from 'svelte/store';
import { marked, prepareMarkdownContent, formatMessageContent } from './markdownRendering';
import equal from 'fast-deep-equal';
import { ModuleKind, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
import * as structuredOutput from './structuredOutput';
import * as messageAttachments from './messageAttachments';

// Compile the real parent component. Replace unrelated controls and service imports
// so the regression can check its rendered attachment count/order without a browser
// or the full application. ContentRenderer's boundary renders the supplied Markdown.
const noop = () => {};
const leaf = (body: (props: any) => string) => ({
	default: (renderer: any, props: any) => renderer.push(body(props))
});
const settings = writable({ renderMarkdownInAssistantMessages: true });
const dependencies: Record<string, any> = {
	svelte,
	'svelte/internal/server': server,
	'fast-deep-equal': { default: equal },
	'./structuredOutput': structuredOutput,
	'./messageAttachments': messageAttachments,
	'$lib/stores': {
		settings,
		models: writable([]),
		config: writable({}),
		user: writable(null),
		audioQueue: writable(null),
		temporaryChatEnabled: writable(false),
		TTSWorker: writable(null)
	},
	'$lib/utils': {
		removeAllDetails: (value: string) => value,
		sanitizeResponseContent: (value: string) => value
	},
	'$lib/components/common/Image.svelte': leaf(({ src }) => `<img src="${src}">`),
	'$lib/components/common/FileItem.svelte': leaf(({ url }) => `<a href="${url}">File</a>`),
	'./ContentRenderer.svelte': leaf(({ content, output, model }) => {
		const text = output?.length ? structuredOutput.getOutputText(output) : content;
		const prepared = prepareMarkdownContent(
			formatMessageContent(text, model?.info?.meta?.capabilities?.citations !== false),
			model?.name
		);
		return `<section data-response-content>${
			get(settings).renderMarkdownInAssistantMessages ? marked.parse(prepared) : server.escape(text)
		}</section>`;
	})
};

const source = readFileSync(new URL('./ResponseMessage.svelte', import.meta.url), 'utf8');
const compiled = compile(source, { generate: 'server' }).js.code;
const code = transpileModule(compiled, {
	compilerOptions: { module: ModuleKind.CommonJS }
}).outputText;
const componentModule = { exports: {} as any };
new Function('require', 'module', 'exports', code)(
	(name: string) => dependencies[name] ?? (name.endsWith('.svelte') ? { default: noop } : {}),
	componentModule,
	componentModule.exports
);

const image = { type: 'image', url: '/api/v1/files/generated-1/content' };
const renderMessage = (message: Record<string, unknown>, markdown = true) => {
	settings.set({ renderMarkdownInAssistantMessages: markdown });
	return render(componentModule.exports.default, {
		props: {
			messageId: 'response-1',
			history: {
				messages: {
					'response-1': {
						id: 'response-1',
						role: 'assistant',
						model: 'synthetic-model',
						content: '',
						files: [image],
						done: true,
						...message
					}
				}
			},
			siblings: ['response-1'],
			readOnly: true
		},
		context: new Map([['i18n', writable({ t: (text: string) => text })]])
	}).body;
};

describe('generated images in assistant responses', () => {
	it('renders an image only once when the model embeds the tool attachment', () => {
		const html = renderMessage({ content: `Here is the image.\n\n![Generated](${image.url})` });
		expect(html.split(`src="${image.url}"`)).toHaveLength(2);
	});

	it('places a fallback attachment after the reasoning and tool content', () => {
		const html = renderMessage({
			content: '<details><summary>Thinking and generating</summary>Tool analysis</details>\n\nDone.'
		});
		expect(html.indexOf(`src="${image.url}"`)).toBeGreaterThan(html.indexOf('</section>'));
	});

	it.each([false, true])('deduplicates structured responses when done is %s', (done) => {
		const html = renderMessage({
			done,
			output: [
				{ type: 'reasoning', content: [{ type: 'output_text', text: 'Planning an image' }] },
				{ type: 'function_call', call_id: 'call-1', name: 'generate_image' },
				{
					type: 'function_call_output',
					call_id: 'call-1',
					output: [{ type: 'input_text', text: JSON.stringify({ images: [image] }) }]
				},
				{ type: 'message', content: [{ type: 'output_text', text: `![Result](${image.url})` }] }
			]
		});
		expect(html.split(`src="${image.url}"`)).toHaveLength(2);
	});

	it('retains a fallback when the model only acknowledges generation', () => {
		const html = renderMessage({ content: 'The image has been generated.' });
		expect(html).toContain(`src="${image.url}"`);
	});

	it('preserves downloadable non-image attachments', () => {
		const html = renderMessage({
			content: 'The file is ready.',
			files: [{ type: 'file', url: '/api/v1/files/report/content', name: 'report.pdf' }]
		});
		expect(html).toContain('href="/api/v1/files/report/content"');
	});

	it('keeps the image preview when Markdown rendering is disabled', () => {
		const html = renderMessage({ content: `![Generated](${image.url})` }, false);
		expect(html.split(`src="${image.url}"`)).toHaveLength(2);
	});

	it('ignores stale legacy content when structured output is active', () => {
		const html = renderMessage({
			content: `![Generated](${image.url})`,
			output: [{ type: 'reasoning', content: [{ type: 'output_text', text: 'Generating' }] }]
		});
		expect(html.split(`src="${image.url}"`)).toHaveLength(2);
	});

	it('keeps attachments if a legacy error prevents rendering the response content', () => {
		const html = renderMessage({ error: true, content: `![Generated](${image.url})` });
		expect(html.split(`src="${image.url}"`)).toHaveLength(2);
	});
});
