import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as chatApi from '$lib/apis/chats';
import { isTemporaryChatId } from '$lib/utils/chatId';
import { createMessagesList, getDeepestChildId } from '$lib/utils';
import { WEBUI_API_BASE_URL } from '$lib/constants';

const source = readFileSync(new URL('./Chat.svelte', import.meta.url), 'utf8');
const names = new Set(['addVoiceMessage', 'saveVoice', 'mergeChatMessages']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createChat = (initial: any, { id = 'shared', temporary = false, active = false } = {}) => {
	const initChatHandler = vi.fn(async () => {});
	const body = `
		const { createMessagesList, updateChatById, addChatVoiceMessage, updateChatVoiceMessage,
			initChatHandler, WEBUI_API_BASE_URL, isTemporaryChatId } = dependencies;
		const uuidv4 = () => 'voice-1';
		const localStorage = { token: 'synthetic-token' };
		const $user = { id: 'writer', name: 'Writer' };
		let $chatId = chatId;
		const $temporaryChatEnabled = temporary;
		const chat = { user_id: 'owner' };
		const generating = active, taskIds = [];
		const autoScrollToBottom = () => {};
		let history = initial;
		${declarations}
		return { add: addVoiceMessage, save: saveVoice, merge: mergeChatMessages, history: () => history, navigate: (id, next) => { $chatId = id; history = next; } };
	`;
	return {
		initChatHandler,
		...new Function(
			'dependencies',
			'initial',
			'chatId',
			'temporary',
			'active',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)(
			{ ...chatApi, createMessagesList, initChatHandler, WEBUI_API_BASE_URL, isTemporaryChatId },
			structuredClone(initial),
			id,
			temporary,
			active
		)
	};
};

const history = () => ({
	currentId: 'a',
	messages: {
		u: { id: 'u', role: 'user', user_id: 'writer', content: 'question', childrenIds: ['a'] },
		a: {
			id: 'a',
			role: 'assistant',
			user_id: 'writer',
			parentId: 'u',
			childrenIds: [],
			content: 'current backend answer',
			output: [{ type: 'function_call_output', output: 'raw', citation_context: 'frozen' }],
			meta: { request_context: { private: 'writer' }, voice: { speech: [] } }
		}
	}
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('saved shared voice callbacks', () => {
	it('persists the participant transcript through the narrow API and accepts server authorship', async () => {
		const fetcher = vi.fn(async (url: string, options: any) => {
			if (!url.endsWith('/voice/messages'))
				return new Response(JSON.stringify({ detail: 'Forbidden' }), { status: 401 });
			const payload = JSON.parse(options.body);
			return new Response(
				JSON.stringify({
					messages: {
						'voice-1': {
							id: payload.id,
							role: payload.role,
							content: payload.content,
							parentId: 'a',
							childrenIds: [],
							user_id: 'writer',
							user: { id: 'writer', name: 'Writer' },
							meta: { voice: payload.voice }
						},
						a: { id: 'a', childrenIds: ['voice-1'] }
					},
					currentId: 'voice-1',
					replaceLinks: true
				})
			);
		});
		vi.stubGlobal('fetch', fetcher);
		const chat = createChat(history());
		expect(await chat.add('user', 'spoken words', { model: 'voice' })).toBe('voice-1');
		expect(chat.history().messages['voice-1'].user_id).toBe('writer');
		expect(chat.history().currentId).toBe('voice-1');
		expect(chat.history().messages.a.output).toEqual(history().messages.a.output);
		expect(fetcher).toHaveBeenCalledTimes(1);
		const [url, request] = fetcher.mock.calls[0];
		expect(url).toBe(`${WEBUI_API_BASE_URL}/chats/shared/voice/messages`);
		expect(request.headers.authorization).toBe('Bearer synthetic-token');
		expect(JSON.parse(request.body)).toEqual({
			id: 'voice-1',
			parent_id: 'a',
			branch_id: 'a',
			role: 'user',
			content: 'spoken words',
			voice: { model: 'voice' }
		});
	});

	it.each([false, true])(
		'applies delayed-reply links without overwriting existing output or metadata (active=%s)',
		async (active) => {
			const initial: any = history();
			initial.messages.a.childrenIds = ['next'];
			initial.messages.next = {
				id: 'next',
				role: 'user',
				user_id: 'writer',
				parentId: 'a',
				content: 'next question',
				childrenIds: []
			};
			initial.currentId = 'next';
			if (active) initial.messages.a.done = false;
			vi.stubGlobal('fetch', async (url: string) =>
				url.endsWith('/voice/messages')
					? new Response(
							JSON.stringify({
								messages: {
									'voice-1': {
										id: 'voice-1',
										role: 'assistant',
										user_id: 'writer',
										parentId: 'a',
										childrenIds: ['next'],
										content: 'spoken answer'
									},
									a: { id: 'a', childrenIds: ['voice-1'] },
									next: { id: 'next', parentId: 'voice-1' }
								},
								currentId: 'next',
								replaceLinks: true
							})
						)
					: new Response(JSON.stringify({ detail: 'Forbidden' }), { status: 401 })
			);
			const chat = createChat(initial, { active });
			await chat.add('assistant', 'spoken answer', { model: 'voice' }, 'u');
			expect(chat.history().messages.a.childrenIds).toEqual(['voice-1']);
			expect(chat.history().messages.next.parentId).toBe('voice-1');
			expect(chat.history().messages.a.output).toEqual(initial.messages.a.output);
			expect(chat.history().messages.a.meta).toEqual(initial.messages.a.meta);
			expect(chat.history().currentId).toBe('next');
		}
	);

	it('saves voice metadata through its narrow API and keeps tool state', async () => {
		const fetcher = vi.fn(async (url: string) =>
			url.endsWith('/messages/a/voice')
				? new Response(
						JSON.stringify({
							chat: {
								history: {
									messages: {
										a: { meta: { voice: { speech: [{ item_id: 's', transcript: 'spoken' }] } } }
									}
								}
							}
						})
					)
				: new Response(JSON.stringify({ detail: 'Forbidden' }), { status: 401 })
		);
		vi.stubGlobal('fetch', fetcher);
		const chat = createChat(history());
		await chat.save('a', { speech: [{ item_id: 's', transcript: 'spoken' }] });
		expect(chat.history().messages.a.meta.request_context).toEqual({ private: 'writer' });
		expect(chat.history().messages.a.output).toEqual(history().messages.a.output);
		expect(fetcher.mock.calls[0][0]).toBe(`${WEBUI_API_BASE_URL}/chats/shared/messages/a/voice`);
	});

	it('surfaces access revocation before adding an unsaved local transcript', async () => {
		vi.stubGlobal(
			'fetch',
			async () =>
				new Response(JSON.stringify({ detail: 'Chat continuation is not allowed.' }), {
					status: 403
				})
		);
		const chat = createChat(history());
		await expect(chat.add('user', 'spoken words', {})).rejects.toBeTruthy();
		expect(chat.history().messages['voice-1']).toBeUndefined();
	});

	it.each([
		{ id: 'temporary:local', temporary: true },
		{ id: 'temporary:local', temporary: false },
		{ id: 'local:legacy', temporary: false },
		{ id: '', temporary: false }
	])('keeps local/new voice chats functional (%s)', async (options) => {
		const fetcher = vi.fn();
		vi.stubGlobal('fetch', fetcher);
		const chat = createChat(history(), options);
		await chat.add('user', 'spoken words', { model: 'voice' });
		expect(chat.history().messages['voice-1'].content).toBe('spoken words');
		expect(chat.history().messages['voice-1'].voice_provenance).toEqual({
			source: 'browser',
			user: { id: 'writer', name: 'Writer' }
		});
		fetcher.mockClear();
		await chat.save('voice-1', { speech: [{ item_id: 'local' }] });
		expect(chat.history().messages['voice-1'].meta.voice.speech).toEqual([{ item_id: 'local' }]);
		expect(fetcher).not.toHaveBeenCalled();
		expect(chat.initChatHandler).toHaveBeenCalledTimes(options.id ? 0 : 1);
	});
});

it('does not merge a saved transcript into a different chat after navigation', async () => {
	let release: (response: Response) => void;
	vi.stubGlobal(
		'fetch',
		() =>
			new Promise<Response>((resolve) => {
				release = resolve;
			})
	);
	const chat = createChat(history());
	const saving = chat.add('user', 'spoken words', { model: 'voice' });
	const next = {
		currentId: 'another',
		messages: { another: { id: 'another', role: 'user', content: 'new chat' } }
	};
	chat.navigate('another-chat', next);
	release!(
		new Response(
			JSON.stringify({
				messages: { 'voice-1': { id: 'voice-1', parentId: 'a', role: 'user', childrenIds: [] } },
				currentId: 'voice-1',
				replaceLinks: true
			})
		)
	);
	await saving;
	expect(chat.history().messages['voice-1']).toBeUndefined();
	expect(chat.history().currentId).toBe('another');
});

const pausedVoiceResponse = () => {
	let release!: (turn: any) => void;
	vi.stubGlobal(
		'fetch',
		(_url: string) =>
			new Promise<Response>((resolve) => {
				release = (turn) => resolve(new Response(JSON.stringify(turn)));
			})
	);
	return { release: (turn: any) => release(turn) };
};

const insertedVoiceTurn = () => ({
	messages: {
		'voice-1': {
			id: 'voice-1',
			role: 'user',
			user_id: 'writer',
			user: { id: 'writer', name: 'Writer' },
			parentId: 'a',
			childrenIds: [],
			content: 'spoken words',
			done: true,
			meta: { voice: { model: 'voice', input_id: 'input', speech: [] } }
		},
		a: { id: 'a', parentId: 'u', childrenIds: ['voice-1'] }
	},
	currentId: 'voice-1',
	replaceLinks: true
});

const publicVoiceTurn = (turn: any) => ({
	...turn,
	messages: Object.fromEntries(
		Object.entries(turn.messages).map(([id, message]: [string, any]) => {
			const { meta, ...publicMessage } = message;
			return [id, publicMessage];
		})
	)
});

describe('voice socket events before the HTTP response', () => {
	it('keeps a later participant child and still restores the author private voice', async () => {
		const response = pausedVoiceResponse();
		const chat = createChat(history());
		const saving = chat.add('user', 'spoken words', { model: 'voice' });
		const committed = insertedVoiceTurn();
		chat.merge(publicVoiceTurn(committed));
		chat.merge({
			messages: {
				'other-user': {
					id: 'other-user',
					role: 'user',
					user_id: 'other',
					parentId: 'voice-1',
					childrenIds: [],
					content: 'newer turn',
					done: true
				},
				'voice-1': { id: 'voice-1', parentId: 'a', childrenIds: ['other-user'] }
			},
			currentId: 'other-user',
			replaceLinks: true
		});
		expect(chat.history().messages['voice-1'].childrenIds).toEqual(['other-user']);
		response.release(committed);
		await saving;
		expect(chat.history().messages['voice-1'].childrenIds).toEqual(['other-user']);
		expect(chat.history().messages['other-user'].parentId).toBe('voice-1');
		expect(getDeepestChildId(chat.history(), 'voice-1')).toBe('other-user');
		expect(chat.history().currentId).toBe('other-user');
		expect(chat.history().messages['voice-1'].meta.voice).toEqual(
			committed.messages['voice-1'].meta.voice
		);
		expect(chat.history().messages['voice-1'].user_id).toBe('writer');
	});

	it('keeps a later delayed-speech reparent of the just-created public message', async () => {
		const response = pausedVoiceResponse();
		const chat = createChat(history());
		const saving = chat.add('user', 'spoken words', { model: 'voice' });
		const committed = insertedVoiceTurn();
		chat.merge(publicVoiceTurn(committed));
		chat.merge({
			messages: {
				'late-speech': {
					id: 'late-speech',
					role: 'assistant',
					user_id: 'writer',
					parentId: 'a',
					childrenIds: ['voice-1'],
					content: 'delayed voice answer',
					done: true
				},
				a: { id: 'a', parentId: 'u', childrenIds: ['late-speech'] },
				'voice-1': { id: 'voice-1', parentId: 'late-speech' }
			},
			currentId: 'voice-1',
			replaceLinks: true
		});
		response.release(committed);
		await saving;
		expect(chat.history().messages['voice-1'].parentId).toBe('late-speech');
		expect(chat.history().messages.a.childrenIds).toEqual(['late-speech']);
		expect(createMessagesList(chat.history(), 'voice-1').map((message: any) => message.id)).toEqual(
			['u', 'a', 'late-speech', 'voice-1']
		);
		expect(chat.history().messages['voice-1'].meta.voice.model).toBe('voice');
	});

	it('keeps newer links on an active backend message that socket updates mutate in place', async () => {
		const response = pausedVoiceResponse();
		const initial: any = history();
		initial.messages.a.done = false;
		const chat = createChat(initial, { active: true });
		const activeMessage = chat.history().messages.a;
		const saving = chat.add('user', 'spoken words', { model: 'voice' });
		const committed = insertedVoiceTurn();
		chat.merge(publicVoiceTurn(committed));
		chat.merge({
			messages: {
				'other-user': {
					id: 'other-user',
					role: 'user',
					user_id: 'other',
					parentId: 'a',
					childrenIds: [],
					content: 'a newer sibling',
					done: true
				},
				a: { id: 'a', parentId: 'u', childrenIds: ['voice-1', 'other-user'] }
			},
			currentId: 'voice-1',
			replaceLinks: true
		});
		expect(chat.history().messages.a).toBe(activeMessage);
		expect(activeMessage.childrenIds).toEqual(['voice-1', 'other-user']);
		response.release(committed);
		await saving;
		expect(chat.history().messages.a.childrenIds).toEqual(['voice-1', 'other-user']);
		expect(chat.history().messages.a.output).toEqual(initial.messages.a.output);
		expect(chat.history().messages.a.meta).toEqual(initial.messages.a.meta);
		expect(chat.history().messages.a.done).toBe(false);
	});

	it('preserves newer author voice metadata while merging the initial private response', async () => {
		const response = pausedVoiceResponse();
		const chat = createChat(history());
		const saving = chat.add('user', 'spoken words', { model: 'voice' });
		const committed = insertedVoiceTurn();
		chat.merge(publicVoiceTurn(committed));
		const current = chat.history().messages['voice-1'];
		const newerVoice = {
			...committed.messages['voice-1'].meta.voice,
			speech: [{ item_id: 'later', transcript: 'later speech' }],
			superseded: true
		};
		chat.history().messages['voice-1'] = { ...current, meta: { voice: newerVoice } };
		response.release(committed);
		await saving;
		expect(chat.history().messages['voice-1'].meta.voice).toEqual(newerVoice);
		expect(chat.history().messages['voice-1'].user_id).toBe('writer');
	});
});
