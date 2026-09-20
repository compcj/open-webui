import { describe, expect, it } from 'vitest';

import { hasPdfSignature } from './terminal-files';

describe('hasPdfSignature', () => {
	it('accepts a PDF signature even when the response MIME is octet-stream', () => {
		const bytes = new TextEncoder().encode('%PDF-1.7\n');
		expect(hasPdfSignature(bytes.buffer)).toBe(true);
	});

	it('allows leading transport whitespace before the PDF signature', () => {
		const bytes = new TextEncoder().encode('\r\n %PDF-2.0');
		expect(hasPdfSignature(bytes.buffer)).toBe(true);
	});

	it('rejects non-PDF bytes', () => {
		const bytes = new TextEncoder().encode('<html>not a pdf</html>');
		expect(hasPdfSignature(bytes.buffer)).toBe(false);
	});
});
