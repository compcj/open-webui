import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	downloadDeliveryFile,
	downloadFilePreview,
	getFileDelivery,
	validateDeliveryUrl
} from './index';

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('validateDeliveryUrl', () => {
	const baseUrl = '/api/v1/terminals/system%2Fmain';

	it('accepts only the signed file endpoint under the expected same-origin terminal', () => {
		expect(
			validateDeliveryUrl(
				'/api/v1/terminals/system%2Fmain/files/download?ref=signed.value',
				baseUrl,
				'download'
			)
		).toBe('http://localhost:3000/api/v1/terminals/system%2Fmain/files/download?ref=signed.value');
	});

	it.each([
		'https://attacker.example/api/v1/terminals/system%2Fmain/files/download?ref=signed',
		'http://attacker@localhost:3000/api/v1/terminals/system%2Fmain/files/download?ref=signed',
		'/api/v1/terminals/other/files/download?ref=signed',
		'/api/v1/terminals/system%2Fmain/files/download?ref=signed&next=https://attacker.example',
		'/api/v1/terminals/system%2Fmain/files/download',
		'/api/v1/terminals/system%2Fmain/files/image?ref=signed'
	])('rejects an untrusted download URL: %s', (url) => {
		expect(validateDeliveryUrl(url, baseUrl, 'download')).toBeNull();
	});
});

describe('getFileDelivery', () => {
	it('requests display metadata with bearer and session headers and validates returned URLs', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(
				JSON.stringify({
					path: '/tmp/chart.svg',
					exists: true,
					owner_id: 'owner-1',
					download_url: '/api/v1/terminals/system/files/download?ref=download-ref',
					image_url: '/api/v1/terminals/system/files/image?ref=image-ref'
				}),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			)
		);
		vi.stubGlobal('fetch', fetchMock);

		const result = await getFileDelivery(
			'/api/v1/terminals/system',
			' token ',
			'/tmp/chart.svg',
			'chat-1'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			'/api/v1/terminals/system/files/display?path=%2Ftmp%2Fchart.svg',
			{
				headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-1' }
			}
		);
		expect(result).toMatchObject({
			path: '/tmp/chart.svg',
			owner_id: 'owner-1',
			download_url: 'http://localhost:3000/api/v1/terminals/system/files/download?ref=download-ref',
			image_url: 'http://localhost:3000/api/v1/terminals/system/files/image?ref=image-ref'
		});
	});

	it('fails closed when the server returns an external delivery URL', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(
					JSON.stringify({
						path: '/tmp/file.txt',
						exists: true,
						owner_id: 'owner-1',
						download_url: 'https://attacker.example/file'
					}),
					{ status: 200 }
				)
			)
		);

		await expect(
			getFileDelivery('/api/v1/terminals/system', 'token', '/tmp/file.txt')
		).resolves.toBeNull();
	});
});

describe('downloadDeliveryFile', () => {
	it('validates the URL before sending bearer credentials and session context', async () => {
		const fetchMock = vi.fn().mockResolvedValue(
			new Response(new Uint8Array([1, 2, 3]), {
				status: 200,
				headers: { 'content-disposition': "attachment; filename*=UTF-8''report.pdf" }
			})
		);
		vi.stubGlobal('fetch', fetchMock);

		const result = await downloadDeliveryFile(
			'/api/v1/terminals/system/files/download?ref=signed',
			'/api/v1/terminals/system',
			'token',
			'chat-1'
		);

		expect(fetchMock).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/terminals/system/files/download?ref=signed',
			{ headers: { Authorization: 'Bearer token', 'X-Session-Id': 'chat-1' } }
		);
		expect(result?.filename).toBe('report.pdf');
	});

	it('does not fetch an invalid URL', async () => {
		const fetchMock = vi.fn();
		vi.stubGlobal('fetch', fetchMock);

		await expect(
			downloadDeliveryFile('https://attacker.example/file', '/api/v1/terminals/system', 'token')
		).resolves.toBeNull();
		expect(fetchMock).not.toHaveBeenCalled();
	});
});

describe('downloadFilePreview', () => {
	it('accepts a PDF response served as application/octet-stream', async () => {
		const pdf = new TextEncoder().encode('%PDF-1.7\n');
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(pdf, {
					status: 200,
					headers: { 'content-type': 'application/octet-stream' }
				})
			)
		);

		const result = await downloadFilePreview(
			'/api/v1/terminals/system',
			'token',
			'/tmp/report.docx',
			'chat-1'
		);

		expect(result?.filename).toBe('report.docx');
	});

	it('rejects non-PDF bytes regardless of the response MIME', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue(
				new Response(new TextEncoder().encode('<html>no</html>'), {
					status: 200,
					headers: { 'content-type': 'application/pdf' }
				})
			)
		);

		await expect(
			downloadFilePreview('/api/v1/terminals/system', 'token', '/tmp/report.docx')
		).resolves.toBeNull();
	});
});
