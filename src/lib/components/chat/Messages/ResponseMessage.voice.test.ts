import { readFileSync } from 'node:fs';
import { compile } from 'svelte/compiler';
import * as svelte from 'svelte';
// @ts-expect-error Svelte's compiler runtime does not publish type declarations.
import * as server from 'svelte/internal/server';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import equal from 'fast-deep-equal';
import { ModuleKind, transpileModule } from 'typescript';
import { describe, expect, it } from 'vitest';
import * as structuredOutput from './structuredOutput';

// Render the real response component, stubbing unrelated controls and services.
const dependencies: Record<string, any> = {
	svelte,
	'svelte/internal/server': server,
	'fast-deep-equal': { default: equal },
	'./structuredOutput': structuredOutput,
	'$lib/stores': {
		settings: writable({}),
		models: writable([]),
		config: writable({}),
		user: writable({ id: 'owner' }),
		audioQueue: writable(null),
		temporaryChatEnabled: writable(false),
		TTSWorker: writable(null)
	},
	'$lib/utils': {
		removeAllDetails: (value: string) => value,
		sanitizeResponseContent: (value: string) => value
	}
};
const source = readFileSync(new URL('./ResponseMessage.svelte', import.meta.url), 'utf8');
const code = transpileModule(compile(source, { generate: 'server' }).js.code, {
	compilerOptions: { module: ModuleKind.CommonJS }
}).outputText;
const componentModule = { exports: {} as any };
new Function('require', 'module', 'exports', code)(
	(name: string) => dependencies[name] ?? (name.endsWith('.svelte') ? { default: () => {} } : {}),
	componentModule,
	componentModule.exports
);

const renderMessage = (message: Record<string, unknown>, compactPreview = false) =>
	render(componentModule.exports.default, {
		props: {
			messageId: 'response',
			history: {
				messages: {
					response: {
						id: 'response',
						role: 'assistant',
						user_id: 'writer',
						model: 'voice-model',
						content: 'spoken answer',
						done: true,
						...message
					}
				}
			},
			siblings: ['response'],
			readOnly: true,
			compactPreview
		},
		context: new Map([
			[
				'i18n',
				writable({
					t: (text: string, values?: Record<string, string>) =>
						text.replace('{{name}}', values?.name ?? '')
				})
			]
		])
	}).body;

describe('browser-submitted voice provenance', () => {
	it.each([false, true])(
		'shows transcript origin and actor to the owner (compact=%s)',
		(compact) => {
			const html = renderMessage(
				{
					voice_provenance: { source: 'browser', user: { id: 'writer', name: 'Writer' } }
				},
				compact
			);
			expect(html).toContain('Browser-submitted voice transcript by Writer');
		}
	);

	it('escapes the public actor name', () => {
		const html = renderMessage({
			voice_provenance: {
				source: 'browser',
				user: { id: 'writer', name: '<script>actor</script>' }
			}
		});
		expect(html).toContain('&lt;script>actor&lt;/script>');
		expect(html).not.toContain('<script>actor</script>');
	});

	it('keeps ordinary assistant replies without a transcript marker', () => {
		expect(renderMessage({})).not.toContain('Browser-submitted voice transcript');
	});
});
