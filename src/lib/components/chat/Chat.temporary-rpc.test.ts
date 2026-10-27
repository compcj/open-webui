import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { describe, expect, it, vi } from 'vitest';
import { isTemporaryContextRpc } from '$lib/utils/temporary-context';
const source = readFileSync(new URL('../../../routes/+layout.svelte', import.meta.url), 'utf8');
const declaration: any = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.find((node: any) => node.id?.name === 'chatEventHandler');
describe('page-local RPC handoff', () => {
	it('requires the active page guard before running scoped completion or returning its callback', async () => {
		const dispatch = vi.fn(),
			execute = vi.fn(),
			callback = vi.fn();
		const handler = new Function(
			'isTemporaryContextRpc',
			'window',
			'CustomEvent',
			'applyLayoutChatEvent',
			`const ${source.slice(declaration.start, declaration.end)}; return chatEventHandler;`
		)(
			isTemporaryContextRpc,
			{ dispatchEvent: dispatch },
			class {
				constructor(
					public type: string,
					public detail: any
				) {
					this.detail = detail.detail;
				}
			},
			execute
		);
		const event = {
			temporary_context: { id: 'scope', epoch: 'epoch' },
			data: { type: 'request:chat:completion' }
		};
		await handler(event, callback);
		expect(execute).not.toHaveBeenCalled();
		const handoff = dispatch.mock.calls[0][0];
		expect(handoff.type).toBe('temporary-context:rpc');
		await handoff.detail.run(() => false);
		expect(execute).not.toHaveBeenCalled();
		let active = true;
		await handoff.detail.run(() => active);
		expect(execute).toHaveBeenCalledOnce();
		const guarded = execute.mock.calls[0][1];
		active = false;
		guarded('late');
		expect(callback).not.toHaveBeenCalled();
	});
});
