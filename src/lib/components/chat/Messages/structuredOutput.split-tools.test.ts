import { describe, expect, it } from 'vitest';
import {
	applyResponseStreamEvent,
	buildOutputDisplayItems,
	type OutputItem
} from './structuredOutput';

const parent: OutputItem = {
	type: 'function_call',
	id: 'fetch-parent',
	call_id: 'fetch-parent',
	name: 'fetch_url',
	arguments: '{"url":"https://example.test/one"}{"url":"https://example.test/two"}',
	status: 'in_progress'
};

const calls = (output: OutputItem[]) =>
	buildOutputDisplayItems(output).flatMap((item) =>
		item.type === 'detail_group' ? item.tokens : item.type === 'detail_single' ? [item.token] : []
	);

describe('split tool-call display', () => {
	it('replaces the streamed placeholder with the actual calls and their results', () => {
		let output = applyResponseStreamEvent([], {
			type: 'response.output_item.added',
			output_index: 0,
			item: parent
		});
		expect(calls(output).map((token) => token.attributes.done)).toEqual(['false']);
		output = applyResponseStreamEvent(output, {
			type: 'response.output_item.done',
			output_index: 0,
			item: { ...parent, status: 'superseded' }
		});
		for (const [index, callId] of ['fetch-one', 'fetch-two'].entries()) {
			output = applyResponseStreamEvent(output, {
				type: 'response.output_item.done',
				output_index: index + 1,
				item: { ...parent, id: callId, call_id: callId, arguments: '{}', status: 'completed' }
			});
		}
		expect(calls(output).map((token) => token.attributes.id)).toEqual(['fetch-one', 'fetch-two']);
		expect(calls(output).every((token) => token.attributes.done === 'false')).toBe(true);
		for (const [index, callId] of ['fetch-one', 'fetch-two'].entries()) {
			output = applyResponseStreamEvent(output, {
				type: 'response.output_item.done',
				output_index: index + 3,
				item: {
					type: 'function_call_output',
					id: `result-${callId}`,
					call_id: callId,
					status: 'completed',
					output: [{ type: 'input_text', text: `Result ${index + 1}` }]
				}
			});
		}
		expect(calls(output).map((token) => token.attributes.done)).toEqual(['true', 'true']);
		expect(calls(output).map((token) => token.text)).toEqual(['Result 1', 'Result 2']);
		expect(calls(JSON.parse(JSON.stringify(output)))).toEqual(calls(output));
		expect(output[0].arguments).toBe(parent.arguments);
	});

	it('keeps a real unfinished call visible alongside a superseded placeholder', () => {
		const output = [
			{ ...parent, status: 'superseded' },
			{ ...parent, id: 'active', call_id: 'active', arguments: '{}' }
		];
		expect(calls(output).map((token) => token.attributes.id)).toEqual(['active']);
		expect(calls(output)[0].attributes.done).toBe('false');
	});
});
