import { describe, expect, it } from 'vitest';

import { buildSlashMentionContent } from './slash-mention';

describe('buildSlashMentionContent', () => {
	it('keeps the slash trigger char so skill mentions serialize as <$id|label>', () => {
		const content = buildSlashMentionContent({ id: 'my-skill|My Skill', label: 'My Skill' });

		expect(content[0]).toEqual({
			type: 'mention',
			attrs: { id: 'my-skill|My Skill', label: 'My Skill', mentionSuggestionChar: '/' }
		});
	});

	it('does not mutate the passed props', () => {
		const props = { id: 'my-skill|My Skill', label: 'My Skill' };
		buildSlashMentionContent(props);

		expect(props).toEqual({ id: 'my-skill|My Skill', label: 'My Skill' });
	});

	it('appends a trailing space after the mention', () => {
		const content = buildSlashMentionContent({ id: 'my-skill|My Skill', label: 'My Skill' });

		expect(content[1]).toEqual({ type: 'text', text: ' ' });
	});
});
