"""Server-owned selections frozen when a complete V2 request is admitted for transport.

Candidate contexts may be generated freely. Only this table decides what gets
replayed; no transaction spans candidate generation or provider transport.
"""

from __future__ import annotations

import copy
import time
import uuid
from types import SimpleNamespace

from open_webui.internal.db import Base, get_async_db_context
from open_webui.models.chat_context_checkpoints import read_snapshot_in_session
from open_webui.models.chat_tool_results import (
    ChatToolResult,
    ChatToolResultContext,
    branch_chain,
    row_dict,
    source_state,
)
from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, select, text
from sqlalchemy.exc import IntegrityError, OperationalError


class ChatToolResultSelection(Base):
    __tablename__ = 'chat_tool_result_selection'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, ForeignKey('chat.id', ondelete='CASCADE'), nullable=False)
    result_id = Column(Text, ForeignKey('chat_tool_result.id', ondelete='CASCADE'), nullable=True)
    message_id = Column(Text, nullable=False)
    output_id = Column(Text, nullable=False)
    call_id = Column(Text, nullable=False)
    batch_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    source_hash = Column(Text, nullable=False)
    source_fingerprint = Column(Text, nullable=False)
    model_id = Column(Text, nullable=False)
    branch_anchor_id = Column(Text, nullable=False)
    state = Column(Text, nullable=False)
    context_id = Column(Text, ForeignKey('chat_tool_result_context.id', ondelete='CASCADE'), nullable=True)
    method = Column(Text, nullable=False)
    allocation = Column(Integer, nullable=False)
    original_tokens = Column(Integer, nullable=False)
    processed_tokens = Column(Integer, nullable=False)
    decision_id = Column(Text, nullable=True)
    recorded_at = Column(BigInteger, nullable=False)
    batch_report = Column(JSON, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            'chat_id',
            'model_id',
            'branch_anchor_id',
            'message_id',
            'output_id',
            'source_hash',
            'source_fingerprint',
            name='uq_chat_tool_result_selection_anchor',
        ),
        Index('ix_chat_tool_result_selection_chat', 'chat_id', 'model_id', 'branch_anchor_id'),
    )


async def _snapshot(db, chat_id, *, lock=False):
    if lock and db.bind.dialect.name == 'sqlite':
        # SQLite ignores SELECT FOR UPDATE; serialize before the first read.
        await db.execute(text('BEGIN IMMEDIATE'))
    return await read_snapshot_in_session(db, chat_id, lock=lock)


def _chain_ids(snapshot, branch_id):
    chain = branch_chain(snapshot['messages_map'], branch_id) if snapshot else None
    return [message['id'] for message in chain] if chain else []


SOURCE_KEYS = ('message_id', 'output_id', 'call_id', 'batch_id', 'name', 'kind')


def _stored_source(snapshot, selection, branch_id):
    from open_webui.utils.tool_result_sources import source_text

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


async def _source(db, snapshot, chat_id, descriptor, branch_id):
    if not isinstance(descriptor, dict) or any(
        not isinstance(descriptor.get(k), str) or not descriptor[k] for k in SOURCE_KEYS
    ):
        return None
    if not isinstance(descriptor.get('text'), str):
        return None
    state = source_state(snapshot, descriptor, branch_id)
    if state is None:
        return None
    row = (
        (
            await db.execute(
                select(ChatToolResult).filter_by(
                    chat_id=chat_id,
                    message_id=descriptor['message_id'],
                    output_id=descriptor['output_id'],
                    source_hash=state[0],
                    source_fingerprint=state[1],
                )
            )
        )
        .scalars()
        .first()
    )
    return SimpleNamespace(
        **{key: descriptor[key] for key in (*SOURCE_KEYS, 'text')},
        id=state[1],
        result_id=row.id if row else None,
        chat_id=chat_id,
        source_hash=state[0],
        source_fingerprint=state[1],
    )


async def _effective(db, snapshot, chat_id, model_id, branch_id):
    chain = _chain_ids(snapshot, branch_id)
    if not chain:
        return {}
    ranks = {anchor: rank for rank, anchor in enumerate(chain)}
    rows = (
        await db.execute(
            select(ChatToolResultSelection, ChatToolResultContext)
            .outerjoin(ChatToolResultContext, ChatToolResultContext.id == ChatToolResultSelection.context_id)
            .where(
                ChatToolResultSelection.chat_id == chat_id,
                ChatToolResultSelection.model_id == model_id,
                ChatToolResultSelection.branch_anchor_id.in_(chain),
            )
        )
    ).all()
    valid = []
    for selection, context in rows:
        raw = _stored_source(snapshot, selection, branch_id)
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


def _resolved(selection, raw, context):
    return {
        **row_dict(selection),
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
    }


def _new_row(raw, model_id, branch_id, *, state='pending'):
    return ChatToolResultSelection(
        id=str(uuid.uuid4()),
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
        recorded_at=time.time_ns(),
    )


