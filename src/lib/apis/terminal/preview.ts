import { terminalRequest, type TerminalConnection } from './index';

type PreviewTarget =
	| { kind: 'browser'; session_id: string }
	| { kind: 'port'; port: number; path?: string; session_id?: string };

type PreviewTicket = { url: string; expires_at: number };

export function previewTicketToken(url: URL) {
	return /^\/p\/([A-Za-z0-9_-]+)(?:\/|$)/.exec(url.pathname)?.[1] ?? '';
}

export function validatePreviewTicket(
	ticket: PreviewTicket,
	previewBase: string,
	appOrigin: string,
	now = Date.now() / 1000
): PreviewTicket {
	const url = new URL(ticket.url);
	if (
		url.origin !== new URL(previewBase).origin ||
		url.hostname === new URL(appOrigin).hostname ||
		!['https:', 'http:'].includes(url.protocol) ||
		url.username ||
		url.password ||
		!previewTicketToken(url) ||
		!Number.isFinite(ticket.expires_at) ||
		ticket.expires_at <= now
	) {
		throw new Error('Invalid terminal preview ticket');
	}
	return ticket;
}

export async function requestPreviewTicket(
	connection: TerminalConnection,
	chatId: string | null,
	target: PreviewTarget,
	previewBase: string,
	signal: AbortSignal,
	previousUrl?: string
) {
	if (!connection.system || !previewBase) throw new Error('Terminal previews are disabled');
	const credential = connection.key;
	const ticket = await terminalRequest<PreviewTicket>(connection, chatId, '/preview-tickets', {
		method: 'POST',
		signal,
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			...target,
			chat_id: chatId,
			...(typeof credential !== 'string' ? { temporary_context: credential.temporaryContext } : {}),
			...(previousUrl ? { previous_url: previousUrl } : {})
		})
	});
	return validatePreviewTicket(ticket, previewBase, window.location.origin);
}
