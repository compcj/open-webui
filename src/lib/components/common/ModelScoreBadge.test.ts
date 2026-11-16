import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import { writable } from 'svelte/store';
import ModelScoreBadge from './ModelScoreBadge.svelte';

const renderScore = (score: unknown) =>
	render(ModelScoreBadge, {
		props: { score, id: 'model-score' },
		context: new Map([
			[
				'i18n',
				writable({
					t: (_key: string, values: { score: number }) => `Intelligence score: ${values.score}/99`
				})
			]
		])
	}).body;

describe('model intelligence score badge', () => {
	it.each([0, 1, 50, 99])('renders score %s with an accessible description', (score) => {
		const html = renderScore(score);
		expect(html).toContain('id="model-score"');
		expect(html).toContain(`Intelligence score: ${score}/99`);
		expect(html).toContain(`>${score}</span>`);
	});

	it.each([undefined, null, -1, 100, 1.5, '50', '', true, false, NaN, Infinity])(
		'hides an absent or invalid score (%s)',
		(score) => {
			expect(renderScore(score)).not.toContain('<span');
		}
	);
});