class ChatToolResultSelectionsTable:
    async def register_pending(self, chat_id, model_id, descriptors, *, branch_id):
        if not isinstance(model_id, str) or not model_id or not isinstance(descriptors, list):
            return False
        try:
            async with get_async_db_context() as db:
                snapshot = await _snapshot(db, chat_id, lock=True)
                if not _chain_ids(snapshot, branch_id):
                    return False
                sources = [await _source(db, snapshot, chat_id, data, branch_id) for data in descriptors]
                if any(raw is None for raw in sources):
                    return False
                effective = await _effective(db, snapshot, chat_id, model_id, branch_id)
                for raw in {raw.id: raw for raw in sources}.values():
                    if raw.id not in effective:
                        db.add(_new_row(raw, model_id, branch_id))
                await db.commit()
                return True
        except (IntegrityError, OperationalError):
            return False

    async def list_selections(self, chat_id, model_id, branch_id):
        async with get_async_db_context() as db:
            snapshot = await _snapshot(db, chat_id)
            effective = await _effective(db, snapshot, chat_id, model_id, branch_id)
            return [_resolved(*row) for row in effective.values()]

    async def prepare_selections(
        self, chat_id, model_id, *, branch_id, choices, batch_reports, validate=None, expected_sources=()
    ):
        if (
            not isinstance(model_id, str)
            or not model_id
            or not isinstance(choices, list)
            or not isinstance(batch_reports, list)
        ):
            return None
        try:
            async with get_async_db_context() as db:
                snapshot = await _snapshot(db, chat_id, lock=True)
                chain = _chain_ids(snapshot, branch_id)
                if not chain:
                    return None
                # Validate every loaded source, including history-only sends
                # and sibling outputs omitted from this batch's choices. A new
                # original must not become a legacy baseline after its pending
                # registration was invalidated by an edit during generation.
                for expected in expected_sources:
                    source = SimpleNamespace(**{**expected, 'chat_id': chat_id})
                    if _stored_source(snapshot, source, branch_id) is None:
                        return None
                effective = await _effective(db, snapshot, chat_id, model_id, branch_id)
                selected, newly_prepared = [], {}
                required_reports, reported_sources = set(), set()
                for choice in choices:
                    if not isinstance(choice, dict):
                        return None
                    raw = await _source(db, snapshot, chat_id, choice.get('descriptor'), branch_id)
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
                    context = await db.get(ChatToolResultContext, context_id) if context_id else None
                    if method in {'summary', 'excerpt'} or (method == 'read' and context_id is not None):
                        if not existing or context is None:
                            return None
                        if (
                            context.result_id != raw.result_id
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
                        row = _new_row(raw, model_id, branch_id)
                        db.add(row)
                    row.state, row.context_id, row.method = 'prepared', context_id, method
                    row.result_id = raw.result_id
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.recorded_at = time.time_ns()
                    row.decision_id = str(uuid.uuid4())
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
                            result.get('source_message_id')
                            or result.get('message_id')
                            or candidate.get('source_message_id')
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
                        if raw.batch_id == candidate.get('batch_id')
                        and raw.message_id == candidate.get('source_message_id')
                    }
                    if len(member_ids) != len(members) or member_ids != batch_ids:
                        return None
                    # Never publish losing candidate accounting for an already
                    # prepared member in a partially overlapping admission.
                    reported_sources.update(item[1].id for item in fresh)
                    report = copy.deepcopy(candidate)
                    canonical_results = []
                    for result, (row, raw, context) in zip(results, members):
                        if raw.id not in newly_prepared:
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
                                'context_id': row.context_id,
                                'message_id': raw.message_id,
                                'source_message_id': raw.message_id,
                                'output_id': raw.output_id,
                                'result_id': raw.result_id,
                                'method': row.method,
                                'allocated_tokens': row.allocation,
                                'original_tokens': row.original_tokens,
                                'processed_tokens': row.processed_tokens,
                            }
                        )
                    report['results'] = canonical_results
                    report['original_tokens'] = sum(item[0].original_tokens for item in members)
                    report['processed_tokens'] = sum(item[0].processed_tokens for item in members)
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
                                'context_id': row.context_id,
                                'source_hash': raw.source_hash,
                                'source_fingerprint': raw.source_fingerprint,
                            }
                            for row, raw, _ in members
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
                    if not validate([_resolved(*item) for item in prepared], fresh_ids):
                        return None
                await db.flush()
                canonical = [_resolved(*item) for item in selected]
                await db.commit()
                return canonical
        except (IntegrityError, OperationalError):
            return None

    async def latest_accounting(self, chat_id, model_id, branch_id):
        async with get_async_db_context() as db:
            snapshot = await _snapshot(db, chat_id)
            effective = await _effective(db, snapshot, chat_id, model_id, branch_id)
            chain = _chain_ids(snapshot, branch_id)
            reports = [
                item[0].batch_report
                for item in effective.values()
                if item[0].state == 'prepared' and item[0].batch_report
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
                    raw = _stored_source(snapshot, SimpleNamespace(**ref, chat_id=chat_id), branch_id)
                    if (
                        raw is None
                        or raw.message_id != batch.get('source_message_id')
                        or raw.batch_id != batch.get('batch_id')
                        or raw.id not in effective
                        or effective[raw.id][0].state != 'prepared'
                        or effective[raw.id][0].id != ref.get('selection_id')
                        or effective[raw.id][0].context_id != ref.get('context_id')
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


ChatToolResultSelections = ChatToolResultSelectionsTable()


def get_tool_selection_store():
    return ChatToolResultSelections
