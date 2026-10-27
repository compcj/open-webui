"""Durable original tool results and independently cached V2 projections."""

from __future__ import annotations

import copy
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from open_webui.utils.context_store_rules import branch_chain, source_digest, source_state
from open_webui.models.chat_context_checkpoints import read_snapshot_in_session
from sqlalchemy import BigInteger, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError, OperationalError


class ChatToolResult(Base):
    __tablename__ = 'chat_tool_result'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, ForeignKey('chat.id', ondelete='CASCADE'), nullable=False)
    message_id = Column(Text, nullable=False)
    output_id = Column(Text, nullable=False)
    call_id = Column(Text, nullable=False)
    batch_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    text = Column(Text, nullable=False)
    source_hash = Column(Text, nullable=False)
    source_fingerprint = Column(Text, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            'chat_id', 'message_id', 'output_id', 'source_hash', 'source_fingerprint', name='uq_chat_tool_result_source'
        ),
        Index('ix_chat_tool_result_chat', 'chat_id', 'message_id'),
    )


class ChatToolResultContext(Base):
    __tablename__ = 'chat_tool_result_context'
    id = Column(Text, primary_key=True)
    result_id = Column(Text, ForeignKey('chat_tool_result.id', ondelete='CASCADE'), nullable=False)
    chat_id = Column(Text, ForeignKey('chat.id', ondelete='CASCADE'), nullable=False)
    model_id = Column(Text, nullable=False)
    branch_id = Column(Text, nullable=False)
    task_hash = Column(Text, nullable=False)
    policy_hash = Column(Text, nullable=False)
    budget = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    method = Column(Text, nullable=False)
    source_scope = Column(Text, nullable=True)
    original_tokens = Column(Integer, nullable=False)
    processed_tokens = Column(Integer, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            'result_id',
            'model_id',
            'branch_id',
            'task_hash',
            'policy_hash',
            'budget',
            name='uq_chat_tool_result_context_key',
        ),
        Index('ix_chat_tool_result_context_chat', 'chat_id', 'model_id', 'branch_id'),
    )


def row_dict(row) -> dict:
    return {column.key: copy.deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


async def valid_result(db, result_id: str, branch_id: str, *, lock=False):
    row = await db.get(ChatToolResult, result_id)
    if row is None:
        return None
    snapshot = await read_snapshot_in_session(db, row.chat_id, lock=lock)
    state = source_state(snapshot, row_dict(row), branch_id)
    return row if state == (row.source_hash, row.source_fingerprint) else None


class ChatToolResultsTable:
    async def save_result(self, chat_id, descriptor: dict, *, branch_id: str) -> dict | None:
        keys = ('message_id', 'output_id', 'call_id', 'batch_id', 'name', 'kind')
        if any(not isinstance(descriptor.get(key), str) or not descriptor[key] for key in keys):
            return None
        if not isinstance(descriptor.get('text'), str):
            return None
        identity = {key: descriptor[key] for key in (*keys, 'text')}
        try:
            async with get_async_db_context() as db:
                snapshot = await read_snapshot_in_session(db, chat_id, lock=True)
                state = source_state(snapshot, identity, branch_id)
                if state is None:
                    return None
                source_hash, source_fingerprint = state
                stmt = select(ChatToolResult).where(
                    ChatToolResult.chat_id == chat_id,
                    ChatToolResult.message_id == identity['message_id'],
                    ChatToolResult.output_id == identity['output_id'],
                    ChatToolResult.source_hash == source_hash,
                    ChatToolResult.source_fingerprint == source_fingerprint,
                )
                row = (await db.execute(stmt)).scalars().first()
                if row is None:
                    row = ChatToolResult(
                        **identity,
                        chat_id=chat_id,
                        id=str(uuid.uuid4()),
                        source_hash=source_hash,
                        source_fingerprint=source_fingerprint,
                        created_at=time.time_ns(),
                    )
                    db.add(row)
                    await db.flush()
                    await db.commit()
                return row_dict(row)
        except IntegrityError:
            # The winner is read in a new transaction and source-validated again.
            async with get_async_db_context() as db:
                row = (await db.execute(stmt)).scalars().first()
                if row and await valid_result(db, row.id, branch_id):
                    return row_dict(row)
            return None
        except OperationalError:
            return None

    async def get_result(self, result_id, *, branch_id: str) -> dict | None:
        async with get_async_db_context() as db:
            row = await valid_result(db, result_id, branch_id)
            return row_dict(row) if row else None

    async def get_context(self, result_id, *, model_id, branch_id, task_hash, policy_hash, budget: int) -> dict | None:
        async with get_async_db_context() as db:
            if not await valid_result(db, result_id, branch_id):
                return None
            row = (
                (
                    await db.execute(
                        select(ChatToolResultContext).filter_by(
                            result_id=result_id,
                            model_id=model_id,
                            branch_id=branch_id,
                            task_hash=task_hash,
                            policy_hash=policy_hash,
                            budget=budget,
                        )
                    )
                )
                .scalars()
                .first()
            )
            snapshot = await read_snapshot_in_session(db, row.chat_id) if row else None
            if snapshot and snapshot.get('shared_history') and row.source_scope != 'saved_history_v1':
                return None
            return row_dict(row) if row else None

    async def save_context(
        self,
        result_id,
        *,
        model_id,
        branch_id,
        task_hash,
        policy_hash,
        budget: int,
        text: str,
        method: str,
        original_tokens: int,
        processed_tokens: int,
        source_scope: str | None = None,
    ) -> dict | None:
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
        try:
            async with get_async_db_context() as db:
                raw = await valid_result(db, result_id, branch_id, lock=True)
                if raw is None:
                    return None
                snapshot = await read_snapshot_in_session(db, raw.chat_id)
                if snapshot and snapshot.get('shared_history') and source_scope != 'saved_history_v1':
                    return None
                row = (await db.execute(select(ChatToolResultContext).filter_by(**key))).scalars().first()
                if row is None:
                    row = ChatToolResultContext(
                        **key,
                        chat_id=raw.chat_id,
                        id=str(uuid.uuid4()),
                        text=text,
                        method=method,
                        source_scope=source_scope,
                        original_tokens=original_tokens,
                        processed_tokens=processed_tokens,
                        created_at=time.time_ns(),
                    )
                    db.add(row)
                    await db.flush()
                    await db.commit()
                return row_dict(row)
        except IntegrityError:
            return await self.get_context(**key)
        except OperationalError:
            return None

    async def list_contexts(self, chat_id, model_id, branch_id) -> list[dict]:
        async with get_async_db_context() as db:
            snapshot = await read_snapshot_in_session(db, chat_id)
            rows = (
                await db.execute(
                    select(ChatToolResultContext, ChatToolResult)
                    .join(ChatToolResult, ChatToolResult.id == ChatToolResultContext.result_id)
                    .where(
                        ChatToolResultContext.chat_id == chat_id,
                        ChatToolResultContext.model_id == model_id,
                        ChatToolResultContext.branch_id == branch_id,
                    )
                )
            ).all()
            return [
                {
                    **row_dict(context),
                    **{
                        key: getattr(raw, key)
                        for key in ('message_id', 'output_id', 'source_hash', 'name', 'call_id', 'batch_id', 'kind')
                    },
                }
                for context, raw in rows
                if (not snapshot or not snapshot.get('shared_history') or context.source_scope == 'saved_history_v1')
                and source_state(snapshot, row_dict(raw), branch_id) == (raw.source_hash, raw.source_fingerprint)
            ]


ChatToolResults = ChatToolResultsTable()
