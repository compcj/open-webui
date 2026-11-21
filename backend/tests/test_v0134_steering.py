"""Saved steering advances after the publishing worker confirms its exact reply and task."""

import copy
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models import chats, chat_messages
from test_v0133_agent_execution_store import event_loop_policy

from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, Chats
from test_v0133_agent_admission import ACTOR, agent_database


@pytest_asyncio.fixture(params=['sqlite', 'postgresql'])
async def steering_database(request, agent_database, monkeypatch):
    if request.param == 'sqlite':
        yield agent_database
        return
    url = os.getenv('TEST_POSTGRES_URL')
    if not url:
        pytest.skip('Set TEST_POSTGRES_URL for PostgreSQL steering tests')
    schema = 'steering_' + uuid4().hex
    engine = create_async_engine(url, execution_options={'schema_translate_map': {None: schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with sessions() as db:
                yield db

    monkeypatch.setattr(chats, 'get_async_db', context)
    monkeypatch.setattr(chat_messages, 'get_async_db_context', context)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA {schema}'))
            await conn.run_sync(Chat.__table__.create)
            await conn.run_sync(ChatMessage.__table__.create)
        yield sessions
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()


@pytest.mark.asyncio
async def test_steering_requires_committed_results_and_every_bound_worker(steering_database):
    message = {
        'id': 'reply',
        'role': 'assistant',
        'model': 'ordinary',
        'done': False,
        'user_id': ACTOR.id,
        'parentId': 'user',
        'childrenIds': [],
    }
    async with steering_database() as db:
        db.add(
            Chat(
                id='chat',
                user_id=ACTOR.id,
                title='chat',
                chat={'history': {'messages': {'reply': message}}},
                created_at=1,
                updated_at=1,
            )
        )
        await ChatMessages.upsert_message('reply', 'chat', ACTOR.id, message, db=db)
        await db.commit()
    await Chats.bind_steering_task('chat', 'reply', ACTOR.id, 'a')
    queued = {'id': 'held', 'content': 'next'}
    assert not await Chats.enqueue_steering('chat', 'reply', SimpleNamespace(id='other'), queued)
    assert await Chats.enqueue_steering('chat', 'reply', ACTOR, queued)
    assert await Chats.enqueue_steering('chat', 'reply', ACTOR, queued)
    with pytest.raises(Exception):
        await Chats.enqueue_steering('chat', 'reply', ACTOR, {**queued, 'content': 'different'})
    call = {'type': 'function_call', 'call_id': 'call', 'status': 'completed'}
    async with steering_database() as db:
        row = await db.get(ChatMessage, 'chat-reply')
        row.done, row.output = True, [call]
        await db.commit()
    assert not await Chats.publish_steering_output('chat', 'reply', 'a', [call])
    await Chats.bind_steering_task('chat', 'reply', ACTOR.id, 'b', continuing=True)
    output = [call, {'type': 'function_call_output', 'call_id': 'call', 'output': [], 'status': 'completed'}]
    async with steering_database() as db:
        row = await db.get(ChatMessage, 'chat-reply')
        row.output = output
        await db.commit()
    assert not await Chats.publish_steering_output('chat', 'reply', 'a', output)
    assert await Chats.publish_steering_output('chat', 'reply', 'b', output)
    assert not await Chats.confirm_steering_task('chat', 'reply', ACTOR.id, 'b')
    assert await Chats.confirm_steering_task('chat', 'reply', ACTOR.id, 'a')
    assert not await Chats.confirm_steering_task('chat', 'reply', 'other', 'a')
    async with steering_database() as db:
        row = await db.get(ChatMessage, 'chat-reply')
        assert row.meta['steering_run']['confirmed']
        assert len(row.meta['steering_run']['entries']) == 1
    public = await ChatMessages.get_message_by_id('chat-reply')
    assert 'steering_run' not in (public.meta or {})


@pytest.mark.asyncio
async def test_client_snapshots_cannot_supply_steering_execution_provenance(steering_database):
    supplied = {'steering_run': {'confirmed': True, 'entries': ['untrusted']}}
    async with steering_database() as db:
        db.add(Chat(id='chat', user_id=ACTOR.id, title='chat', chat={}, created_at=1, updated_at=1))
        await db.flush()
        await ChatMessages.upsert_message(
            'reply', 'chat', ACTOR.id, {'role': 'assistant', 'meta': copy.deepcopy(supplied)}, db=db
        )
        await db.commit()
        row = await db.get(ChatMessage, 'chat-reply')
        assert 'steering_run' not in row.meta


@pytest.mark.asyncio
async def test_prepared_steering_reuses_ids_until_one_worker_binds(steering_database):
    parent = {
        'id': 'source',
        'role': 'assistant',
        'user_id': ACTOR.id,
        'model': 'ordinary',
        'done': False,
        'childrenIds': [],
    }
    async with steering_database() as db:
        db.add(
            Chat(
                id='chat',
                user_id=ACTOR.id,
                title='chat',
                chat={'history': {'messages': {'source': parent}}},
                created_at=1,
                updated_at=1,
            )
        )
        await ChatMessages.upsert_message('source', 'chat', ACTOR.id, parent, db=db)
        await db.commit()
    await Chats.bind_steering_task('chat', 'source', ACTOR.id, 'worker')
    message = {'id': 'queued', 'content': 'new question', 'files': []}
    assert await Chats.enqueue_steering('chat', 'source', ACTOR, message)
    async with steering_database() as db:
        row = await db.get(ChatMessage, 'chat-source')
        row.done, row.output = True, []
        await db.commit()
    assert await Chats.publish_steering_output('chat', 'source', 'worker', [])
    assert await Chats.confirm_steering_task('chat', 'source', ACTOR.id, 'worker')
    async with Chats._chat_transaction('chat') as (db, chat):
        assert (await Chats.take_steering_messages(db, chat, 'source', ACTOR.id))[0]['id'] == 'queued'
        combined = {'id': 'combined', 'role': 'user', 'content': 'new question', 'parentId': 'source'}
        await ChatMessages.upsert_message('combined', 'chat', ACTOR.id, combined, db=db)
        await ChatMessages.upsert_message(
            'next',
            'chat',
            ACTOR.id,
            {'role': 'assistant', 'model': 'ordinary', 'done': False, 'parentId': 'combined'},
            db=db,
        )
        await Chats.record_steering_dispatch(
            db, 'chat', 'source', combined, 'next', {'model_id': 'ordinary', 'variables': {'private': True}}
        )
    assert await Chats.enqueue_steering('chat', 'source', ACTOR, message) == 'retry'
    async with Chats._chat_transaction('chat') as (db, chat):
        retry = (await Chats.take_steering_messages(db, chat, 'source', ACTOR.id))[0]
        assert retry['_prepared_reply'] == 'next' and retry['id'] == 'combined'
        assert 'variables' not in retry['_saved_run']
        assert await ChatMessages.has_unsettled_steering('chat', db=db)
    assert await Chats.bind_steering_task('chat', 'next', ACTOR.id, 'new-worker')
    async with Chats._chat_transaction('chat') as (db, chat):
        assert not await Chats.take_steering_messages(db, chat, 'source', ACTOR.id)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as conflict:
        await Chats.bind_steering_task('chat', 'next', ACTOR.id, 'duplicate-worker')
    assert conflict.value.status_code == 409


def test_queue_keeps_multimodal_content_and_text_in_order():
    from test_web_tool_citations import load_functions

    ns = {'copy': copy}
    load_functions(ns, 'utils/subagents.py', {'queued_content'})
    image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,dGVzdA=='}}
    result = ns['queued_content']([{'content': 'first'}, {'content': [image]}])
    assert result == [{'type': 'text', 'text': 'first'}, {'type': 'text', 'text': '\n\n'}, image]
    assert result[-1] is not image


@pytest.mark.asyncio
async def test_steering_binding_is_between_registry_and_worker_start():
    from unittest.mock import AsyncMock
    from test_task_cancellation import RedisDouble, worker
    import asyncio

    registry, redis = worker(), RedisDouble()
    events = []

    async def bind():
        assert await registry['redis_list_tasks'](redis, 'chat') == ['work']
        events.append('bound')

    async def work():
        events.append('started')

    _, task = await registry['create_task'](redis, work(), id='chat', task_id='work', before_start=bind)
    await task
    assert events == ['bound', 'started']

    registry['redis_save_task'] = AsyncMock(side_effect=ConnectionError('unavailable'))
    binding, effect, confirm = AsyncMock(), AsyncMock(), AsyncMock()
    with pytest.raises(ConnectionError):
        await registry['create_task'](
            redis, effect(), id='chat', task_id='retry', before_start=binding, on_worker_exit=confirm
        )
    binding.assert_not_awaited()
    effect.assert_not_awaited()
    confirm.assert_not_awaited()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_rejected_steering_binding_retires_only_its_unstarted_registry_entry():
    from unittest.mock import AsyncMock
    from test_task_cancellation import RedisDouble, worker

    registry, redis = worker(), RedisDouble()
    await registry['redis_save_task'](redis, 'running', 'chat')
    effect, confirm = AsyncMock(), AsyncMock()
    reject = AsyncMock(side_effect=ValueError('already bound'))
    with pytest.raises(ValueError, match='already bound'):
        await registry['create_task'](
            redis, effect(), id='chat', task_id='duplicate', before_start=reject, on_worker_exit=confirm
        )
    assert await registry['redis_list_tasks'](redis, 'chat') == ['running']
    effect.assert_not_awaited()
    confirm.assert_not_awaited()
