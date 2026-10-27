"""V2 storage ports over the bounded temporary repository, with no SQL access."""

from __future__ import annotations

import copy
import time
import uuid

from open_webui.utils.context_scope import ContextScope, ContextStore
from open_webui.utils.context_store_errors import ContextConflict, ContextGone, ContextNotFound
from open_webui.utils.context_store_rules import (
    SOURCE_KEYS,
    branch_chain,
    effective_selections,
    latest_valid_accounting,
    pending_selection_transition,
    prepare_selection_transition,
    resolved_selection,
    source_state,
    source_digest,
)
from open_webui.utils.tool_result_sources import output_descriptors


def _table(doc, name):
    return doc['data'].setdefault(name, {})


def _snapshot(doc):
    return doc['data'].get('snapshot')


def _source_key(message_id, output_id):
    # JSON list avoids ambiguities if an imported identifier contains a separator.
    import json

    return json.dumps([message_id, output_id], separators=(',', ':'))


def _raw_binding(output, sidecars, output_id):
    call = None
    item = None
    for value in output:
        if value.get('id') == output_id:
            item = value
            break
    if item is not None:
        for value in output:
            if value is item:
                break
            if value.get('type') == 'function_call' and value.get('call_id') == item.get('call_id'):
                call = value
    else:
        item = next((value for value in sidecars or [] if value.get('id') == output_id), None)
    if item is None:
        return None, None
    return item, source_digest(
        {
            'item': item,
            'call': None
            if call is None
            else {key: value for key, value in call.items() if key not in ('status', 'approved')},
        }
    )


