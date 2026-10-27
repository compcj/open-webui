import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { ToolResultPage } from '$lib/apis/chats';

export type TemporaryIdentity = {
	id: string;
	epoch: string;
	revision: number;
	status?: string;
	socket_generation?: number;
};
export type TemporaryRequestIdentity = TemporaryIdentity & { branch_id: string };
export type RawHistory = { messages: Record<string, any>; currentId: string | null };
export class TemporaryContextError extends Error {
	constructor(
		public status: number,
		public detail: any
	) {
		super(detail?.code ?? detail?.message ?? String(detail ?? status));
	}
}
const root = `${WEBUI_API_BASE_URL}/temporary-contexts`;
async function request(token: string, path: string, method: string, body?: any, keepalive = false) {
	const response = await fetch(`${root}${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		...(body === undefined ? {} : { body: JSON.stringify(body) }),
		keepalive
	});
	const result = await response.json().catch(() => ({}));
	if (!response.ok) throw new TemporaryContextError(response.status, result.detail ?? result);
	return result;
}
const path = (scope: TemporaryIdentity) => `/${encodeURIComponent(scope.id)}`;
export const temporaryContextsApi = {
	create: (token: string, body: any = {}) => request(token, '/', 'POST', body),
	sync: (
		token: string,
		scope: TemporaryIdentity,
		body: { history: RawHistory; params?: any; files?: any[] }
	) =>
		request(token, `${path(scope)}/sync`, 'POST', {
			...body,
			epoch: scope.epoch,
			expected_revision: scope.revision
		}),
	lease: (token: string, scope: TemporaryIdentity, session_id: string, rebind = false) =>
		request(token, `${path(scope)}/lease`, 'POST', { epoch: scope.epoch, session_id, rebind }),
	claimRpc: (
		token: string,
		scope: TemporaryIdentity,
		rpcId: string,
		identity: {
			socket_generation: number;
			task_id: string;
			reply_id: string;
			model_id: string;
			seq: number;
		}
	) =>
		request(token, `${path(scope)}/rpc/${encodeURIComponent(rpcId)}/claim`, 'POST', {
			epoch: scope.epoch,
			...identity
		}),
	state: (token: string, scope: TemporaryIdentity, after_seq = 0) =>
		request(
			token,
			`${path(scope)}/state?${new URLSearchParams({ epoch: scope.epoch, after_seq: String(after_seq) })}`,
			'GET'
		),
	close: (token: string, scope: TemporaryIdentity, keepalive = false) =>
		request(
			token,
			`${path(scope)}?${new URLSearchParams({ epoch: scope.epoch })}`,
			'DELETE',
			undefined,
			keepalive
		),
	usage: (
		token: string,
		scope: TemporaryRequestIdentity,
		model: string,
		model_item?: any,
		session_id?: string
	) =>
		request(token, `${path(scope)}/context-usage`, 'POST', {
			epoch: scope.epoch,
			revision: scope.revision,
			branch_id: scope.branch_id,
			model,
			model_item,
			session_id
		}),
	compact: (
		token: string,
		scope: TemporaryRequestIdentity,
		model: string,
		model_item?: any,
		session_id?: string
	) =>
		request(token, `${path(scope)}/compact`, 'POST', {
			epoch: scope.epoch,
			revision: scope.revision,
			branch_id: scope.branch_id,
			model,
			model_item,
			session_id
		}),
	toolResult: (
		token: string,
		scope: TemporaryIdentity,
		resultId: string,
		branchId: string,
		options: { query?: string; cursor?: string; max_tokens?: number } = {}
	): Promise<ToolResultPage> => {
		const query = new URLSearchParams({ epoch: scope.epoch, branch_id: branchId });
		for (const [key, value] of Object.entries(options))
			if (value !== undefined) query.set(key, String(value));
		return request(
			token,
			`${path(scope)}/tool-results/${encodeURIComponent(resultId)}?${query}`,
			'GET'
		);
	}
};
