"""Server-owned temporary effect identities and durable normalized-result settlement.

An execution remains started until normalization (including media/citations) is
stored. Unknown outcomes fail closed; replay never invokes the effect again.
"""

from __future__ import annotations

import asyncio
import copy
import logging

from open_webui.utils.context_scope import temporary_request_store
from open_webui.utils.context_store_errors import ContextConflict, ContextStoreError
from open_webui.utils.context_store_rules import source_digest

log = logging.getLogger(__name__)


class TemporaryToolExecution:
    def __init__(self, store, *, round_id, ordinal, call_id):
        self.store = store
        self.identity = dict(round_id=round_id, ordinal=ordinal, call_id=call_id)
        if store is not None:
            self.identity.update(reply_id=store.scope.reply_id, model_id=store.scope.model_id)
        self.execution_id = None
        self.started = False
        self.resolved = False
        self.replayed = False
        self.result = None
        self.uncertainty_recorded = False

    @classmethod
    def for_request(cls, request, *, round_id, ordinal, call_id):
        return cls(temporary_request_store(request), round_id=round_id, ordinal=ordinal, call_id=call_id)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self.started and not self.resolved:
            reason = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'outcome_unknown'
            await self.uncertain(reason)
        return False

    async def lookup(self, target):
        """Check an existing identity before asking approval again; never start here."""
        if self.store is None:
            return None
        doc = await self.store.executions._read()
        for entry in doc['executions'].values():
            if entry['task_id'] != self.store.scope.task_id or entry['identity'] != self.identity:
                continue
            if entry['target_hash'] != source_digest(target) or entry['status'] != 'resolved':
                raise ContextConflict()
            self.execution_id = entry['id']
            self.result = await self.store.executions.execution_result(entry['id'])
            self.resolved = self.replayed = True
            return copy.deepcopy(self.result)
        return None

    async def start(self, target):
        if self.store is None:
            return None
        scope, adapter = self.store.scope, self.store.executions
        entry, started = await adapter.repository.begin_execution(
            scope.context_id,
            scope.actor_id,
            scope.epoch,
            task_id=scope.task_id,
            identity=self.identity,
            target_hash=source_digest(target),
            expected_fence=scope.execution_fence,
        )
        self.execution_id = entry['id']
        if not started:
            if entry['status'] != 'resolved':
                raise ContextConflict()
            self.result = await adapter.execution_result(entry['id'])
            self.resolved = self.replayed = True
            return copy.deepcopy(self.result)
        self.started = True
        return None

    async def finish(self, result):
        if self.store is None or not self.started or self.resolved:
            return
        try:
            await self.store.executions.save_execution_result(self.execution_id, result)
        except BaseException:
            await self.uncertain('result_persistence_failed')
            raise
        self.result = copy.deepcopy(result)
        self.resolved = True

    async def uncertain(self, reason='outcome_unknown'):
        if self.store is None or not self.started or self.resolved or self.uncertainty_recorded:
            return
        scope = self.store.scope
        try:
            await self.store.executions.repository.mark_uncertain(
                scope.context_id,
                scope.actor_id,
                scope.epoch,
                task_id=scope.task_id,
                execution_id=self.execution_id,
                reason_code=reason,
            )
            self.uncertainty_recorded = True
        except ContextStoreError:
            # The durable started record remains blocking even during an outage.
            log.warning('Unable to settle temporary execution uncertainty')

    def check_direct_result(self, result):
        if self.store is not None and isinstance(result, dict) and result.get('error'):
            # A missing callback cannot prove whether the browser effect ran.
            raise ContextConflict()


async def commit_temporary_results(request, output, executions, *, sidecars=None):
    store = temporary_request_store(request)
    if store is None:
        return False
    await store.results.commit_raw_results(store.scope.reply_id, executions, output, sidecars, settlement=True)
    return True
