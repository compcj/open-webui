"""Exercise chat deletion against SQLite with foreign-key cascades disabled."""

import ast
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Text, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,args,remaining',
    [
        ('delete_chat_by_id', ('first',), {'other', 'second'}),
        ('delete_chat_by_id_and_user_id', ('first', 'owner'), {'other', 'second'}),
        ('delete_chat_by_id_and_user_id', ('first', 'stranger'), {'first', 'other', 'second'}),
        ('delete_chats_by_user_id', ('owner',), {'other'}),
        ('delete_chats_by_user_id_and_folder_id', ('owner', 'folder'), {'other', 'second'}),
    ],
)
async def test_delete_cleans_only_target_chats_checkpoints(tmp_path, monkeypatch, method, args, remaining):
    base = declarative_base()

    class Chat(base):
        __tablename__ = 'chat'
        id = Column(Text, primary_key=True)
        user_id = Column(Text)
        folder_id = Column(Text)

    class ChatMessage(base):
        __tablename__ = 'chat_message'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    class ChatContextCheckpoint(base):
        __tablename__ = 'chat_context_checkpoint'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    class ChatToolResult(base):
        __tablename__ = 'chat_tool_result'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    class ChatToolResultContext(base):
        __tablename__ = 'chat_tool_result_context'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    class ChatToolResultSelection(base):
        __tablename__ = 'chat_tool_result_selection'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    class SharedChat(base):
        __tablename__ = 'shared_chat'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    shared_module = ModuleType('open_webui.models.shared_chats')
    shared_module.SharedChat = SharedChat
    monkeypatch.setitem(sys.modules, 'open_webui.models.shared_chats', shared_module)

    class AutomationRun(base):
        __tablename__ = 'automation_run'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'cleanup.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    original = next(
        n
        for n in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(n, ast.ClassDef) and n.name == 'ChatTable'
    )
    node = next(n for n in original.body if isinstance(n, ast.AsyncFunctionDef) and n.name == method)
    module = ast.fix_missing_locations(
        ast.Module(
            body=[ast.ClassDef(name='ChatTable', bases=[], keywords=[], body=[node], decorator_list=[])],
            type_ignores=[],
        )
    )
    namespace = dict(
        AsyncSession=AsyncSession,
        get_async_db_context=context,
        Chat=Chat,
        ChatMessage=ChatMessage,
        ChatContextCheckpoint=ChatContextCheckpoint,
        ChatToolResult=ChatToolResult,
        ChatToolResultContext=ChatToolResultContext,
        ChatToolResultSelection=ChatToolResultSelection,
        AutomationRun=AutomationRun,
        select=select,
        update=update,
        delete=delete,
    )
    exec(compile(module, str(source), 'exec'), namespace)
    table = namespace['ChatTable']()
    table.delete_shared_chat_by_chat_id = AsyncMock(return_value=True)
    table.delete_shared_chats_by_user_id = AsyncMock(return_value=True)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            session.add_all(
                [
                    Chat(id='first', user_id='owner', folder_id='folder'),
                    Chat(id='other', user_id='stranger', folder_id='folder'),
                    Chat(id='second', user_id='owner', folder_id='another'),
                ]
            )
            for chat_id in ('first', 'other', 'second'):
                session.add(SharedChat(id=chat_id, chat_id=chat_id))
                for model in ('a', 'b'):
                    session.add(ChatContextCheckpoint(id=f'{chat_id}-{model}', chat_id=chat_id))
                    session.add(ChatToolResult(id=f'{chat_id}-{model}', chat_id=chat_id))
                    session.add(ChatToolResultContext(id=f'{chat_id}-{model}', chat_id=chat_id))
                    session.add(ChatToolResultSelection(id=f'{chat_id}-{model}', chat_id=chat_id))
            await session.commit()
        assert await getattr(table, method)(*args) is True
        async with sessions() as session:
            rows = (await session.execute(select(ChatContextCheckpoint))).scalars().all()
            assert {row.chat_id for row in rows} == remaining
            assert len(rows) == len(remaining) * 2
            for model in (ChatToolResult, ChatToolResultContext, ChatToolResultSelection):
                rows = (await session.execute(select(model))).scalars().all()
                assert {row.chat_id for row in rows} == remaining
                assert len(rows) == len(remaining) * 2
            assert set((await session.execute(select(Chat.id))).scalars()) == remaining
            if method == 'delete_chats_by_user_id_and_folder_id':
                assert set((await session.execute(select(SharedChat.chat_id))).scalars()) == remaining
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,args,remaining',
    [
        ('delete_model_by_id', ('first',), {'second', 'base-provider'}),
        ('delete_all_models', (), {'base-provider'}),
    ],
)
async def test_model_delete_cleans_selections_without_foreign_keys(tmp_path, method, args, remaining):
    base = declarative_base()

    class Model(base):
        __tablename__ = 'model'
        id = Column(Text, primary_key=True)

    class ChatToolResultSelection(base):
        __tablename__ = 'chat_tool_result_selection'
        id = Column(Text, primary_key=True)
        model_id = Column(Text)

    class AccessGrant(base):
        __tablename__ = 'access_grant'
        id = Column(Text, primary_key=True)
        resource_type = Column(Text)
        resource_id = Column(Text)

    class ModelHistory(base):
        __tablename__ = 'model_history'
        id = Column(Text, primary_key=True)
        model_id = Column(Text)

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'model-cleanup.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    source = Path(__file__).parents[1] / 'open_webui/models/models.py'
    original = next(
        node
        for node in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(node, ast.ClassDef) and node.name == 'ModelsTable'
    )
    nodes = [
        node
        for node in original.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {method, '_delete_models'}
    ]
    namespace = dict(
        AsyncSession=AsyncSession,
        get_async_db_context=context,
        Model=Model,
        ChatToolResultSelection=ChatToolResultSelection,
        delete=delete,
        select=select,
        AccessGrant=AccessGrant,
        ModelHistory=ModelHistory,
    )
    module = ast.fix_missing_locations(
        ast.Module(
            body=[ast.ClassDef(name='ModelsTable', bases=[], keywords=[], body=nodes, decorator_list=[])],
            type_ignores=[],
        )
    )
    exec(compile(module, str(source), 'exec'), namespace)
    table = namespace['ModelsTable']()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            session.add_all([Model(id='first'), Model(id='second')])
            session.add_all(
                [ChatToolResultSelection(id=model, model_id=model) for model in ('first', 'second', 'base-provider')]
            )
            for model in ('first', 'second', 'base-provider'):
                session.add(AccessGrant(id=model, resource_type='model', resource_id=model))
                session.add(ModelHistory(id=model, model_id=model))
            session.add(AccessGrant(id='other-resource', resource_type='skill', resource_id='first'))
            await session.commit()
        assert await getattr(table, method)(*args)
        async with sessions() as session:
            assert set((await session.execute(select(ChatToolResultSelection.model_id))).scalars()) == remaining
            assert set((await session.execute(select(ModelHistory.model_id))).scalars()) == remaining
            grants = (await session.execute(select(AccessGrant))).scalars().all()
            assert {(grant.resource_type, grant.resource_id) for grant in grants} == {
                *(('model', model) for model in remaining),
                ('skill', 'first'),
            }
    finally:
        await engine.dispose()
