import { afterEach, describe, expect, it, vi } from 'vitest';
import * as chats from './index';
import { WEBUI_API_BASE_URL } from '$lib/constants';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('narrow voice persistence API', () => {
	it('encodes chat identity and sends only one voice transcript', async () => {
		const fetcher = vi.fn(
			async () => new Response(JSON.stringify({ messages: {}, currentId: null }))
		);
		vi.stubGlobal('fetch', fetcher);
		expect(typeof chats.addChatVoiceMessage).toBe('function');
		const message = {
			id: 'new',
			role: 'user' as const,
			parent_id: null,
			branch_id: null,
			content: 'spoken words',
			voice: { model: 'voice' }
		};
		await chats.addChatVoiceMessage('token', 'chat/one', message);
		expect(fetcher).toHaveBeenCalledWith(
			`${WEBUI_API_BASE_URL}/chats/chat%2Fone/voice/messages`,
			expect.objectContaining({
				method: 'POST',
				body: JSON.stringify(message),
				headers: expect.objectContaining({ authorization: 'Bearer token' })
			})
		);
	});

	it('encodes message identity and passes through server authorization errors', async () => {
		const fetcher = vi.fn(
			async (_url: string) =>
				new Response(JSON.stringify({ detail: 'Chat continuation is not allowed.' }), {
					status: 403
				})
		);
		vi.stubGlobal('fetch', fetcher);
		expect(typeof chats.updateChatVoiceMessage).toBe('function');
		await expect(
			chats.updateChatVoiceMessage('token', 'chat/one', 'message/one', { speech: [] })
		).rejects.toBe('Chat continuation is not allowed.');
		expect(fetcher.mock.calls[0][0]).toBe(
			`${WEBUI_API_BASE_URL}/chats/chat%2Fone/messages/message%2Fone/voice`
		);
	});
});
