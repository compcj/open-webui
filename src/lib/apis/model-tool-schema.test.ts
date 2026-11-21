import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getModels, getPersonalTerminalServers } from './index';
import { config as appConfig, settings, user } from '$lib/stores';
import { getOpenAIModelsDirect } from './openai';
import type { ToolSchemaModel } from '$lib/utils/model-tool-schema';

vi.mock('$lib/utils', () => ({ convertOpenApiToToolPayload: vi.fn(), resolveSchema: vi.fn() }));
vi.mock('./openai', () => ({ getOpenAIModelsDirect: vi.fn() }));

beforeEach(() => {
	vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: [] }) }));
	vi.mocked(getOpenAIModelsDirect).mockResolvedValue({ data: [] });
});
afterEach(() => {
	vi.unstubAllGlobals();
	vi.clearAllMocks();
});

const connections = (configs: Record<string, unknown>[]) => ({
	OPENAI_API_BASE_URLS: configs.map((_, index) => `https://provider-${index}.example/v1`),
	OPENAI_API_KEYS: configs.map(() => 'synthetic-key'),
	OPENAI_API_CONFIGS: Object.fromEntries(configs.map((config, index) => [String(index), config]))
});

describe('direct model tool schema configuration', () => {
	it('uses manual choices for listed models, including opaque aliases and prefixed IDs', async () => {
		const result = await getModels(
			'',
			connections([
				{
					model_ids: ['opaque', 'gemini-without-selection'],
					prefix_id: 'connection',
					model_tool_schema_compatibility: { opaque: 'gemini' }
				}
			])
		);
		expect(
			result.map((model: ToolSchemaModel) => [
				model.id,
				model.info?.meta?.tool_schema_compatibility
			])
		).toEqual([
			['connection.opaque', 'gemini'],
			['connection.gemini-without-selection', 'default']
		]);
		expect(result[0]).toMatchObject({ direct: true, openai: { id: 'opaque' } });
		expect(getOpenAIModelsDirect).not.toHaveBeenCalled();
	});

	it('applies choices to discovered models without trusting provider metadata or filtering the list', async () => {
		vi.mocked(getOpenAIModelsDirect).mockResolvedValue({
			data: [
				{ id: 'opaque' },
				{ id: 'gemini-auto', info: { meta: { tool_schema_compatibility: 'gemini' } } }
			]
		});
		const result = await getModels(
			'',
			connections([
				{
					model_tool_schema_compatibility: { opaque: 'gemini' }
				}
			])
		);
		expect(
			result.map((model: ToolSchemaModel) => [
				model.id,
				model.info?.meta?.tool_schema_compatibility
			])
		).toEqual([
			['opaque', 'gemini'],
			['gemini-auto', 'default']
		]);
	});

	it.each([undefined, 'default', 'gemini', 'auto', true])(
		'keeps the direct choice %s independent of a colliding saved model',
		async (mode) => {
			vi.mocked(fetch).mockResolvedValue({
				ok: true,
				json: async () => ({
					data: [
						{
							id: 'opaque',
							info: { meta: { description: 'saved', tool_schema_compatibility: 'gemini' } }
						}
					]
				})
			} as Response);
			const result = await getModels(
				'',
				connections([
					{
						model_ids: ['opaque'],
						model_tool_schema_compatibility: { opaque: mode }
					}
				])
			);
			expect(result).toHaveLength(1);
			expect(result[0]).toMatchObject({
				direct: true,
				info: {
					meta: {
						description: 'saved',
						tool_schema_compatibility: mode === 'gemini' ? 'gemini' : 'default'
					}
				}
			});
		}
	);

	it('isolates per-connection choices for models with the same provider ID', async () => {
		const result = await getModels(
			'',
			connections([
				{
					prefix_id: 'one',
					model_ids: ['opaque'],
					model_tool_schema_compatibility: { opaque: 'gemini' }
				},
				{ prefix_id: 'two', model_ids: ['opaque'] }
			])
		);
		expect(
			result.map((model: ToolSchemaModel) => [
				model.id,
				model.info?.meta?.tool_schema_compatibility
			])
		).toEqual([
			['one.opaque', 'gemini'],
			['two.opaque', 'default']
		]);
	});

	it('keeps connection identity when an earlier connection has no configuration', async () => {
		const config = connections([
			{},
			{
				prefix_id: 'second',
				model_ids: ['opaque'],
				model_tool_schema_compatibility: { opaque: 'gemini' }
			}
		]);
		delete config.OPENAI_API_CONFIGS['0'];
		const result = await getModels('', config);
		expect(result).toHaveLength(1);
		expect(result[0]).toMatchObject({
			id: 'second.opaque',
			urlIdx: '1',
			info: { meta: { tool_schema_compatibility: 'gemini' } }
		});
	});
});

describe('personal connection discovery', () => {
	afterEach(() => {
		appConfig.set(undefined);
		settings.set({});
		user.set(undefined);
	});
	it('honors both model catalog signatures and keeps base listings server-only', async () => {
		appConfig.set({ features: { enable_direct_connections: true } } as any);
		settings.set({ directConnections: connections([{ model_ids: ['personal'] }]) });
		const context = { terminalId: 'template:server:shell', chatId: 'saved', cwd: '/w' };
		const data = await getModels('token', false, true, context);
		expect(data.map((model: { id: string }) => model.id)).toEqual(['personal']);
		expect(vi.mocked(fetch).mock.calls[0][0]).toContain(
			'terminal_id=template%3Aserver%3Ashell&chat_id=saved&cwd=%2Fw&refresh=true'
		);
		const base = await getModels('token', true);
		expect(base).toEqual([]);
		expect(vi.mocked(fetch).mock.calls[1][0]).toContain('/api/models/base?');
	});
	it('binds terminal keys and upload choices to the responding server after another fails discovery', async () => {
		appConfig.set({
			features: { enable_tool_servers: true, enable_direct_integrations: true }
		} as any);
		user.set({
			id: 'owner',
			role: 'user',
			permissions: { features: { direct_tool_servers: true } }
		} as any);
		vi.mocked(fetch).mockImplementation(async (input) => {
			const url = String(input);
			if (url.startsWith('https://offline.example')) return Response.json({}, { status: 503 });
			if (url.endsWith('/api/config')) return Response.json({});
			return Response.json({ info: { title: 'Second' }, paths: {} });
		});
		const servers = [
			{ url: 'https://offline.example', key: 'first', enabled: true },
			{
				url: 'https://online.example',
				key: 'second',
				enabled: true,
				config: { chat_uploads: 'filesystem' }
			}
		];
		const result = await getPersonalTerminalServers(servers);
		expect(result).toHaveLength(1);
		expect(result[0]).toMatchObject({
			url: servers[1].url,
			key: 'second',
			config: servers[1].config
		});
		appConfig.set({
			features: { enable_tool_servers: true, enable_direct_integrations: false }
		} as any);
		vi.mocked(fetch).mockClear();
		expect(await getPersonalTerminalServers(servers)).toEqual([]);
		expect(fetch).not.toHaveBeenCalled();
	});
});
