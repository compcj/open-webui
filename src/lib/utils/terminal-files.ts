import { normalizeDocumentTargetPage } from './documentPreview';

export type TerminalFileDisplay = {
	chatId: string | null;
	terminalId: string;
	path: string;
	page: number | null;
	refreshOnly?: boolean;
};

const fileDisplayListeners = new Set<(request: TerminalFileDisplay) => void>();

export const onTerminalFileDisplay = (listener: (request: TerminalFileDisplay) => void) => {
	fileDisplayListeners.add(listener);
	return () => fileDisplayListeners.delete(listener);
};

export const displayTerminalFile = (
	request: Omit<TerminalFileDisplay, 'page'> & { page?: unknown }
) => {
	if (!request.path || !request.terminalId) return;
	const display = {
		...request,
		chatId: request.chatId || null,
		page: normalizeDocumentTargetPage(request.page)
	};
	for (const listener of fileDisplayListeners) listener(display);
};

export const hasPdfSignature = (data: ArrayBuffer): boolean => {
	const bytes = new Uint8Array(data, 0, Math.min(data.byteLength, 1024));
	let offset = 0;
	while (offset < bytes.length && [9, 10, 12, 13, 32].includes(bytes[offset])) offset += 1;
	return (
		bytes[offset] === 0x25 &&
		bytes[offset + 1] === 0x50 &&
		bytes[offset + 2] === 0x44 &&
		bytes[offset + 3] === 0x46 &&
		bytes[offset + 4] === 0x2d
	);
};
