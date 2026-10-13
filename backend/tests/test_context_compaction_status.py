"""Compaction status delivery and reload against isolated SQLite storage."""

import ast
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, JSON, Text, Integer, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.attributes import flag_modified

from test_web_tool_citations import load_functions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'kind,persist',
    [('context_compaction', True), ('tool_result_compaction', True), ('status', True), ('context_usage', False)],
)
async def test_socket_persists_lifecycle_but_not_usage(kind, persist):
    chats = SimpleNamespace(
        add_message_status_to_chat_by_id_and_message_id=AsyncMock(),
        get_accessible_chat_by_id=AsyncMock(return_value=SimpleNamespace(id='chat')),
    )
    sio = SimpleNamespace(emit=AsyncMock(), manager=SimpleNamespace(rooms={'/': {'user:owner': {}}}))
    namespace = dict(
        Chats=chats,
        sio=sio,
        WEBSOCKET_MANAGER='',
        is_saved_chat_id=lambda id: id == 'chat',
        Users=SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id='owner'))),
    )
    load_functions(namespace, 'socket/main.py', {'get_event_emitter'})
    emitter = await namespace['get_event_emitter']({'user_id': 'owner', 'chat_id': 'chat', 'message_id': 'a'})
    status = {'action': 'context_compaction', 'mode': 'v2', 'model': 'model', 'done': True}
    await emitter({'type': kind, 'data': status})
    assert sio.emit.await_args.args[1]['message_id'] == 'a'
    chats.get_accessible_chat_by_id.assert_awaited_once()
    assert chats.add_message_status_to_chat_by_id_and_message_id.await_count == int(persist)
    if persist:
        chats.add_message_status_to_chat_by_id_and_message_id.assert_awaited_once_with('chat', 'a', status)


@pytest.mark.asyncio
@pytest.mark.parametrize('embedded,normalized', [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize('tool_operation', [False, True])
async def test_status_survives_reload_in_both_history_stores(tmp_path, embedded, normalized, tool_operation):
    base = declarative_base()

    class Chat(base):
        __tablename__ = 'chat'
        id = Column(Text, primary_key=True)
        chat = Column(JSON)
        user_id = Column(Text)
        title = Column(Text)
        current_message_id = Column(Text)
        updated_at = Column(Integer)

    class ChatMessage(base):
        __tablename__ = 'chat_message'
        id = Column(Text, primary_key=True)
        status_history = Column(JSON)
        user_id = Column(Text)
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
    methods = [
        n
        for n in cls.body
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name in {'_chat_transaction', 'add_message_status_to_chat_by_id_and_message_id'}
    ]

    async def upsert_message(message_id, chat_id, user_id, data, db):
        # Persist the newly normalized row in the caller's real transaction.
        message = await db.get(ChatMessage, f'{chat_id}-{message_id}')
        if message is None:
            message = ChatMessage(id=f'{chat_id}-{message_id}', user_id=user_id)
            db.add(message)
        message.status_history = data.get('statusHistory') or []
        message.content = data.get('content')
        message.updated_at = int(time.time())

    namespace = dict(
        Chat=Chat,
        ChatMessage=ChatMessage,
        get_async_db=context,
        asynccontextmanager=asynccontextmanager,
        text=text,
        ChatMessages=SimpleNamespace(upsert_message=upsert_message),
        ChatModel=SimpleNamespace(model_validate=lambda row: row),
        flag_modified=flag_modified,
        time=time,
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
            ast.ClassDef(name='ChatTable', bases=[], keywords=[], body=methods, decorator_list=[]),
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    table = namespace['ChatTable']()
    table._clean_null_bytes = lambda x: x
    table._repair_chat_current_id = lambda chat: None
    table.get_current_message_id = lambda chat: chat['history']['currentId']
    previous = {'action': 'web_search', 'done': True}
    start = {'action': 'context_compaction', 'done': False}
    done = {'action': 'context_compaction', 'done': True, 'before_tokens': 90000, 'after_tokens': 20000}
    if tool_operation:
        start.update(action='tool_result_compaction', operation_id='op', source_message_id='a', method='summary')
        done.update(action='tool_result_compaction', operation_id='op', source_message_id='a', method='summary')
    expected = [previous, done] if tool_operation else [previous, start, done]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            messages = {'a': {'content': 'original', 'statusHistory': [previous]}} if embedded else {}
            session.add(Chat(id='chat', user_id='owner', chat={'history': {'messages': messages, 'currentId': 'a'}}))
            if normalized:
                session.add(ChatMessage(id='chat-a', content='original', status_history=[previous]))
            await session.commit()
        for status in (start, done, done, start) if tool_operation else (start, done):
            result = await table.add_message_status_to_chat_by_id_and_message_id('chat', 'a', status)
            assert result is not None
        async with sessions() as session:
            chat = await session.get(Chat, 'chat')
            if embedded:
                message = chat.chat['history']['messages']['a']
                assert message['statusHistory'] == expected
                assert message['content'] == 'original'
            message = await session.get(ChatMessage, 'chat-a')
            assert message is not None
            assert message.status_history == expected
            assert message.content == 'original'
            if not normalized:
                assert message.user_id == 'owner'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'data',
    [
        {'method': 'original', 'done': True},
        {'method': 'read', 'done': True},
        {'method': 'summary', 'done': True, 'source_message_id': 'older'},
        {'method': 'summary', 'done': False, 'source_message_id': 'older'},
    ],
)
async def test_socket_rejects_non_notifications_before_emit_and_save(data):
    chats = SimpleNamespace(
        add_message_status_to_chat_by_id_and_message_id=AsyncMock(),
        get_accessible_chat_by_id=AsyncMock(return_value=SimpleNamespace(id='chat')),
    )
    sio = SimpleNamespace(emit=AsyncMock(), manager=SimpleNamespace(rooms={'/': {'user:owner': {}}}))
    namespace = dict(
        Chats=chats,
        sio=sio,
        WEBSOCKET_MANAGER='',
        is_saved_chat_id=lambda id: id == 'chat',
        Users=SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id='owner'))),
    )
    load_functions(namespace, 'socket/main.py', {'get_event_emitter'})
    emitter = await namespace['get_event_emitter']({'user_id': 'owner', 'chat_id': 'chat', 'message_id': 'a'})
    await emitter({'type': 'tool_result_compaction', 'data': {'action': 'tool_result_compaction', **data}})
    sio.emit.assert_not_awaited()
    chats.add_message_status_to_chat_by_id_and_message_id.assert_not_awaited()
