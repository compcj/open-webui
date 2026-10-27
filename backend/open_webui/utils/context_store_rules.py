"""Pure branch/source validation and atomic selection transitions for all stores.

Adapters own authentication, locking/CAS, quotas and persistence. These functions
never perform I/O and never mutate caller-owned snapshots or rows. A failed
transition returns no writes, including when final request accounting rejects it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from types import SimpleNamespace


def record(value):
    return SimpleNamespace(**copy.deepcopy(value)) if isinstance(value, dict) else copy.deepcopy(value)


def record_dict(value):
    return copy.deepcopy(value if isinstance(value, dict) else vars(value))


def source_digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def branch_chain(messages: dict, branch_id: str) -> list[dict] | None:
    """Reject dangling parents and cycles instead of admitting a partial branch."""
    chain, seen = [], set()
    current = branch_id
    while current:
        if current in seen or current not in messages:
            return None
        seen.add(current)
        message = messages[current]
        chain.append({**message, 'id': current})
        current = message.get('parentId') or message.get('parent_id')
    return list(reversed(chain))


def source_state(snapshot: dict | None, descriptor: dict, branch_id: str) -> tuple[str, str] | None:
    from open_webui.utils.tool_result_sources import source_text

    if not snapshot:
        return None
    chain = branch_chain(snapshot['messages_map'], branch_id)
    if not chain:
        return None
    message_id, output_id = descriptor.get('message_id'), descriptor.get('output_id')
    source_index = next((i for i, message in enumerate(chain) if message['id'] == message_id), None)
    if source_index is None:
        return None
    message = chain[source_index]
    if message.get('role') != 'assistant':
        return None
    text = source_text(message, output_id)
    if text is None or text != descriptor.get('text'):
        return None
    items = [
        item
        for index, item in enumerate(message.get('output') or [])
        if isinstance(item, dict) and (item.get('id') or f'output-{index}') == output_id
    ]
    if not items:
        sidecars = message.get('tool_results') or (message.get('meta') or {}).get('tool_results') or []
        items = [item for item in sidecars if item.get('id') == output_id]
    if len(items) != 1:
        return None
    item = items[0]
    if item.get('call_id') and item['call_id'] != descriptor.get('call_id'):
        return None
    provenance = item.get('tool_result') or item
    for key in ('batch_id', 'name', 'kind'):
        if key in provenance and provenance[key] != descriptor.get(key):
            return None
    identity = {key: descriptor[key] for key in ('message_id', 'output_id', 'call_id', 'batch_id', 'name', 'kind')}
    # Only the selected source is immutable on its originating assistant. Later
    # output/content appended to that assistant must not invalidate earlier tools.
    ancestors = [
        {
            **{
                key: message.get(key)
                for key in ('id', 'parentId', 'parent_id', 'role', 'content', 'output', 'tool_calls', 'files')
            },
            'tool_results': message.get('tool_results') or (message.get('meta') or {}).get('tool_results') or [],
        }
        for message in chain[:source_index]
    ]
    source_hash = source_digest(text)
    return source_hash, source_digest(
        {
            'ancestors': ancestors,
            'source': identity,
            'source_hash': source_hash,
            'parent': message.get('parentId') or message.get('parent_id'),
        }
    )


def _chain_ids(snapshot, branch_id):
    chain = branch_chain(snapshot['messages_map'], branch_id) if snapshot else None
    return [message['id'] for message in chain] if chain else []


SOURCE_KEYS = ('message_id', 'output_id', 'call_id', 'batch_id', 'name', 'kind')


def stored_source(snapshot, selection, branch_id):
    from open_webui.utils.tool_result_sources import source_text

    selection = record(selection)
    if not snapshot:
        return None
    message = snapshot['messages_map'].get(selection.message_id)
    if not message:
        return None
    descriptor = {key: getattr(selection, key) for key in SOURCE_KEYS}
    descriptor['text'] = source_text(message, selection.output_id)
    state = source_state(snapshot, descriptor, branch_id)
    if state != (selection.source_hash, selection.source_fingerprint):
        return None
    return SimpleNamespace(
        **descriptor,
        id=state[1],
        result_id=selection.result_id,
        chat_id=selection.chat_id,
        source_hash=state[0],
        source_fingerprint=state[1],
    )


def source_for_descriptor(snapshot, chat_id, descriptor, branch_id, results):
    if not isinstance(descriptor, dict) or any(
        not isinstance(descriptor.get(k), str) or not descriptor[k] for k in SOURCE_KEYS
    ):
        return None
    if not isinstance(descriptor.get('text'), str):
        return None
    state = source_state(snapshot, descriptor, branch_id)
    if state is None:
        return None
    result = next(
        (
            item
            for item in results
            if item['chat_id'] == chat_id
            and item['message_id'] == descriptor['message_id']
            and item['output_id'] == descriptor['output_id']
            and (item['source_hash'], item['source_fingerprint']) == state
        ),
        None,
    )
    return SimpleNamespace(
        **{key: descriptor[key] for key in (*SOURCE_KEYS, 'text')},
        id=state[1],
        result_id=result['id'] if result else None,
        chat_id=chat_id,
        source_hash=state[0],
        source_fingerprint=state[1],
    )


def effective_selections(snapshot, chat_id, model_id, branch_id, selections, contexts):
    chain = _chain_ids(snapshot, branch_id)
    if not chain:
        return {}
    ranks = {anchor: rank for rank, anchor in enumerate(chain)}
    context_map = {item['id']: record(item) for item in contexts}
    rows = [
        (record(item), context_map.get(item.get('context_id')))
        for item in selections
        if item['chat_id'] == chat_id and item['model_id'] == model_id and item['branch_anchor_id'] in chain
    ]
    valid = []
    for selection, context in rows:
        raw = stored_source(snapshot, selection, branch_id)
        if raw is None:
            continue
        if selection.state not in {'pending', 'prepared'}:
            continue
        if selection.context_id is not None and (
            context is None
            or context.result_id != raw.result_id
            or context.chat_id != chat_id
            or context.model_id != model_id
            or context.branch_id not in chain
            or ranks[context.branch_id] > ranks[selection.branch_anchor_id]
        ):
            continue
        if snapshot.get('shared_history') and context is not None and context.source_scope != 'saved_history_v1':
            # Keep the admitted identity immutable, but project the proven raw
            # source instead of exposing an old private-context summary.
            context = None
        valid.append((selection, raw, context))
    # Prepared selections outrank pending registrations even when the latter are
    # closer. A stable tie-breaker makes legacy/corrupt duplicate data harmless.
    valid.sort(
        key=lambda item: (item[0].state == 'prepared', ranks[item[0].branch_anchor_id], item[0].id), reverse=True
    )
    effective = {}
    for selection, raw, context in valid:
        effective.setdefault(raw.id, (selection, raw, context))
    return effective


def resolved_selection(selection, raw, context):
    from open_webui.utils.context_compaction_v2 import estimate_text

    fallback = selection.context_id is not None and context is None
    return {
        **record_dict(selection),
        **{
            key: getattr(raw, key)
            for key in (
                'message_id',
                'output_id',
                'call_id',
                'batch_id',
                'name',
                'kind',
                'source_hash',
                'source_fingerprint',
            )
        },
        'batch_report': copy.deepcopy((selection.batch_report or {}).get('last_tool_batch')),
        'branch_id': selection.branch_anchor_id,
        'text': context.text if context is not None else raw.text,
        **(
            {
                'context_id': None,
                'method': 'original',
                'allocation': estimate_text(raw.text),
                'processed_tokens': estimate_text(raw.text),
                'batch_report': None,
            }
            if fallback
            else {}
        ),
    }


def make_pending_selection(raw, model_id, branch_id, *, state='pending', now=time.time_ns, new_id=None):
    return SimpleNamespace(
        id=(new_id or (lambda: str(uuid.uuid4())))(),
        chat_id=raw.chat_id,
        result_id=raw.result_id,
        model_id=model_id,
        **{key: getattr(raw, key) for key in (*SOURCE_KEYS, 'source_hash', 'source_fingerprint')},
        branch_anchor_id=branch_id,
        state=state,
        context_id=None,
        method='original',
        allocation=0,
        original_tokens=0,
        processed_tokens=0,
        recorded_at=now(),
        decision_id=None,
        batch_report=None,
    )


def pending_selection_transition(
    snapshot, chat_id, model_id, branch_id, selections, contexts, results, descriptors, *, now=time.time_ns, new_id=None
):
    if not isinstance(model_id, str) or not model_id or not isinstance(descriptors, list):
        return None
    if not _chain_ids(snapshot, branch_id):
        return None
    sources = [source_for_descriptor(snapshot, chat_id, data, branch_id, results) for data in descriptors]
    if any(raw is None for raw in sources):
        return None
    effective = effective_selections(snapshot, chat_id, model_id, branch_id, selections, contexts)
    return [
        record_dict(make_pending_selection(raw, model_id, branch_id, now=now, new_id=new_id))
        for raw in {raw.id: raw for raw in sources}.values()
        if raw.id not in effective
    ]


def prepare_selection_transition(
    snapshot,
    chat_id,
    model_id,
    branch_id,
    selections,
    contexts,
    results,
    *,
    choices,
    batch_reports,
    validate=None,
    expected_sources=(),
    now=time.time_ns,
    new_id=None,
):
    if (
        not isinstance(model_id, str)
        or not model_id
        or not isinstance(choices, list)
        or not isinstance(batch_reports, list)
    ):
        return None
    new_id = new_id or (lambda: str(uuid.uuid4()))
    context_map = {item['id']: record(item) for item in contexts}
    original_rows = {item['id']: item for item in selections}
    chain = _chain_ids(snapshot, branch_id)
    if not chain:
        return None
    # Validate every loaded source, including history-only sends
    # and sibling outputs omitted from this batch's choices. A new
    # original must not become a legacy baseline after its pending
    # registration was invalidated by an edit during generation.
    for expected in expected_sources:
        source = SimpleNamespace(**{**expected, 'chat_id': chat_id})
        if stored_source(snapshot, source, branch_id) is None:
            return None
    effective = effective_selections(snapshot, chat_id, model_id, branch_id, selections, contexts)
    selected, newly_prepared = [], {}
    required_reports, reported_sources = set(), set()
    for choice in choices:
        if not isinstance(choice, dict):
            return None
        raw = source_for_descriptor(snapshot, chat_id, choice.get('descriptor'), branch_id, results)
        if raw is None:
            return None
        existing = effective.get(raw.id)
        if existing and existing[0].state == 'prepared':
            selected.append(existing)
            continue
        if existing and existing[0].state == 'pending':
            required_reports.add(raw.id)
        method, context_id = choice.get('method'), choice.get('context_id')
        if method not in {'summary', 'excerpt', 'original', 'read'}:
            return None
        values = {key: choice.get(key) for key in ('allocation', 'original_tokens', 'processed_tokens')}
        if any(type(value) is not int or value < 0 for value in values.values()):
            return None
        if values['processed_tokens'] > values['allocation']:
            return None
        context = context_map.get(context_id) if context_id else None
        if method in {'summary', 'excerpt'} or (method == 'read' and context_id is not None):
            if not existing or context is None:
                return None
            if (
                (snapshot.get('shared_history') and context.source_scope != 'saved_history_v1')
                or context.result_id != raw.result_id
                or context.chat_id != chat_id
                or context.model_id != model_id
                or context.branch_id not in chain
                or context.method != method
                or context.original_tokens != values['original_tokens']
                or context.processed_tokens != values['processed_tokens']
            ):
                return None
        elif context_id is not None:
            return None
        if existing and existing[0].branch_anchor_id == branch_id:
            row = existing[0]
        else:
            row = make_pending_selection(raw, model_id, branch_id, now=now, new_id=new_id)
        row.state, row.context_id, row.method = 'prepared', context_id, method
        row.result_id = raw.result_id
        for key, value in values.items():
            setattr(row, key, value)
        row.recorded_at = now()
        row.decision_id = new_id()
        item = row, raw, context
        effective[raw.id] = item
        newly_prepared[raw.id] = item
        selected.append(item)
    # Attach reports only to batches containing newly admitted
    # versions. A retry reads the original report and timestamp.
    prepared = [item for item in effective.values() if item[0].state == 'prepared']
    by_key = {(item[1].message_id, item[1].output_id): item for item in prepared}
    for candidate in batch_reports:
        if not isinstance(candidate, dict) or candidate.get('complete') is not True:
            return None
        results = candidate.get('results')
        if not isinstance(results, list) or not results:
            return None
        members = []
        for result in results:
            if not isinstance(result, dict):
                return None
            source_message = (
                result.get('source_message_id') or result.get('message_id') or candidate.get('source_message_id')
            )
            item = by_key.get((source_message, result.get('output_id')))
            if item is None and result.get('result_id'):
                item = next((item for item in prepared if item[1].result_id == result['result_id']), None)
            if (
                item is None
                or item[1].batch_id != candidate.get('batch_id')
                or item[1].message_id != candidate.get('source_message_id')
            ):
                return None
            members.append(item)
        fresh = [item for item in members if item[1].id in newly_prepared]
        if not fresh:
            continue
        member_ids = {item[1].id for item in members}
        batch_ids = {
            raw.id
            for _, raw, _ in prepared
            if raw.batch_id == candidate.get('batch_id') and raw.message_id == candidate.get('source_message_id')
        }
        if len(member_ids) != len(members) or member_ids != batch_ids:
            return None
        # Never publish losing candidate accounting for an already
        # prepared member in a partially overlapping admission.
        reported_sources.update(item[1].id for item in fresh)
        report = copy.deepcopy(candidate)
        canonical_results = []
        for result, (row, raw, context) in zip(results, members):
            resolved = resolved_selection(row, raw, context)
            if raw.id not in newly_prepared and resolved['batch_report'] is not None:
                # Keep the winning operation's metadata too, not only
                # its token counts. A competing caller did not make it.
                result = next(
                    (
                        item
                        for item in (row.batch_report or {}).get('last_tool_batch', {}).get('results', [])
                        if item.get('selection_id') == row.id
                    ),
                    {},
                )
            canonical_results.append(
                {
                    **result,
                    'selection_id': row.id,
                    'context_id': resolved['context_id'],
                    'message_id': raw.message_id,
                    'source_message_id': raw.message_id,
                    'output_id': raw.output_id,
                    'result_id': raw.result_id,
                    'method': resolved['method'],
                    'allocated_tokens': resolved['allocation'],
                    'original_tokens': resolved['original_tokens'],
                    'processed_tokens': resolved['processed_tokens'],
                }
            )
        report['results'] = canonical_results
        report['original_tokens'] = sum(item['original_tokens'] for item in canonical_results)
        report['processed_tokens'] = sum(item['processed_tokens'] for item in canonical_results)
        report['recorded_at'] = max(item[0].recorded_at for item in fresh)
        report['decision_id'] = str(candidate.get('decision_id') or fresh[0][0].decision_id)
        envelope = {
            'model': model_id,
            'branch_id': branch_id,
            'recorded_at': report['recorded_at'],
            'last_tool_batch': report,
            'source_refs': [
                {
                    **{key: getattr(raw, key) for key in SOURCE_KEYS},
                    'result_id': raw.result_id,
                    'selection_id': row.id,
                    'context_id': result['context_id'],
                    'source_hash': raw.source_hash,
                    'source_fingerprint': raw.source_fingerprint,
                }
                for (row, raw, _), result in zip(members, canonical_results)
            ],
        }
        for row, _, _ in fresh:
            row.batch_report = envelope
            row.recorded_at = report['recorded_at']
            row.decision_id = report['decision_id']
    if not required_reports <= reported_sources:
        return None
    if validate is not None:
        fresh_ids = {row.id for key, (row, _, _) in newly_prepared.items() if key in required_reports}
        # Pure payload accounting only: never make provider/tool
        # requests while holding the source transaction.
        if not validate([resolved_selection(*item) for item in prepared], fresh_ids):
            return None
    return {
        'canonical': [resolved_selection(*item) for item in selected],
        'writes': [
            record_dict(row) for row, _, _ in newly_prepared.values() if original_rows.get(row.id) != record_dict(row)
        ],
    }


def latest_valid_accounting(snapshot, effective, chat_id, model_id, branch_id):
    chain = _chain_ids(snapshot, branch_id)
    reports = [
        item[0].batch_report
        for item in effective.values()
        if item[0].state == 'prepared'
        and item[0].batch_report
        and not (item[0].context_id is not None and item[2] is None)
    ]
    reports.sort(
        key=lambda report: (
            report.get('recorded_at', 0),
            report.get('last_tool_batch', {}).get('decision_id', ''),
        ),
        reverse=True,
    )
    for report in reports:
        if report.get('model') != model_id or report.get('branch_id') not in chain:
            continue
        batch = report.get('last_tool_batch') or {}
        refs = report.get('source_refs') or []
        if batch.get('complete') is not True or not refs or len(refs) != len(batch.get('results') or []):
            continue
        valid = True
        for ref in refs:
            raw = stored_source(snapshot, SimpleNamespace(**ref, chat_id=chat_id), branch_id)
            if (
                raw is None
                or raw.message_id != batch.get('source_message_id')
                or raw.batch_id != batch.get('batch_id')
                or raw.id not in effective
                or effective[raw.id][0].state != 'prepared'
                or effective[raw.id][0].id != ref.get('selection_id')
                or resolved_selection(*effective[raw.id])['context_id'] != ref.get('context_id')
            ):
                valid = False
                break
        if valid:
            resolved = copy.deepcopy(report)
            # A later admission on an ancestor may add another member
            # without changing this branch's winning projection. Keep
            # the historic decision, explicitly marked as partial.
            current_ids = {
                row.id
                for row, raw, _ in effective.values()
                if row.state == 'prepared'
                and raw.message_id == batch.get('source_message_id')
                and raw.batch_id == batch.get('batch_id')
            }
            resolved['last_tool_batch']['complete'] = current_ids == {ref['selection_id'] for ref in refs}
            return resolved
    return None
