import { describe, expect, it } from 'vitest';
import { buildOutputDisplayItems, type OutputItem } from './structuredOutput';
import { getUnrenderedMessageFiles } from './messageAttachments';

describe('generated image context display', () => {
	it.each(['generate_image', 'edit_image'])(
		'%s keeps visual context hidden and displays one attachment',
		(name) => {
			const image = { type: 'image', url: '/api/v1/files/generated/content' };
			const output: OutputItem[] = [
				{ type: 'function_call', call_id: 'call-1', name, status: 'completed', arguments: '{}' },
				{
					type: 'function_call_output',
					call_id: 'call-1',
					output: [
						{ type: 'input_text', text: JSON.stringify({ status: 'success', images: [image] }) },
						{ type: 'input_image', image_url: image.url }
					]
				},
				{ type: 'message', content: [{ type: 'output_text', text: 'Image created.' }] }
			];
			const items = buildOutputDisplayItems(output);
			expect(items.some((item) => item.type === 'file')).toBe(false);
			const text = items.filter((item) => item.type === 'message').map((item) => item.text);
			expect(text).toEqual(['Image created.']);
			expect(getUnrenderedMessageFiles([image], text)).toEqual([image]);
			expect(getUnrenderedMessageFiles([image], `![Generated](${image.url})`)).toEqual([]);
			const noImageParts = structuredClone(output);
			noImageParts[1].output = noImageParts[1].output?.filter(
				(part) => part.type !== 'input_image'
			);
			expect(items).toEqual(buildOutputDisplayItems(noImageParts));
		}
	);
});
