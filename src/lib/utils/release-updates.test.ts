import { describe, expect, it } from 'vitest';
import { getReleaseChannels } from './release-updates';

describe('fork and upstream releases', () => {
	it('compares fork revisions numerically within the same upstream version', () => {
		const [fork, upstream] = getReleaseChannels({
			current: '0.13.4+f9',
			fork_latest: '0.13.4+f10',
			latest: '0.13.4'
		});
		expect(fork.available).toBe(true);
		expect(fork.url).toBe('https://github.com/compcj/open-webui/releases/tag/v0.13.4%2Bf10');
		expect(upstream.available).toBe(false);
	});
	it('shows a newer upstream release independently of an unavailable fork feed', () => {
		const [fork, upstream] = getReleaseChannels({
			current: '0.13.4+f12',
			fork_latest: null,
			latest: '0.14.0'
		});
		expect(fork.version).toBeNull();
		expect(upstream.available).toBe(true);
		expect(upstream.url).toBe('https://github.com/open-webui/open-webui/releases/tag/v0.14.0');
	});
	it('does not offer an older upstream series with a higher fork revision', () => {
		const [fork] = getReleaseChannels({
			current: '0.13.4+f1',
			fork_latest: '0.13.3+f20',
			latest: null
		});
		expect(fork.available).toBe(false);
	});
});