class TemporaryContextAdapter:
    def __init__(self, repository, scope: ContextScope):
        if scope.kind != 'temporary' or not scope.epoch:
            raise ValueError('Temporary context storage requires an authenticated epoch')
        self.repository, self.scope = repository, scope

    def bundle(self) -> ContextStore:
        return ContextStore(self.scope, self, self, self, self, self, self)

    def _key(self, key):
        if key != self.scope.store_key:
            raise ContextNotFound()

    def _task(self, doc, *, settlement=False, allow_done=False):
        scope = self.scope
        task = doc['tasks'].get(scope.task_id) if scope.task_id else None
        if scope.task_id and (
            not task
            or task['reply_id'] != scope.reply_id
            or task['model_id'] != scope.model_id
            or task['fence'] != scope.execution_fence
            or (task['status'] == 'done' and not allow_done)
        ):
            raise ContextConflict()
        if not settlement and scope.execution_fence is not None and doc['fence'] != scope.execution_fence:
            raise ContextGone()
        return task

    async def _read(self, *, settlement=False):
        doc = await self.repository.read(
            self.scope.context_id, self.scope.actor_id, self.scope.epoch, allow_closed=settlement
        )
        self._task(doc, settlement=settlement, allow_done=settlement)
        return doc

    async def _write(self, transition, *, source_changed=False, settlement=False):
        def checked(doc, now):
            self._task(doc, settlement=settlement)
            return transition(doc, now)

        return await self.repository.transact(
            self.scope.context_id,
            self.scope.actor_id,
            self.scope.epoch,
            checked,
            expected_fence=self.scope.execution_fence,
            settlement_task_id=self.scope.task_id if settlement else None,
            source_changed=source_changed,
        )

    async def ensure_active(self):
        await self._read()

    async def read_snapshot(self, chat_id=None):
        if chat_id is not None:
            self._key(chat_id)
        doc = await self._read()
        snapshot = copy.deepcopy(_snapshot(doc))
        if snapshot is None:
            raise ContextGone()
        if self.scope.branch_id in snapshot['messages_map']:
            snapshot['current_id'] = self.scope.branch_id
        return snapshot

    async def latest_version(self, chat_id, model_id):
        self._key(chat_id)
        doc = await self._read()
        return max(
            (row['version'] for row in doc['data'].get('checkpoints', {}).values() if row['model_id'] == model_id),
            default=0,
        )

    async def list_checkpoints(self, chat_id, model_id):
        self._key(chat_id)
        doc = await self._read()
        return sorted(
            [copy.deepcopy(row) for row in doc['data'].get('checkpoints', {}).values() if row['model_id'] == model_id],
            key=lambda row: row['version'],
            reverse=True,
        )

    async def save_checkpoint(self, data, *, expected_version):
        from open_webui.utils.context_compaction_v2 import digest, source_refs_match

        self._key(data['chat_id'])

        def write(doc, now):
            snapshot = _snapshot(doc)
            if (
                not snapshot
                or not source_refs_match(data['source_refs'], snapshot['messages_map'])
                or digest(data['source_refs']) != data['source_fingerprint']
                or data['model_id'] != self.scope.model_id
            ):
                return None
            chain = branch_chain(snapshot['messages_map'], self.scope.branch_id)
            if not chain or data['branch_anchor_id'] not in {m['id'] for m in chain}:
                return None
            table = _table(doc, 'checkpoints')
            version = max((row['version'] for row in table.values() if row['model_id'] == data['model_id']), default=0)
            if version != expected_version:
                return None
            row = {**copy.deepcopy(data), 'id': str(uuid.uuid4()), 'version': version + 1, 'created_at': time.time_ns()}
            table[row['id']] = row
            return row

        _, result = await self._write(write)
        return result

    async def save_result(self, chat_id, descriptor, *, branch_id):
        self._key(chat_id)

        def write(doc, now):
            if any(
                not isinstance(descriptor.get(key), str) or not descriptor[key] for key in SOURCE_KEYS
            ) or not isinstance(descriptor.get('text'), str):
                return None
            state = source_state(_snapshot(doc), descriptor, branch_id)
            if state is None:
                return None
            table = _table(doc, 'results')
            row = next(
                (
                    item
                    for item in table.values()
                    if item['message_id'] == descriptor['message_id']
                    and item['output_id'] == descriptor['output_id']
                    and (item['source_hash'], item['source_fingerprint']) == state
                ),
                None,
            )
            if row is None:
                row = {key: descriptor[key] for key in (*SOURCE_KEYS, 'text')}
                row.update(
                    id=str(uuid.uuid4()),
                    chat_id=chat_id,
                    source_hash=state[0],
                    source_fingerprint=state[1],
                    created_at=time.time_ns(),
                )
                table[row['id']] = row
            return row

        _, result = await self._write(write)
        return result

    @staticmethod
    def _valid_result(doc, result_id, branch_id):
        row = doc['data'].get('results', {}).get(result_id)
        if row and source_state(_snapshot(doc), row, branch_id) == (row['source_hash'], row['source_fingerprint']):
            return row
        return None

    async def get_result(self, result_id, *, branch_id):
        return copy.deepcopy(self._valid_result(await self._read(), result_id, branch_id))

    async def get_context(self, result_id, *, model_id, branch_id, task_hash, policy_hash, budget):
        doc = await self._read()
        if not self._valid_result(doc, result_id, branch_id):
            return None
        key = dict(
            result_id=result_id,
            model_id=model_id,
            branch_id=branch_id,
            task_hash=task_hash,
            policy_hash=policy_hash,
            budget=budget,
        )
        return next(
            (
                copy.deepcopy(row)
                for row in doc['data'].get('contexts', {}).values()
                if all(row.get(name) == value for name, value in key.items())
            ),
            None,
        )

    async def save_context(
        self,
        result_id,
        *,
        model_id,
        branch_id,
        task_hash,
        policy_hash,
        budget,
        text,
        method,
        original_tokens,
        processed_tokens,
        source_scope=None,
    ):
        if (
            source_scope not in {None, 'saved_history_v1'}
            or type(budget) is not int
            or budget < 0
            or method not in {'summary', 'excerpt', 'original', 'read'}
            or not isinstance(text, str)
            or type(processed_tokens) is not int
            or type(original_tokens) is not int
            or not 0 <= processed_tokens <= budget
            or original_tokens < 0
        ):
            return None
        key = dict(
            result_id=result_id,
            model_id=model_id,
            branch_id=branch_id,
            task_hash=task_hash,
            policy_hash=policy_hash,
            budget=budget,
        )

        def write(doc, now):
            raw = self._valid_result(doc, result_id, branch_id)
            if raw is None or model_id != self.scope.model_id:
                return None
            table = _table(doc, 'contexts')
            row = next((item for item in table.values() if all(item.get(k) == v for k, v in key.items())), None)
            if row is None:
                row = {
                    **key,
                    'id': str(uuid.uuid4()),
                    'chat_id': self.scope.store_key,
                    'text': text,
                    'method': method,
                    'source_scope': source_scope,
                    'original_tokens': original_tokens,
                    'processed_tokens': processed_tokens,
                    'created_at': time.time_ns(),
                }
                table[row['id']] = row
            return row

        _, result = await self._write(write)
        return result

    async def list_contexts(self, chat_id, model_id, branch_id):
        self._key(chat_id)
        doc = await self._read()
        result = []
        for row in doc['data'].get('contexts', {}).values():
            if row['model_id'] != model_id or row['branch_id'] != branch_id:
                continue
            raw = self._valid_result(doc, row['result_id'], branch_id)
            if raw:
                result.append(
                    {
                        **copy.deepcopy(row),
                        **{
                            key: raw[key]
                            for key in ('message_id', 'output_id', 'source_hash', 'name', 'call_id', 'batch_id', 'kind')
                        },
                    }
                )
        return result

    def _selection_inputs(self, doc):
        return (
            _snapshot(doc),
            list(doc['data'].get('selections', {}).values()),
            list(doc['data'].get('contexts', {}).values()),
            list(doc['data'].get('results', {}).values()),
        )

    async def register_pending(self, chat_id, model_id, descriptors, *, branch_id):
        self._key(chat_id)

        def write(doc, now):
            if not isinstance(descriptors, list) or model_id != self.scope.model_id or not self.scope.task_id:
                return False
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    return False
                admission = (
                    doc['data']
                    .get('result_admissions', {})
                    .get(_source_key(descriptor.get('message_id'), descriptor.get('output_id')))
                )
                execution = doc['executions'].get((admission or {}).get('execution_id'))
                if (
                    not admission
                    or not execution
                    or execution['status'] != 'resolved'
                    or execution['task_id'] != self.scope.task_id
                    or source_state(_snapshot(doc), descriptor, branch_id)
                    != (admission['source_hash'], admission['source_fingerprint'])
                ):
                    return False
            snapshot, selections, contexts, raw = self._selection_inputs(doc)
            writes = pending_selection_transition(
                snapshot, chat_id, model_id, branch_id, selections, contexts, raw, descriptors
            )
            if writes is None:
                return False
            _table(doc, 'selections').update({row['id']: row for row in writes})
            return True

        _, result = await self._write(write)
        return result

    async def list_selections(self, chat_id, model_id, branch_id):
        self._key(chat_id)
        doc = await self._read()
        snapshot, selections, contexts, _ = self._selection_inputs(doc)
        effective = effective_selections(snapshot, chat_id, model_id, branch_id, selections, contexts)
        return [resolved_selection(*item) for item in effective.values()]

    async def prepare_selections(
        self, chat_id, model_id, *, branch_id, choices, batch_reports, validate=None, expected_sources=()
    ):
        self._key(chat_id)

        def write(doc, now):
            if model_id != self.scope.model_id:
                return None
            snapshot, selections, contexts, raw = self._selection_inputs(doc)
            result = prepare_selection_transition(
                snapshot,
                chat_id,
                model_id,
                branch_id,
                selections,
                contexts,
                raw,
                choices=choices,
                batch_reports=batch_reports,
                validate=validate,
                expected_sources=expected_sources,
            )
            if result is None:
                return None
            _table(doc, 'selections').update({row['id']: row for row in result['writes']})
            return result['canonical']

        _, result = await self._write(write)
        return result

    async def latest_accounting(self, chat_id, model_id, branch_id):
        self._key(chat_id)
        doc = await self._read()
        snapshot, selections, contexts, _ = self._selection_inputs(doc)
        effective = effective_selections(snapshot, chat_id, model_id, branch_id, selections, contexts)
        return latest_valid_accounting(snapshot, effective, chat_id, model_id, branch_id)

    def _reply(self, doc, reply_id, *, allow_done=False):
        if not self.scope.task_id or reply_id != self.scope.reply_id:
            raise ContextConflict()
        self._task(doc, settlement=True, allow_done=allow_done)
        snapshot = _snapshot(doc)
        message = (snapshot or {}).get('messages_map', {}).get(reply_id)
        if not message or message.get('role') != 'assistant' or message.get('model') != self.scope.model_id:
            raise ContextConflict()
        return message

    async def put_reply_patch(self, reply_id, patch, *, settlement=False):
        def write(doc, now):
            message = self._reply(doc, reply_id)
            if any(key in patch for key in ('id', 'parentId', 'parent_id', 'role', 'model', 'user_id')):
                raise ContextConflict()
            for key, value in patch.items():
                if key == 'meta' and isinstance(value, dict):
                    message['meta'] = {**message.get('meta', {}), **copy.deepcopy(value)}
                else:
                    message[key] = copy.deepcopy(value)
            return message

        doc, result = await self._write(write, source_changed=True, settlement=settlement)
        return doc, result

    async def save_execution_result(self, execution_id, result):
        def write(doc, now):
            self._reply(doc, self.scope.reply_id, allow_done=True)
            _table(doc, 'resolved_outputs')[execution_id] = copy.deepcopy(result)

        return await self.repository.resolve_execution(
            self.scope.context_id,
            self.scope.actor_id,
            self.scope.epoch,
            task_id=self.scope.task_id,
            execution_id=execution_id,
            raw_transition=write,
            requires_publication=True,
        )

    async def execution_result(self, execution_id):
        doc = await self._read(settlement=True)
        execution = doc['executions'].get(execution_id)
        if not execution or execution['task_id'] != self.scope.task_id or execution['status'] != 'resolved':
            raise ContextConflict()
        result = doc['data'].get('resolved_outputs', {}).get(execution_id)
        if result is not None:
            return copy.deepcopy(result)
        # A result moved into canonical raw history stays replayable without a
        # second retained copy. Only this server-owned admission binds it.
        snapshot = _snapshot(doc)
        for key, admission in doc['data'].get('result_admissions', {}).items():
            if admission['execution_id'] != execution_id:
                continue
            message_id, output_id = __import__('json').loads(key)
            message = snapshot['messages_map'].get(message_id, {})
            for item in message.get('output', []):
                if item.get('id') == output_id:
                    from open_webui.utils.tool_result_sources import output_text

                    return {
                        'content': output_text(item),
                        **copy.deepcopy(admission.get('result_projection') or {}),
                        'output_item': copy.deepcopy(item),
                    }
            for item in message.get('tool_results', []):
                if item.get('id') == output_id:
                    return {
                        'content': item['text'],
                        **copy.deepcopy(admission.get('result_projection') or {}),
                        'output_item': copy.deepcopy(item),
                    }
        raise ContextConflict()

    async def commit_raw_results(self, reply_id, execution_ids, output, sidecars=None, *, settlement=False):
        def write(doc, now):
            message = self._reply(doc, reply_id, allow_done=True)
            # Every previously admitted result stays fixed, including batches
            # omitted from this incremental execution mapping.
            for key, admission in doc['data'].get('result_admissions', {}).items():
                source_message, source_output = __import__('json').loads(key)
                if source_message == reply_id:
                    _, binding = _raw_binding(
                        output, sidecars if sidecars is not None else message.get('tool_results'), source_output
                    )
                    if binding != admission.get('raw_binding'):
                        raise ContextConflict()
            message['output'] = copy.deepcopy(output)
            if sidecars is not None:
                message['tool_results'] = copy.deepcopy(sidecars)
            descriptors = output_descriptors(output)
            descriptors.update({item['id']: {**item, 'output_id': item['id']} for item in sidecars or []})
            for output_id, execution_id in execution_ids.items():
                execution = doc['executions'].get(execution_id)
                descriptor = descriptors.get(output_id)
                if (
                    not execution
                    or execution['status'] != 'resolved'
                    or execution['task_id'] != self.scope.task_id
                    or descriptor is None
                ):
                    raise ContextConflict()
                if descriptor.get('call_id') != execution['identity']['call_id']:
                    raise ContextConflict()
                item, binding = _raw_binding(output, sidecars, output_id)
                staged = doc['data'].get('resolved_outputs', {}).get(execution_id)
                existing = doc['data'].get('result_admissions', {}).get(_source_key(reply_id, output_id))
                if staged is not None:
                    if not isinstance(staged, dict) or staged.get('content') != descriptor.get('text'):
                        raise ContextConflict()
                    if staged.get('output_item') is not None:
                        if staged['output_item'] != item:
                            raise ContextConflict()
                    elif item.get('type') == 'function_call_output':
                        from open_webui.utils.images.context import build_tool_result_parts

                        parts, files = build_tool_result_parts(staged)
                        nontext = lambda values: [value for value in values if value.get('type') != 'input_text']
                        if (
                            nontext(parts) != nontext(item.get('output') or [])
                            or files != (item.get('files') or [])
                            or (staged.get('embeds') or []) != (item.get('embeds') or [])
                            or any(staged.get(key) != item.get(key) for key in ('citation_context', 'citation_sources'))
                        ):
                            raise ContextConflict()
                    elif staged.get('files') or staged.get('embeds'):
                        raise ContextConflict()
                elif (
                    not existing
                    or existing.get('execution_id') != execution_id
                    or existing.get('raw_binding') != binding
                ):
                    raise ContextConflict()
                descriptor = {**descriptor, 'message_id': reply_id}
                state = source_state(_snapshot(doc), descriptor, reply_id)
                if state is None:
                    raise ContextConflict()
                _table(doc, 'result_admissions')[_source_key(reply_id, output_id)] = {
                    'execution_id': execution_id,
                    'raw_binding': binding,
                    'result_projection': (
                        {
                            key: copy.deepcopy(value)
                            for key, value in staged.items()
                            if key not in ('content', 'output_item', 'publication')
                        }
                        if staged is not None
                        else copy.deepcopy(existing.get('result_projection') or {})
                    ),
                    'source_hash': state[0],
                    'source_fingerprint': state[1],
                }
                _table(doc, 'resolved_outputs').pop(execution_id, None)
            return message

        return await self.repository.publish_execution_results(
            self.scope.context_id,
            self.scope.actor_id,
            self.scope.epoch,
            task_id=self.scope.task_id,
            execution_ids=list(execution_ids.values()),
            raw_transition=write,
            expected_fence=self.scope.execution_fence,
        )

    async def recover_publications(self):
        """Recover persisted originals after the executor has exited, never rerun it."""
        doc = await self._read(settlement=True)
        task = self._task(doc, settlement=True, allow_done=True)
        if task['status'] != 'done':
            return False
        message = self._reply(doc, self.scope.reply_id, allow_done=True)
        output = copy.deepcopy(message.get('output') or [])
        sidecars = copy.deepcopy(message.get('tool_results') or [])
        mapping = {}
        pending = [
            entry
            for entry in doc['executions'].values()
            if entry['task_id'] == self.scope.task_id and entry.get('publication_pending')
        ]
        pending.sort(key=lambda entry: (entry['started_at'], entry['identity']['ordinal']))
        for entry in pending:
            staged = doc['data'].get('resolved_outputs', {}).get(entry['id']) or {}
            item = staged.get('output_item')
            if not isinstance(item, dict) or not item.get('id'):
                raise ContextConflict()
            publication = staged.get('publication') or {}
            call = publication.get('call_item')
            if call:
                call_index = next((i for i, value in enumerate(output) if value.get('id') == call.get('id')), None)
                if call_index is None:
                    output.append(copy.deepcopy(call))
                else:
                    output[call_index] = copy.deepcopy(call)
            target = sidecars if publication.get('sidecar') else output
            found = next((i for i, value in enumerate(target) if value.get('id') == item['id']), None)
            if found is None:
                target.append(copy.deepcopy(item))
            else:
                target[found] = copy.deepcopy(item)
            mapping[item['id']] = entry['id']
        if mapping:
            await self.commit_raw_results(self.scope.reply_id, mapping, output, sidecars, settlement=True)
        return bool(mapping)
