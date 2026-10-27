import {
	temporaryContextsApi,
	type TemporaryIdentity,
	type TemporaryRequestIdentity,
	type RawHistory
} from '$lib/apis/temporary-contexts';

type Actor = { id: string; token: string };
type Options = {
	api?: typeof temporaryContextsApi;
	actor: () => Actor;
	session: () => string;
	applyEvent: (event: any, callback?: any) => any;
	applyState: (state: any) => void;
	cancelInteractions: () => void;
	onError: (error: any) => void;
	interval?: (callback: () => void, milliseconds: number) => any;
	clearInterval?: (timer: any) => void;
	delay?: (milliseconds: number) => Promise<void>;
};
/** Owned by one mounted Chat page; never put this controller in account stores. */
export class TemporaryContextController {
	identity: TemporaryIdentity | null = null;
	private owner: Actor | null = null;
	private generation = 0;
	private seq = 0;
	private bound = false;
	private replies = new Map<string, { model: string; task?: string; needsTaskRefresh?: boolean }>();
	private rpcClaims = new Map<string, { expires: number; authorized: boolean }>();
	private compacting: { branch: string; model: string } | null = null;
	private timer: any;
	private creating: Promise<void> | null = null;
	private rebuildAllowed = false;
	private api: typeof temporaryContextsApi;
	constructor(private options: Options) {
		this.api = options.api ?? temporaryContextsApi;
	}
	get chatId() {
		return this.identity ? `temporary:${this.identity.id}` : null;
	}
	capture() {
		const generation = this.generation;
		const actor = { ...this.options.actor() };
		return () =>
			generation === this.generation &&
			actor.id === this.options.actor().id &&
			actor.token === this.options.actor().token;
	}
	private assertOwner() {
		if (
			!this.owner ||
			this.owner.id !== this.options.actor().id ||
			this.owner.token !== this.options.actor().token
		)
			throw new Error('Temporary context owner changed.');
	}
	private assert(current: () => boolean) {
		if (!current()) throw new Error('Temporary context changed; retry from the current chat.');
	}
	private adopt(next: TemporaryIdentity) {
		if (
			!this.identity ||
			(next.id === this.identity.id &&
				next.epoch === this.identity.epoch &&
				next.revision >= this.identity.revision &&
				(next.socket_generation ?? this.identity.socket_generation ?? 0) >=
					(this.identity.socket_generation ?? 0))
		)
			this.identity = {
				id: next.id,
				epoch: next.epoch,
				revision: next.revision,
				status: next.status,
				socket_generation: next.socket_generation ?? this.identity?.socket_generation
			};
	}
	async ensure() {
		if (
			this.owner &&
			(this.owner.id !== this.options.actor().id || this.owner.token !== this.options.actor().token)
		)
			this.detach();
		if (this.identity) return this.identity;
		if (!this.creating) {
			const current = this.capture();
			const owner = { ...this.options.actor() };
			const pending = (async () => {
				const scope = await this.api.create(owner.token);
				if (!current()) {
					void this.api.close(owner.token, scope).catch(() => {});
					this.assert(current);
				}
				this.owner = owner;
				this.identity = scope;
				this.startLease();
			})().finally(() => {
				if (this.creating === pending) this.creating = null;
			});
			this.creating = pending;
		}
		await this.creating;
		return this.identity!;
	}
	private startLease() {
		this.clearTimer();
		this.timer = (this.options.interval ?? setInterval)(() => {
			void this.lease().catch(this.options.onError);
		}, 60000);
	}
	private clearTimer() {
		if (this.timer !== undefined) (this.options.clearInterval ?? clearInterval)(this.timer);
		this.timer = undefined;
	}
	async lease(rebind = false) {
		if (!this.identity || !this.owner) return;
		this.assertOwner();
		const current = this.capture();
		const scope = { ...this.identity };
		const next = await this.api.lease(this.owner.token, scope, this.options.session(), rebind);
		this.assert(current);
		this.adopt(next);
		this.bound = true;
	}
	async prepare(
		history: RawHistory,
		branchId: string,
		params?: any,
		files?: any[]
	): Promise<TemporaryRequestIdentity> {
		const current = this.capture();
		await this.ensure();
		this.assert(current);
		try {
			const next = await this.api.sync(
				this.owner!.token,
				{ ...this.identity! },
				{ history: structuredClone(history), params, files }
			);
			this.assert(current);
			this.adopt(next);
			for (const message of Object.values(history.messages))
				if (message.role === 'assistant') {
					const existing = this.replies.get(message.id);
					this.replies.set(message.id, {
						model: message.model,
						...(existing?.model === message.model
							? { task: existing?.task, needsTaskRefresh: !!existing?.task }
							: {})
					});
				}
			await this.lease();
			this.assert(current);
			return this.requestIdentity(branchId);
		} catch (error) {
			await this.handleError(error, current);
			throw error;
		}
	}
	requestIdentity(branchId: string): TemporaryRequestIdentity {
		if (!this.identity) throw new Error('Temporary context is unavailable.');
		return {
			id: this.identity.id,
			epoch: this.identity.epoch,
			revision: this.identity.revision,
			branch_id: branchId
		};
	}
	async handleError(error: any, current = this.capture()) {
		if (!current()) return;
		if (error?.status === 409) await this.reconcile().catch(this.options.onError);
		if (error?.status === 410) this.rebuildAllowed = error?.detail?.rebuild_allowed === true;
	}
	async rebuild() {
		this.assertOwner();
		if (!this.rebuildAllowed || !this.identity || !this.owner)
			throw new Error('The previous execution has not been confirmed stopped.');
		const current = this.capture();
		const replaces = { id: this.identity.id, epoch: this.identity.epoch };
		const next = await this.api.create(this.owner.token, { replaces });
		this.assert(current);
		this.generation++;
		this.seq = 0;
		this.replies.clear();
		this.identity = next;
		this.rebuildAllowed = false;
		this.startLease();
	}
	private activeReplyTask(state: any, replyId: string, modelId: string) {
		const active = (state?.tasks ?? []).filter(
			(task: any) =>
				task.reply_id === replyId && task.model_id === modelId && task.status !== 'done'
		);
		return active.length === 1 ? active[0].task_id : undefined;
	}
	private async receiveRpc(
		event: any,
		callback: any,
		apply?: (event: any, callback: any, current: () => boolean) => any
	) {
		const rpcId = event.rpc_id;
		const socketGeneration = this.identity?.socket_generation;
		if (
			typeof rpcId !== 'string' ||
			!rpcId ||
			rpcId.length > 128 ||
			!Number.isSafeInteger(socketGeneration) ||
			event.socket_generation !== socketGeneration ||
			!Number.isSafeInteger(event.seq) ||
			event.seq < 1
		)
			return false;
		const manual =
			this.compacting &&
			event.data?.type === 'request:chat:completion' &&
			this.compacting.branch === event.message_id &&
			this.compacting.model === event.model_id
				? this.compacting
				: null;
		const reply = this.replies.get(event.message_id);
		if (!manual && (!reply || reply.model !== event.model_id)) return false;
		for (const [id, claim] of this.rpcClaims)
			if (claim.expires <= Date.now()) this.rpcClaims.delete(id);
		if (this.rpcClaims.has(rpcId) || this.rpcClaims.size >= 4096) return false;
		// Reserve synchronously: two callbacks cannot pass the same await boundary.
		const claim = { expires: Date.now() + 3600000, authorized: false };
		this.rpcClaims.set(rpcId, claim);
		const current = this.capture();
		const stillCurrent = () =>
			current() &&
			this.bound &&
			this.identity?.socket_generation === socketGeneration &&
			(!manual || this.compacting === manual);
		try {
			const state = await this.reconcile();
			if (!stillCurrent()) return false;
			if (
				!manual &&
				(reply?.needsTaskRefresh || (reply?.task && reply.task !== event.task_id)) &&
				this.activeReplyTask(state, event.message_id, event.model_id) !== event.task_id
			)
				return false;
			const refreshedReply = this.replies.get(event.message_id);
			if (!manual && refreshedReply?.task && refreshedReply.task !== event.task_id) return false;
			const pending = state?.pending_rpcs?.find((item: any) => item.rpc_id === rpcId);
			if (
				!pending ||
				pending.claimed ||
				pending.socket_generation !== socketGeneration ||
				pending.task_id !== event.task_id ||
				pending.reply_id !== event.message_id ||
				pending.model_id !== event.model_id ||
				pending.seq !== event.seq
			)
				return false;
			const started = Date.now();
			const accepted = await this.api.claimRpc(this.owner!.token, { ...this.identity! }, rpcId, {
				socket_generation: socketGeneration!,
				task_id: event.task_id,
				reply_id: event.message_id,
				model_id: event.model_id,
				seq: event.seq
			});
			if (
				!stillCurrent() ||
				accepted?.claimed !== true ||
				accepted.rpc_id !== rpcId ||
				accepted.socket_generation !== socketGeneration ||
				!Number.isFinite(accepted.remaining_ms) ||
				accepted.remaining_ms <= 0
			)
				return false;
			claim.expires = started + Math.min(accepted.remaining_ms, 3600000);
			claim.authorized = true;
			if (refreshedReply && !manual) {
				refreshedReply.task = event.task_id;
				refreshedReply.needsTaskRefresh = false;
			}
			const valid = () =>
				stillCurrent() &&
				claim.authorized &&
				(manual ||
					(!this.replies.get(event.message_id)?.needsTaskRefresh &&
						this.replies.get(event.message_id)?.task === event.task_id)) &&
				this.rpcClaims.get(rpcId) === claim &&
				Date.now() < claim.expires;
			if (!valid()) return false;
			this.seq = Math.max(this.seq, event.seq);
			this.adopt(event.temporary_context);
			if (apply) await apply(event, callback, valid);
			else
				await this.options.applyEvent(
					event,
					typeof callback === 'function'
						? (...args: any[]) => {
								if (valid()) callback(...args);
							}
						: callback
				);
			return true;
		} catch (error: any) {
			claim.authorized = false;
			if (current() && error?.status !== 409 && error?.status !== 410) this.options.onError(error);
			return false;
		}
	}
	async receive(
		event: any,
		callback?: any,
		apply?: (event: any, callback: any, current: () => boolean) => any
	) {
		const scope = event?.temporary_context;
		if (
			!this.identity ||
			!this.bound ||
			!this.owner ||
			this.owner.id !== this.options.actor().id ||
			this.owner.token !== this.options.actor().token ||
			scope?.id !== this.identity.id ||
			scope?.epoch !== this.identity.epoch ||
			event.chat_id !== this.chatId
		)
			return false;
		if (event.rpc_id !== undefined) return this.receiveRpc(event, callback, apply);
		if (
			isTemporaryContextRpc(event) ||
			['confirmation', 'input', 'request:user_input', 'request:elicitation'].includes(
				event.data?.type
			)
		)
			return false;
		const reply = this.replies.get(event.message_id);
		if (
			!reply ||
			event.model_id !== reply.model ||
			!Number.isSafeInteger(event.seq) ||
			event.seq <= this.seq
		)
			return false;
		const current = this.capture();
		if (reply.needsTaskRefresh || (reply.task && event.task_id !== reply.task)) {
			const state = await this.reconcile();
			if (
				!current() ||
				!this.bound ||
				this.activeReplyTask(state, event.message_id, event.model_id) !== event.task_id
			)
				return false;
			// The canonical snapshot already includes notifications through its watermark.
			if (event.seq <= this.seq) return false;
		}
		if (event.seq !== this.seq + 1) {
			await this.reconcile();
			if (!current() || event.seq <= this.seq || event.seq !== this.seq + 1) return false;
		}
		const acceptedReply = this.replies.get(event.message_id);
		if (
			!acceptedReply ||
			acceptedReply.needsTaskRefresh ||
			(acceptedReply.task && acceptedReply.task !== event.task_id)
		)
			return false;
		acceptedReply.task = event.task_id;
		this.seq = event.seq;
		this.adopt(scope);
		if (apply) await apply(event, callback, current);
		else await this.options.applyEvent(event, callback);
		return true;
	}
	async reconcile() {
		if (!this.identity || !this.owner) return;
		this.assertOwner();
		const current = this.capture();
		const state = await this.api.state(this.owner.token, { ...this.identity }, this.seq);
		if (
			!current() ||
			state.id !== this.identity?.id ||
			state.epoch !== this.identity?.epoch ||
			state.event_seq < this.seq ||
			state.revision < this.identity.revision ||
			(state.socket_generation ?? 0) < (this.identity.socket_generation ?? 0)
		)
			return;
		this.adopt(state);
		this.seq = state.event_seq;
		for (const message of Object.values(state.history?.messages ?? {}) as any[])
			if (message.role === 'assistant') {
				const previous = this.replies.get(message.id);
				this.replies.set(message.id, {
					model: message.model,
					task: previous?.task,
					needsTaskRefresh: previous?.needsTaskRefresh
				});
			}
		for (const [id, reply] of this.replies) {
			const active = this.activeReplyTask(state, id, reply.model);
			if (active) {
				reply.task = active;
				reply.needsTaskRefresh = false;
			} else if (!reply.task) {
				const done = (state.tasks ?? []).filter(
					(task: any) =>
						task.reply_id === id && task.model_id === reply.model && task.status === 'done'
				);
				if (done.length === 1) reply.task = done[0].task_id;
			}
		}
		for (const [id, claim] of this.rpcClaims) {
			if (
				claim.authorized &&
				!(state.pending_rpcs ?? []).some(
					(item: any) =>
						item.rpc_id === id && item.socket_generation === this.identity?.socket_generation
				)
			)
				claim.authorized = false;
		}
		this.options.applyState(state);
		return state;
	}
	async waitForSettled(attempts = 5) {
		const current = this.capture();
		for (let attempt = 0; attempt < attempts && current(); attempt++) {
			const state = await this.reconcile();
			if (!current() || state?.uncertain) return false;
			if (
				state?.uncertain === false &&
				Array.isArray(state.tasks) &&
				state.tasks.every((task: any) => task.status === 'done')
			)
				return true;
			if (attempt + 1 < attempts)
				await (
					this.options.delay ?? ((ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)))
				)(500);
		}
		return false;
	}
	disconnect() {
		this.bound = false;
		this.rpcClaims.clear();
		this.compacting = null;
		this.generation++;
		this.options.cancelInteractions();
		this.clearTimer();
	}
	async rebind() {
		if (!this.identity) return;
		this.disconnect();
		await this.lease(true);
		await this.reconcile();
		this.startLease();
	}
	async close(keepalive = false) {
		if (!this.identity || !this.owner) return true;
		this.disconnect();
		const current = this.capture();
		const result = await this.api.close(this.owner.token, { ...this.identity }, keepalive);
		if (!current()) return false;
		this.adopt(result);
		if (result.status !== 'closed_confirmed') return false;
		this.identity = null;
		this.owner = null;
		this.seq = 0;
		this.replies.clear();
		return true;
	}
	detach(keepalive = false) {
		const scope = this.identity;
		const owner = this.owner;
		this.disconnect();
		this.identity = null;
		this.owner = null;
		this.creating = null;
		this.seq = 0;
		this.replies.clear();
		this.rebuildAllowed = false;
		if (scope && owner) void this.api.close(owner.token, scope, keepalive).catch(() => {});
	}
	async usage(branch: string, model: string, item?: any) {
		this.assertOwner();
		const current = this.capture();
		const result = await this.api.usage(
			this.owner!.token,
			this.requestIdentity(branch),
			model,
			item,
			this.options.session()
		);
		this.assert(current);
		return result;
	}
	async compact(branch: string, model: string, item?: any) {
		this.assertOwner();
		if (this.compacting) throw new Error('Temporary context compaction is already running.');
		const current = this.capture();
		const expected = { branch, model };
		this.compacting = expected;
		try {
			const result = await this.api.compact(
				this.owner!.token,
				this.requestIdentity(branch),
				model,
				item,
				this.options.session()
			);
			this.assert(current);
			return result;
		} finally {
			if (this.compacting === expected) this.compacting = null;
		}
	}
	async toolResult(resultId: string, branch: string, options?: any) {
		this.assertOwner();
		const current = this.capture();
		const result = await this.api.toolResult(
			this.owner!.token,
			this.identity!,
			resultId,
			branch,
			options
		);
		this.assert(current);
		return result;
	}
}

export function temporaryBranch(
	history: RawHistory,
	messageId: string | null = history.currentId
): string {
	const message = messageId ? history.messages[messageId] : null;
	return message?.role === 'user' ? message.id : message?.parentId;
}

export function reconcileTemporaryHistory(local: RawHistory, remote: RawHistory): RawHistory {
	const messages = { ...local.messages };
	for (const [id, message] of Object.entries(remote.messages)) {
		// Preserve drafts and the user's selected branch; canonical assistant output is server-owned.
		if (!messages[id] || message.role === 'assistant') {
			const children = [
				...new Set([...(message.childrenIds ?? []), ...(messages[id]?.childrenIds ?? [])])
			];
			messages[id] = {
				...structuredClone(message),
				...(children.length ? { childrenIds: children } : {})
			};
		}
	}
	return { messages, currentId: local.currentId ?? remote.currentId };
}

const temporaryRpcTypes = new Set([
	'execute:python',
	'execute:tool',
	'request:terminal',
	'request:terminal:state',
	'request:chat:completion'
]);
export const isTemporaryContextRpc = (event: any) =>
	!!event?.temporary_context && temporaryRpcTypes.has(event?.data?.type);
