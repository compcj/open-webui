"""Read effective share modes from existing snapshots using the database's JSON parser."""

import json
import os

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from open_webui.internal import db as database
from open_webui.models.shared_chats import SharedChats
from test_v0133_agent_execution_store import event_loop_policy


@pytest.mark.asyncio
@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
async def test_share_mode_matches_the_last_decoded_top_level_field(dialect, monkeypatch):
    url = 'sqlite+aiosqlite://' if dialect == 'sqlite' else os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set TEST_POSTGRES_URL for PostgreSQL share-mode checks')
    engine = create_async_engine(url, poolclass=StaticPool)
    sessions = async_sessionmaker(engine)
    monkeypatch.setattr(database, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(database, 'DATABASE_ENABLE_SESSION_SHARING', True)
    # Raw fixtures retain historical JSON encodings, duplicate keys and invalid Unicode escapes.
    samples = [
        r'{"share_mode":"continue","share_mode":null}',
        r'{"share_mode":null,"share_\u006dode":"c\u006fntinue"}',
        r'{"share_mode\u0000other":"continue"}',
        r'{"share_\u0000mode":"continue"}',
        r'{"share_m\udc00ode":"continue"}',
        r'{"share_mode":"continue\u0000other"}',
        r'{"share_mode":"conti\u0000nue"}',
        r'{"share_mode":"conti\ud800nue"}',
        r'{"share_mode":"Continue"}',
        r'{"share_mode":"continue "}',
        r'{"share_mode":true}',
        r'{"share_mode":{"nested":"continue"}}',
        r'{"share_mode":["continue"]}',
        r'{"message":{"share_mode":"continue"}}',
        r'{"message":"literal \\u0000 and escaped \u0000","share_mode":"continue"}',
        r'{"message":"\u4e00","share_mode":"continue"}',
        r'{"message":"\ud800\ud800 and \udc00 and \ud83d\ude00","share_mode":"continue"}',
    ]
    if dialect == 'sqlite':
        # Python's JSON codec also accepts these numbers in existing SQLite TEXT columns.
        samples.extend(
            [
                r'{"share_mode":"continue","metrics":[NaN,Infinity,-Infinity]}',
                r'{"share_mode":NaN}',
                r'{"share_mode":Infinity}',
                r'{"share_mode":-Infinity}',
                r'{"share_NaNmode":"continue","metric":Infinity}',
                r'{"share_mode":"conti-Infinitynue","metric":NaN}',
                r'{"share_mode":"continue","metric":1NaN}',
                r'{"share_mode":"continue","metric":"\NaN"}',
                r'{"share_mode":"continue","metric":+Infinity}',
                r'{share_mode:"continue"}',
            ]
        )
    bind_json = 'cast(:snapshot as json)' if dialect == 'postgresql' else ':snapshot'
    queries = []

    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def record_query(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    try:
        async with sessions.begin() as db:
            await db.execute(
                text(
                    'CREATE TEMPORARY TABLE shared_chat (id TEXT PRIMARY KEY, chat_id TEXT, chat JSON, updated_at BIGINT)'
                )
            )
        assert await SharedChats.get_share_mode_by_chat_id('chat') is None
        for revision, snapshot in enumerate(samples):
            try:
                stored_mode = json.loads(snapshot).get('share_mode')
            except json.JSONDecodeError:
                stored_mode = None
            expected = 'continue' if stored_mode == 'continue' else None
            async with sessions.begin() as db:
                await db.execute(
                    text(f"INSERT INTO shared_chat VALUES (:id, 'chat', {bind_json}, :revision)"),
                    {'id': str(revision), 'snapshot': snapshot, 'revision': revision},
                )
                queries.clear()
                assert await SharedChats.get_share_mode_by_chat_id('chat', db=db) == expected, snapshot
                assert len(queries) == 1
            queries.clear()
            assert await SharedChats.get_share_mode_by_chat_id('chat') == expected, snapshot
            # A failed PostgreSQL extraction can log part of the snapshot before a retry.
            assert len(queries) == 1
    finally:
        await engine.dispose()
