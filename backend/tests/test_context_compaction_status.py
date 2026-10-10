"""Compaction status delivery and reload against isolated SQLite storage."""

import ast
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, JSON, Text, Integer
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.attributes import flag_modified

from test_web_tool_citations import load_functions


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,persist', [('context_compaction', True), ('status', True), ('context_usage', False)])
async def test_socket_persists_lifecycle_but_not_usage(kind, persist):
    chats = SimpleNamespace(add_message_status_to_chat_by_id_and_message_id=AsyncMock())
    sio = SimpleNamespace(emit=AsyncMock(), manager=SimpleNamespace(rooms={'/': {'user:owner': {}}}))
    namespace = dict(Chats=chats, sio=sio, WEBSOCKET_MANAGER='', is_saved_chat_id=lambda id: id == 'chat')
    load_functions(namespace, 'socket/main.py', {'get_event_emitter'})
    emitter = await namespace['get_event_emitter']({'user_id': 'owner', 'chat_id': 'chat', 'message_id': 'a'})
    status = {'action': 'context_compaction', 'mode': 'v2', 'model': 'model', 'done': True}
    await emitter({'type': kind, 'data': status})
    assert sio.emit.await_args.args[1]['message_id'] == 'a'
    assert chats.add_message_status_to_chat_by_id_and_message_id.await_count == int(persist)
    if persist:
        chats.add_message_status_to_chat_by_id_and_message_id.assert_awaited_once_with('chat', 'a', status)


@pytest.mark.asyncio
@pytest.mark.parametrize('embedded,normalized', [(True, True), (True, False), (False, True)])
async def test_status_survives_reload_in_both_history_stores(tmp_path, embedded, normalized):
    base = declarative_base()

    class Chat(base):
        __tablename__ = 'chat'
        id = Column(Text, primary_key=True)
        chat = Column(JSON)
        title = Column(Text)
        current_message_id = Column(Text)
        updated_at = Column(Integer)

    class ChatMessage(base):
        __tablename__ = 'chat_message'
        id = Column(Text, primary_key=True)
        status_history = Column(JSON)
        content = Column(Text)
        updated_at = Column(Integer)

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'status.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context():
        async with sessions() as session:
            yield session

    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChatTable')
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == 'add_message_status_to_chat_by_id_and_message_id'
    )
    namespace = dict(
        Chat=Chat,
        ChatMessage=ChatMessage,
        get_async_db_context=context,
        ChatModel=SimpleNamespace(model_validate=lambda row: row),
        flag_modified=flag_modified,
        time=time,
    )
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    table = SimpleNamespace(
        _clean_null_bytes=lambda x: x,
        _repair_chat_current_id=lambda chat: None,
        get_current_message_id=lambda chat: chat['history']['currentId'],
    )
    previous = {'action': 'web_search', 'done': True}
    start = {'action': 'context_compaction', 'done': False}
    done = {'action': 'context_compaction', 'done': True, 'before_tokens': 90000, 'after_tokens': 20000}
    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            messages = {'a': {'content': 'original', 'statusHistory': [previous]}} if embedded else {}
            session.add(Chat(id='chat', chat={'history': {'messages': messages, 'currentId': 'a'}}))
            if normalized:
                session.add(ChatMessage(id='chat-a', content='original', status_history=[previous]))
            await session.commit()
        for status in (start, done):
            result = await namespace['add_message_status_to_chat_by_id_and_message_id'](table, 'chat', 'a', status)
            assert result is not None
        async with sessions() as session:
            chat = await session.get(Chat, 'chat')
            if embedded:
                message = chat.chat['history']['messages']['a']
                assert message['statusHistory'] == [previous, start, done]
                assert message['content'] == 'original'
            if normalized:
                message = await session.get(ChatMessage, 'chat-a')
                assert message.status_history == [previous, start, done]
                assert message.content == 'original'
    finally:
        await engine.dispose()
