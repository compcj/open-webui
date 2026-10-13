"""Durable V2 checkpoints. Source validation and version CAS share one transaction."""

from __future__ import annotations

import copy
import time
import uuid

from open_webui.internal.db import Base, get_async_db_context
from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, Integer, Text, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError, OperationalError


class ChatContextCheckpoint(Base):
    __tablename__ = 'chat_context_checkpoint'
    id = Column(Text, primary_key=True)
    chat_id = Column(Text, ForeignKey('chat.id', ondelete='CASCADE'), nullable=False)
    model_id = Column(Text, nullable=False)
    branch_anchor_id = Column(Text, nullable=False)
    version = Column(Integer, nullable=False)
    source_refs = Column(JSON, nullable=False)
    source_fingerprint = Column(Text, nullable=False)
    covered_message_keys = Column(JSON, nullable=False)
    covered_output_ids = Column(JSON, nullable=False)
    summary = Column(Text, nullable=False)
    source_scope = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    __table_args__ = (
        UniqueConstraint('chat_id', 'model_id', 'version', name='uq_chat_context_checkpoint_version'),
        Index('ix_chat_context_checkpoint_lookup', 'chat_id', 'model_id', 'version'),
    )


def checkpoint_dict(row) -> dict:
    return {column.key: copy.deepcopy(getattr(row, column.key)) for column in ChatContextCheckpoint.__table__.columns}


async def read_snapshot_in_session(db, chat_id: str, *, lock=False) -> dict | None:
    # These tables are already registered during normal app initialization. Read
    # directly to avoid Chats.get_messages_map's legacy backfill side effects.
    chat_table = Base.metadata.tables['chat']
    stmt = select(chat_table).where(chat_table.c.id == chat_id)
    if lock:
        stmt = stmt.with_for_update()
    chat = (await db.execute(stmt)).mappings().first()
    if chat is None:
        return None
    data = copy.deepcopy(chat['chat'] or {})
    messages = copy.deepcopy((data.get('history') or {}).get('messages') or {})
    if not messages:
        messages = {m['id']: m for m in data.get('messages') or [] if m.get('id')}
    table = Base.metadata.tables.get('chat_message')
    if table is not None:
        stmt = select(table).where(table.c.chat_id == chat_id)
        if lock:
            stmt = stmt.with_for_update()
        rows = (await db.execute(stmt)).mappings().all()
        for row in rows:
            identity = row['id'].removeprefix(f'{chat_id}-')
            mapping = {'parent_id': 'parentId', 'model_id': 'model'}
            message = {
                mapping.get(k, k): copy.deepcopy(v)
                for k, v in row.items()
                if k not in {'chat_id', 'id', 'updated_at', 'created_at'} and v is not None
            }
            message['id'] = identity
            message.setdefault('content', '')
            messages[identity] = message
    for identity, message in messages.items():
        message['id'] = identity
    current = chat.get('current_message_id') or (data.get('history') or {}).get('currentId') or data.get('currentId')
    shared_history = False
    shared_table = Base.metadata.tables.get('shared_chat')
    if shared_table is not None:
        shares = (
            (await db.execute(select(shared_table.c.chat).where(shared_table.c.chat_id == chat_id))).scalars().all()
        )
        shared_history = any((item or {}).get('share_mode') == 'continue' for item in shares)
    # Folder readers see live history even when they cannot continue it. Walk
    # inherited grants as well; presence is sufficient, no actor dimension.
    folder_table = Base.metadata.tables.get('folder')
    grants = Base.metadata.tables.get('access_grant')
    folder_id, seen = chat.get('folder_id'), set()
    while not shared_history and folder_id and folder_id not in seen and folder_table is not None:
        seen.add(folder_id)
        if grants is not None:
            shared_history = (
                await db.execute(
                    select(grants.c.id)
                    .where(grants.c.resource_type == 'folder', grants.c.resource_id == folder_id)
                    .limit(1)
                )
            ).first() is not None
        folder = (await db.execute(select(folder_table).where(folder_table.c.id == folder_id))).mappings().first()
        folder_id = folder.get('parent_id') if folder else None
    return {
        'chat': data,
        'messages_map': messages,
        'current_id': current,
        'owner_id': chat.get('user_id'),
        'shared_history': shared_history,
    }


class ChatContextCheckpointsTable:
    async def read_snapshot(self, chat_id: str) -> dict | None:
        async with get_async_db_context() as db:
            return await read_snapshot_in_session(db, chat_id)

    async def latest_version(self, chat_id: str, model_id: str) -> int:
        async with get_async_db_context() as db:
            return int(
                (
                    await db.execute(
                        select(func.max(ChatContextCheckpoint.version)).where(
                            ChatContextCheckpoint.chat_id == chat_id, ChatContextCheckpoint.model_id == model_id
                        )
                    )
                ).scalar()
                or 0
            )

    async def list_checkpoints(self, chat_id: str, model_id: str) -> list[dict]:
        async with get_async_db_context() as db:
            rows = (
                (
                    await db.execute(
                        select(ChatContextCheckpoint)
                        .where(ChatContextCheckpoint.chat_id == chat_id, ChatContextCheckpoint.model_id == model_id)
                        .order_by(ChatContextCheckpoint.version.desc())
                    )
                )
                .scalars()
                .all()
            )
            return [checkpoint_dict(row) for row in rows]

    async def save_checkpoint(self, data: dict, *, expected_version: int) -> dict | None:
        from open_webui.utils.context_compaction_v2 import digest, source_refs_match

        try:
            async with get_async_db_context() as db:
                # Locks only span these DB operations, never the model await.
                snapshot = await read_snapshot_in_session(db, data['chat_id'], lock=True)
                if not snapshot or not source_refs_match(data['source_refs'], snapshot['messages_map']):
                    return None
                if snapshot.get('shared_history') and data.get('source_scope') != 'saved_history_v1':
                    return None
                if digest(data['source_refs']) != data['source_fingerprint']:
                    return None
                latest = int(
                    (
                        await db.execute(
                            select(func.max(ChatContextCheckpoint.version)).where(
                                ChatContextCheckpoint.chat_id == data['chat_id'],
                                ChatContextCheckpoint.model_id == data['model_id'],
                            )
                        )
                    ).scalar()
                    or 0
                )
                if latest != expected_version:
                    return None
                row = ChatContextCheckpoint(
                    **data, id=str(uuid.uuid4()), version=expected_version + 1, created_at=time.time_ns()
                )
                db.add(row)
                await db.flush()
                result = checkpoint_dict(row)
                await db.commit()
                return result
        except (IntegrityError, OperationalError):
            # The unique version is also a CAS for SQLite, whose SELECT FOR UPDATE
            # is a no-op. A concurrent writer or snapshot conflict must not win.
            return None


ChatContextCheckpoints = ChatContextCheckpointsTable()
