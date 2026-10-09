"""Real SQLite checkpoint transactions/migration without importing app configuration."""

import importlib
import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import Column, JSON, Text, Boolean, Table, MetaData, create_engine, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).parents[1]


@pytest_asyncio.fixture
async def database(tmp_path):
    package = ModuleType('open_webui')
    package.__path__ = [str(ROOT / 'open_webui')]
    base = declarative_base()
    chat = Table(
        'chat',
        base.metadata,
        Column('id', Text, primary_key=True),
        Column('chat', JSON),
        Column('current_message_id', Text),
    )
    message = Table(
        'chat_message',
        base.metadata,
        Column('id', Text, primary_key=True),
        Column('chat_id', Text),
        Column('role', Text),
        Column('parent_id', Text),
        Column('content', JSON),
        Column('output', JSON),
        Column('files', JSON),
        Column('sources', JSON),
        Column('embeds', JSON),
        Column('done', Boolean),
    )
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'checkpoints.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    db_module = ModuleType('open_webui.internal.db')
    db_module.Base, db_module.get_async_db_context = base, context
    with patch.dict(sys.modules, {'open_webui': package, 'open_webui.internal.db': db_module}):
        sys.path.insert(0, str(ROOT))
        try:
            model = importlib.import_module('open_webui.models.chat_context_checkpoints')
            helpers = importlib.import_module('open_webui.utils.context_compaction_v2')
            async with engine.begin() as connection:
                await connection.run_sync(base.metadata.create_all)
            raw = {'id': 'u', 'role': 'user', 'content': 'original'}
            async with sessions() as session:
                await session.execute(
                    chat.insert().values(id='chat', chat={'history': {'messages': {'u': raw}}}, current_message_id='u')
                )
                await session.commit()
            yield model.ChatContextCheckpoints, helpers, sessions, chat, message
        finally:
            sys.path.remove(str(ROOT))
            await engine.dispose()


@pytest.mark.asyncio
async def test_persist_isolated_by_model_and_rejects_stale_version_or_source(database):
    store, h, sessions, chat, message = database
    snapshot = await store.read_snapshot('chat')
    refs = h.source_refs(list(snapshot['messages_map'].values()))
    data = dict(
        chat_id='chat',
        model_id='model',
        branch_anchor_id='u',
        source_refs=refs,
        source_fingerprint=h.digest(refs),
        covered_message_keys=['wire'],
        covered_output_ids=[],
        summary='handoff',
    )
    first = await store.save_checkpoint(data, expected_version=0)
    assert first['version'] == 1
    assert await store.save_checkpoint(data, expected_version=0) is None
    assert len(await store.list_checkpoints('chat', 'model')) == 1
    assert await store.list_checkpoints('chat', 'another') == []
    assert await store.save_checkpoint({**data, 'model_id': 'another'}, expected_version=0)
    async with sessions() as session:
        await session.execute(
            chat.update().values(
                chat={'history': {'messages': {'u': {'id': 'u', 'role': 'user', 'content': 'edited'}}}}
            )
        )
        await session.commit()
    assert await store.save_checkpoint(data, expected_version=1) is None
    assert (await store.list_checkpoints('chat', 'model'))[0]['summary'] == 'handoff'


@pytest.mark.asyncio
async def test_concurrent_checkpoint_cas_has_one_winner(database):
    import asyncio

    store, h, *_ = database
    snapshot = await store.read_snapshot('chat')
    refs = h.source_refs(list(snapshot['messages_map'].values()))
    data = dict(
        chat_id='chat',
        model_id='model',
        branch_anchor_id='u',
        source_refs=refs,
        source_fingerprint=h.digest(refs),
        covered_message_keys=['wire'],
        covered_output_ids=[],
        summary='handoff',
    )
    results = await asyncio.gather(
        store.save_checkpoint(data, expected_version=0), store.save_checkpoint(data, expected_version=0)
    )
    assert sum(result is not None for result in results) == 1


def test_migration_upgrade_downgrade_preserves_chat(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = ROOT / 'open_webui/migrations/versions/c7d2e4f6a801_add_chat_context_checkpoint.py'
    spec = importlib.util.spec_from_file_location('checkpoint_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE chat (id TEXT PRIMARY KEY, chat JSON)')
        connection.exec_driver_sql("INSERT INTO chat VALUES ('chat', '{}')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert 'chat_context_checkpoint' in inspect(connection).get_table_names()
            assert inspect(connection).get_foreign_keys('chat_context_checkpoint')[0]['referred_table'] == 'chat'
            migration.downgrade()
        assert 'chat_context_checkpoint' not in inspect(connection).get_table_names()
        assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat').scalar() == 1
    engine.dispose()
