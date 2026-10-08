import { Schema } from 'prosemirror-model';
import { EditorState } from 'prosemirror-state';
import { describe, expect, it } from 'vitest';
import { AIAutocompletion } from './AutoCompletion';
const setup = () => {
	const schema = new Schema({
		nodes: {
			doc: { content: 'paragraph+' },
			paragraph: {
				content: 'text*',
				attrs: {
					class: { default: null },
					'data-prompt': { default: null },
					'data-suggestion': { default: null }
				}
			},
			text: {}
		}
	});
	const extension = AIAutocompletion.configure({ debounceTime: null });
	const plugin = (extension.config.addProseMirrorPlugins as Function).call(extension)[0];
	const view: any = {
		state: EditorState.create({
			schema,
			doc: schema.node('doc', null, [
				schema.node('paragraph', { 'data-suggestion': 'Ask another question' })
			])
		})
	};
	view.dispatch = (transaction: any) => {
		view.state = view.state.apply(transaction);
	};
	return { view, plugin, key: (event: any) => plugin.props.handleKeyDown(view, event) };
};
describe('follow-up ghost suggestion acceptance', () => {
	it('inserts the visible suggestion on Tab and clears ghost attributes', () => {
		const { view, key } = setup();
		expect(key({ key: 'Tab' })).toBe(true);
		expect(view.state.doc.textContent).toBe('Ask another question');
		expect(view.state.doc.firstChild.attrs['data-suggestion']).toBeNull();
	});
	it('keeps Shift+Tab available for focus navigation', () => {
		const { view, key } = setup();
		expect(key({ key: 'Tab', shiftKey: true })).toBe(false);
		expect(view.state.doc.textContent).toBe('');
	});
	it('does not accept suggestions during either composition signal', () => {
		const { view, key, plugin } = setup();
		expect(key({ key: 'Tab', isComposing: true })).toBe(false);
		plugin.props.handleDOMEvents.compositionstart(view, {});
		expect(key({ key: 'Tab' })).toBe(false);
		expect(view.state.doc.textContent).toBe('');
		plugin.props.handleDOMEvents.compositionend(view, {});
		expect(key({ key: 'Tab' })).toBe(true);
	});
	it('accepts the suggestion with a horizontal touch gesture', () => {
		const { view, plugin } = setup();
		plugin.props.handleDOMEvents.touchstart(view, { touches: [{ clientX: 10, clientY: 10 }] });
		expect(
			plugin.props.handleDOMEvents.touchend(view, {
				changedTouches: [{ clientX: 80, clientY: 15 }]
			})
		).toBe(true);
		expect(view.state.doc.textContent).toBe('Ask another question');
	});
});
