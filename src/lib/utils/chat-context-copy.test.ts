import { expect, it } from 'vitest';
import { projectRawChat } from './chat-context-copy';
it('preserves every branch, signed replay and business payload while dropping derived UI identities', () => {
	const payload = { selection_id: 'business', temporary_context: 'business', cursor: 'business' };
	const chat = {
		temporary_context: { id: 'scope' },
		contextSummary: 'summary',
		history: {
			currentId: 'a',
			messages: {
				a: {
					id: 'a',
					parentId: 'u',
					role: 'assistant',
					context_usage: { tokens: 2 },
					meta: { temporary_context: { id: 's' }, custom: 7 },
					output: [
						{
							type: 'function_call',
							provider_function: { name: 'actual' },
							arguments: JSON.stringify(payload),
							reasoning_details: [{ signature: ' exact\nbytes ' }],
							selection_id: 'derived',
							citation_context: 'frozen',
							content: payload
						}
					]
				},
				b: { id: 'b', parentId: 'u', content: 'sibling' }
			}
		}
	};
	const result = projectRawChat(chat);
	expect(result.temporary_context).toBeUndefined();
	expect(result.contextSummary).toBeUndefined();
	expect(result.history.messages.b).toEqual(chat.history.messages.b);
	const a = result.history.messages.a;
	expect(a.context_usage).toBeUndefined();
	expect(a.meta).toEqual({ custom: 7 });
	expect(a.output[0].selection_id).toBeUndefined();
	expect(a.output[0].provider_function).toEqual({ name: 'actual' });
	expect(a.output[0].content).toEqual(payload);
	expect(a.output[0].reasoning_details).toEqual(
		chat.history.messages.a.output[0].reasoning_details
	);
	expect(a.output[0].citation_context).toBe('frozen');
	expect(chat.temporary_context).toEqual({ id: 'scope' });
});

it('removes temporary chat identifiers at chat envelopes without deleting message ids', () => {
	const projected = projectRawChat({
		id: 'temporary:scope',
		chat: {
			id: 'temporary:scope',
			history: {
				messages: { raw: { id: 'raw', content: { id: 'temporary:business' } } },
				currentId: 'raw'
			}
		}
	});
	expect(projected.id).toBeUndefined();
	expect(projected.chat.id).toBeUndefined();
	expect(projected.chat.history.messages.raw).toEqual({
		id: 'raw',
		content: { id: 'temporary:business' }
	});
});
