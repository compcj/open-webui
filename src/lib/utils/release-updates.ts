export type ReleaseVersions = {
	current: string;
	latest?: string | null;
	fork_latest?: string | null;
};

function parts(version: string, fork: boolean) {
	const match = /^(\d+)\.(\d+)\.(\d+)(?:\+f(\d+))?$/.exec(version);
	return match
		? [Number(match[1]), Number(match[2]), Number(match[3]), fork ? Number(match[4] ?? 0) : 0]
		: null;
}

export function getReleaseChannels(versions: ReleaseVersions) {
	return [
		{ fork: true, repository: 'compcj/open-webui', version: versions.fork_latest ?? null },
		{ fork: false, repository: 'open-webui/open-webui', version: versions.latest ?? null }
	].map(({ fork, repository, version }) => {
		const installed = parts(versions.current, fork);
		const latest = version && parts(version, fork);
		const differing = installed && latest ? latest.findIndex((n, i) => n !== installed[i]) : -1;
		return {
			fork,
			version,
			available: Boolean(
				installed && latest && differing >= 0 && latest[differing] > installed[differing]
			),
			url: `https://github.com/${repository}/releases${version ? '/tag/v' + encodeURIComponent(version) : ''}`
		};
	});
}
