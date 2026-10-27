"""Server-owned selections frozen when a complete V2 request is admitted for transport.

Candidate contexts may be generated freely. Only this table decides what gets
replayed; no transaction spans candidate generation or provider transport.
"""

from __future__ import annotations

import copy

from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.context_store_rules import (
    _chain_ids,
    effective_selections,
    latest_valid_accounting,
    pending_selection_transition,
    prepare_selection_transition,
    resolved_selection,
)
from open_webui.models.chat_context_checkpoints import read_snapshot_in_session
from open_webui.models.chat_tool_results import (
    ChatToolResult,
    ChatToolResultContext,
    row_dict,
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


async def _records(db, snapshot, chat_id, model_id, branch_id, context_ids=(), *, with_results=False):
    chain = _chain_ids(snapshot, branch_id)
    if not chain:
        return [], [], []
    rows = list(
        (
            await db.execute(
                select(ChatToolResultSelection).where(
                    ChatToolResultSelection.chat_id == chat_id,
                    ChatToolResultSelection.model_id == model_id,
                    ChatToolResultSelection.branch_anchor_id.in_(chain),
                )
            )
        ).scalars()
    )
    context_ids = {identity for identity in context_ids if identity}
    context_ids.update(row.context_id for row in rows if row.context_id)
    contexts = (
        list(
            (await db.execute(select(ChatToolResultContext).where(ChatToolResultContext.id.in_(context_ids)))).scalars()
        )
        if context_ids
        else []
    )
    # Original bodies are read from the locked snapshot. Only result identities
    # are needed here; do not load another copy of potentially large tool text.
    if not with_results:
        return rows, [row_dict(row) for row in contexts], []
    keys = ('id', 'chat_id', 'message_id', 'output_id', 'source_hash', 'source_fingerprint')
    raw = (
        (
            await db.execute(
                select(*(getattr(ChatToolResult, key) for key in keys)).where(ChatToolResult.chat_id == chat_id)
            )
        )
        .mappings()
        .all()
    )
    return rows, [row_dict(row) for row in contexts], [dict(row) for row in raw]


def _apply(db, existing, writes):
    by_id = {row.id: row for row in existing}
    for data in writes:
        row = by_id.get(data['id'])
        if row is None:
            db.add(ChatToolResultSelection(**data))
        else:
            for key, value in data.items():
                setattr(row, key, copy.deepcopy(value))


class ChatToolResultSelectionsTable:
    async def register_pending(self, chat_id, model_id, descriptors, *, branch_id):
        if not isinstance(model_id, str) or not model_id or not isinstance(descriptors, list):
            return False
        try:
            async with get_async_db_context() as db:
                snapshot = await _snapshot(db, chat_id, lock=True)
                rows, contexts, raw = await _records(db, snapshot, chat_id, model_id, branch_id, with_results=True)
                writes = pending_selection_transition(
                    snapshot,
                    chat_id,
                    model_id,
                    branch_id,
                    [row_dict(row) for row in rows],
                    contexts,
                    raw,
                    descriptors,
                )
                if writes is None:
                    return False
                _apply(db, rows, writes)
                await db.commit()
                return True
        except (IntegrityError, OperationalError):
            return False

    async def list_selections(self, chat_id, model_id, branch_id):
        async with get_async_db_context() as db:
            snapshot = await _snapshot(db, chat_id)
            rows, contexts, _ = await _records(db, snapshot, chat_id, model_id, branch_id)
            effective = effective_selections(
                snapshot,
                chat_id,
                model_id,
                branch_id,
                [row_dict(row) for row in rows],
                contexts,
            )
            return [resolved_selection(*item) for item in effective.values()]

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
                rows, contexts, raw = await _records(
                    db,
                    snapshot,
                    chat_id,
                    model_id,
                    branch_id,
                    [choice.get('context_id') for choice in choices if isinstance(choice, dict)],
                    with_results=True,
                )
                result = prepare_selection_transition(
                    snapshot,
                    chat_id,
                    model_id,
                    branch_id,
                    [row_dict(row) for row in rows],
                    contexts,
                    raw,
                    choices=choices,
                    batch_reports=batch_reports,
                    validate=validate,
                    expected_sources=expected_sources,
                )
                if result is None:
                    return None
                _apply(db, rows, result['writes'])
                await db.flush()
                await db.commit()
                return result['canonical']
        except (IntegrityError, OperationalError):
            return None

    async def latest_accounting(self, chat_id, model_id, branch_id):
        async with get_async_db_context() as db:
            snapshot = await _snapshot(db, chat_id)
            rows, contexts, _ = await _records(db, snapshot, chat_id, model_id, branch_id)
            effective = effective_selections(
                snapshot,
                chat_id,
                model_id,
                branch_id,
                [row_dict(row) for row in rows],
                contexts,
            )
            return latest_valid_accounting(snapshot, effective, chat_id, model_id, branch_id)


ChatToolResultSelections = ChatToolResultSelectionsTable()


def get_tool_selection_store():
    return ChatToolResultSelections
