import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { transpileModule, ScriptTarget } from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import * as protocols from '$lib/utils/connection-protocol';
import * as connectionHeaders from '$lib/utils/connection-headers';
import { normalizeDirectToolSchemaConfig } from '$lib/utils/model-tool-schema';
import { normalizeTags } from '$lib/utils/tags';

// Execute the actual modal state/handlers without mounting its modal and tooltip stack.
const source = readFileSync(new URL('./AddConnectionModal.svelte', import.meta.url), 'utf8');
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => {
		const declaration = node.type === 'ExportNamedDeclaration' ? node.declaration : node;
		if (declaration?.type !== 'VariableDeclaration') return [];
		if (declaration.declarations[0]?.id?.name === 'i18n') return [];
		return [source.slice(declaration.start, declaration.end)];
	})
	.join('\n');

const setup = (
	connection: any = null,
	options: { direct?: boolean; ollama?: boolean; azure?: boolean } = {}
) => {
	const verifyOpenAIConnection = vi.fn(async (_token: string, _payload: any, _direct: boolean) => ({
		data: [{ id: 'native-model' }]
	}));
	const verifyOllamaConnection = vi.fn(async () => ({}));
	const onSubmitMock = vi.fn(async (_connection: any) => {});
	const toast = { error: vi.fn(), success: vi.fn() };
	const dependencies = {
		...protocols,
		...connectionHeaders,
		normalizeTags,
		normalizeDirectToolSchemaConfig,
		verifyOpenAIConnection,
		verifyOllamaConnection,
		onSubmitMock,
		toast,
		initialConnection: connection,
		options
	};
	const body = `
		const { ${Object.keys(dependencies).join(', ')} } = dependencies;
		const $i18n = { t: text => text };
		const localStorage = { token: 'synthetic-token' };
		let azure = options.azure ?? false;
		${declarations}
		connection = initialConnection;
		direct = options.direct ?? false; ollama = options.ollama ?? false;
		edit = Boolean(connection); show = true; onSubmit = onSubmitMock;
		init();
		return {
			verify: verifyHandler, save: submitHandler, choose: changeProtocol,
			set: values => { if ('protocol' in values) apiType = values.protocol; },
			state: () => ({ url, key, auth_type, apiType, loading, verifying, availableModelIds })
		};`;
	return {
		...new Function(
			'dependencies',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(dependencies),
		verifyOpenAIConnection,
		verifyOllamaConnection,
		onSubmitMock,
		toast
	};
};

describe('administrator connection modal requests', () => {
	it('applies new native defaults through the actual selection handler', () => {
		const modal = setup();
		modal.choose('gemini');
		expect(modal.state()).toMatchObject({
			apiType: 'gemini',
			url: 'https://generativelanguage.googleapis.com/v1beta',
			auth_type: 'api_key'
		});
	});
	it.each(['', 'responses'])(
		'saves a new native connection switched to %s using valid bearer auth',
		async (protocol) => {
			const modal = setup();
			modal.choose('anthropic');
			modal.choose(protocol);
			expect(modal.state()).toMatchObject({
				apiType: protocol,
				url: 'https://api.anthropic.com/v1',
				auth_type: 'bearer'
			});
			await modal.verify();
			expect(modal.verifyOpenAIConnection.mock.calls[0][1].config).toMatchObject({
				api_type: protocol,
				auth_type: 'bearer'
			});
			await modal.save();
			expect(modal.onSubmitMock.mock.calls[0][0].config).toMatchObject({
				api_type: protocol,
				auth_type: 'bearer'
			});
		}
	);
	it('keeps verification busy until the request completes and reports errors', async () => {
		const modal = setup({ url: 'https://gateway.example', config: { api_type: 'gemini' } });
		let reject!: (error: string) => void;
		modal.verifyOpenAIConnection.mockImplementationOnce(
			() =>
				new Promise((_resolve, rejectRequest) => {
					reject = rejectRequest;
				})
		);
		const pending = modal.verify();
		expect(modal.state().verifying).toBe(true);
		reject('Request failed');
		await pending;
		expect(modal.state().verifying).toBe(false);
		expect(modal.toast.error).toHaveBeenCalledWith('Request failed');
	});
	it.each(['none', 'bearer', 'session', 'system_oauth'])(
		'preserves custom URL, key and explicit %s gateway auth when switching away from native',
		async (auth_type) => {
			const modal = setup({
				url: 'https://gateway.example/custom',
				key: 'synthetic-retained',
				config: { api_type: 'gemini', auth_type }
			});
			modal.choose('responses');
			expect(modal.state()).toMatchObject({
				url: 'https://gateway.example/custom',
				key: 'synthetic-retained',
				auth_type
			});
			await modal.save();
			expect(modal.onSubmitMock.mock.calls[0][0]).toMatchObject({
				url: 'https://gateway.example/custom',
				key: 'synthetic-retained',
				config: { auth_type, api_type: 'responses' }
			});
		}
	);
	it.each(['gemini', 'anthropic', 'responses', ''])(
		'verifies and saves the selected %s protocol',
		async (protocol) => {
			const modal = setup({
				url: 'https://gateway.example/v1',
				key: 'synthetic',
				config: {
					api_type: protocol,
					auth_type: 'api_key',
					headers: { 'x-custom': 'keep' },
					user_agent: 'Custom/1.0',
					enable_session_id: true,
					session_id_header: 'x-session-id',
					forward_cookies: true,
					model_ids: ['configured'],
					tags: [{ name: 'native' }]
				}
			});
			await modal.verify();
			expect(modal.verifyOpenAIConnection.mock.calls[0][1].config).toMatchObject({
				api_type: protocol,
				auth_type: 'api_key',
				headers: { 'x-custom': 'keep' },
				user_agent: 'Custom/1.0',
				forward_cookies: true
			});
			await modal.save();
			expect(modal.onSubmitMock.mock.calls[0][0].config).toMatchObject({
				api_type: protocol,
				auth_type: 'api_key',
				model_ids: ['configured'],
				tags: [{ name: 'native' }]
			});
		}
	);
	it('normalizes legacy chat completions on load', () => {
		expect(
			setup({ url: 'https://gateway.example', config: { api_type: 'chat_completions' } }).state()
				.apiType
		).toBe('');
	});
	it('defaults missing native auth while preserving explicitly loaded gateway auth', () => {
		for (const protocol of ['gemini', 'anthropic']) {
			expect(
				setup({ url: 'https://gateway.example', config: { api_type: protocol } }).state().auth_type
			).toBe('api_key');
			expect(
				setup({ url: '', key: 'keep', config: { api_type: protocol, auth_type: 'none' } }).state()
			).toMatchObject({ url: '', key: 'keep', auth_type: 'none' });
		}
	});
	it('blocks verify and save for native Azure conflicts', async () => {
		const modal = setup(
			{ url: 'https://gateway.example', config: { api_type: 'gemini', provider: 'azure' } },
			{ azure: true }
		);
		await modal.verify();
		await modal.save();
		expect(modal.verifyOpenAIConnection).not.toHaveBeenCalled();
		expect(modal.onSubmitMock).not.toHaveBeenCalled();
		expect(modal.toast.error).toHaveBeenCalledWith(
			'Native Gemini and Anthropic APIs cannot use Azure OpenAI.'
		);
	});
	it('preserves direct browser verification behavior', async () => {
		const modal = setup(
			{ url: 'https://personal.example', config: { auth_type: 'bearer' } },
			{ direct: true }
		);
		await modal.verify();
		expect(modal.verifyOpenAIConnection.mock.calls[0][2]).toBe(true);
		expect(modal.verifyOpenAIConnection.mock.calls[0][1].config).not.toHaveProperty('api_type');
	});
	it('retains an existing personal direct protocol and tool compatibility configuration on save', async () => {
		const modal = setup(
			{
				url: 'https://personal.example',
				config: {
					api_type: 'responses',
					model_ids: ['personal'],
					model_tool_schema_compatibility: { personal: 'gemini' }
				}
			},
			{ direct: true }
		);
		await modal.save();
		expect(modal.onSubmitMock.mock.calls[0][0].config.api_type).toBe('responses');
		expect(modal.onSubmitMock.mock.calls[0][0].config.model_tool_schema_compatibility).toEqual({
			personal: 'gemini'
		});
	});
	it('keeps Ollama verification on its existing API', async () => {
		const modal = setup({ url: 'https://ollama.example', config: {} }, { ollama: true });
		await modal.verify();
		expect(modal.verifyOllamaConnection).toHaveBeenCalledOnce();
		expect(modal.verifyOpenAIConnection).not.toHaveBeenCalled();
	});
});
