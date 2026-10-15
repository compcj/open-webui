import { readFileSync } from 'node:fs';
import { parse } from 'svelte/compiler';
import { ScriptTarget, transpileModule } from 'typescript';
import { afterEach, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('./CallOverlay.svelte', import.meta.url), 'utf8');
const names = new Set(['startRecording', 'stopAudioStream', 'closeAudioContext']);
const declarations = parse(source)
	.instance!.content.body.flatMap((node: any) => node.declarations ?? [])
	.filter((node: any) => names.has(node.id?.name))
	.map((node: any) => `const ${source.slice(node.start, node.end)};`)
	.join('\n');

const createRecorder = (stream: any = null) => {
	const close = vi.fn(async () => {});
	const analyseAudio = vi.fn();
	const body = `
        const { close, analyseAudio } = dependencies;
        let audioContext = { close };
        let audioStream = initialStream;
        let mediaRecorder, audioChunks = [], hasStartedSpeaking = false;
        let $showCallOverlay = true, destroyed = false;
        const stopRecordingCallback = () => {};
        ${declarations}
        return {
            start: startRecording, stop: stopAudioStream,
            destroy: () => { destroyed = true; $showCallOverlay = false; },
            stream: () => audioStream
        };
    `;
	return {
		close,
		analyseAudio,
		...new Function(
			'dependencies',
			'initialStream',
			transpileModule(body, { compilerOptions: { target: ScriptTarget.ES2022 } }).outputText
		)({ close, analyseAudio }, stream)
	};
};

afterEach(() => {
	vi.unstubAllGlobals();
});

it('releases microphone tracks and the audio context when a standard call ends', async () => {
	const stop = vi.fn();
	const recorder = createRecorder({ getAudioTracks: () => [{ stop }] });
	await recorder.stop();
	expect(stop).toHaveBeenCalledOnce();
	expect(recorder.close).toHaveBeenCalledOnce();
	expect(recorder.stream()).toBeNull();
	await recorder.stop();
	expect(recorder.close).toHaveBeenCalledOnce();
});

it('releases a microphone granted after the call has already closed', async () => {
	let release!: (stream: any) => void;
	vi.stubGlobal('navigator', {
		mediaDevices: {
			getUserMedia: () =>
				new Promise((resolve) => {
					release = resolve;
				})
		}
	});
	vi.stubGlobal(
		'MediaRecorder',
		vi.fn(function () {})
	);
	const recorder = createRecorder();
	const starting = recorder.start();
	recorder.destroy();
	const stop = vi.fn();
	release({ getAudioTracks: () => [{ stop }] });
	await starting;
	expect(stop).toHaveBeenCalledOnce();
	expect(recorder.analyseAudio).not.toHaveBeenCalled();
	expect(recorder.stream()).toBeNull();
});
