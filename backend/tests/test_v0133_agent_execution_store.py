import asyncio
import os
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models import chat_messages, chats
from open_webui.models.chat_messages import ChatMessage, ChatMessages
from open_webui.models.chats import Chat, Chats


@pytest.fixture
def event_loop_policy():
    if sys.platform == 'win32':
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.mark.asyncio
@pytest.mark.parametrize('dialect', ['sqlite', 'postgresql'])
async def test_agent_claim_is_atomic_and_survives_ordinary_message_writes(tmp_path, monkeypatch, dialect):
    schema = None
    if dialect == 'postgresql':
        url = os.getenv('TEST_POSTGRES_URL')
        if not url:
            pytest.skip('Set TEST_POSTGRES_URL to exercise the Agent journal on PostgreSQL')
        schema = 'v0133_agent_' + uuid4().hex
        engine = create_async_engine(url, execution_options={'schema_translate_map': {None: schema}})
    else:
        engine = create_async_engine(f'sqlite+aiosqlite:///{(tmp_path / "agents.db").as_posix()}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        async with sessions() as session:
            yield session

    monkeypatch.setattr(chats, 'get_async_db', session_context)
    monkeypatch.setattr(chats, 'get_async_db_context', session_context)
    monkeypatch.setattr(chat_messages, 'get_async_db_context', session_context)
    try:
        async with engine.begin() as connection:
            if schema:
                await connection.execute(text(f'CREATE SCHEMA {schema}'))
            await connection.run_sync(Chat.__table__.create)
            await connection.run_sync(ChatMessage.__table__.create)
        async with sessions() as session:
            history = {'messages': {key: {'id': key, 'role': 'assistant', 'done': False} for key in ('a', 'b')}}
            session.add(
                Chat(id='chat', user_id='owner', title='Chat', chat={'history': history}, created_at=1, updated_at=1)
            )
            await session.flush()
            for message_id in ('a', 'b'):
                session.add(
                    ChatMessage(
                        id=f'chat-{message_id}',
                        chat_id='chat',
                        user_id='owner',
                        role='assistant',
                        created_at=1,
                        updated_at=1,
                    )
                )
            await session.commit()
        record = {'user_id': 'owner', 'task_id': 'task', 'state': 'starting'}
        assert not await Chats.claim_agent_execution('chat', 'a', {**record, 'user_id': 'other'})
        claims = await asyncio.gather(
            Chats.claim_agent_execution('chat', 'a', record),
            Chats.claim_agent_execution('chat', 'b', {**record, 'task_id': 'another'}),
        )
        assert claims.count(True) == 1
        active = await ChatMessages.get_unconfirmed_agent_execution('chat')
        message_id, task_id = active['message_id'], active['task_id']
        await ChatMessages.upsert_message(
            message_id, 'chat', 'owner', {'meta': {'agent_execution': {'state': 'completed'}}, 'done': True}
        )
        assert await ChatMessages.get_unconfirmed_agent_execution('chat') == active
        with pytest.raises(HTTPException, match='not confirmed completion'):
            await Chats.delete_chat_by_id_and_user_id('chat', 'owner')
        assert not await Chats.update_agent_execution('chat', message_id, 'wrong-task', state='completed')
        assert await Chats.update_agent_execution('chat', message_id, task_id, state='unconfirmed')
        assert (await ChatMessages.get_unconfirmed_agent_execution('chat'))['state'] == 'unconfirmed'
        assert await Chats.update_agent_execution('chat', message_id, task_id, session_id='remote-session')
        raw = ' {"synthetic": "private protocol frame"} \n'
        assert not await Chats.append_agent_output_frame('chat', message_id, 'wrong-task', 'remote-session', 1, raw)
        assert not await Chats.append_agent_output_frame('chat', message_id, task_id, 'wrong-session', 1, raw)
        assert not await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 2, raw)
        assert await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 1, raw)
        assert await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 1, raw)
        assert not await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 1, '{}')
        async with Chats.edit_message_output('chat', message_id) as reply:
            assert 'output_journal' not in reply['meta']['agent_execution']
            reply['meta'] = {'agent_execution': {'output_journal': {'frames': ['forged']}}}
        await Chats.update_chat_by_id(
            'chat',
            {
                'history': {
                    'messages': {
                        message_id: {
                            'id': message_id,
                            'role': 'assistant',
                            'meta': {'agent_execution': {'output_journal': {}}},
                        }
                    }
                }
            },
        )
        saved_message = await Chats.get_message_by_id_and_message_id('chat', message_id)
        assert 'output_journal' not in saved_message['meta']['agent_execution']
        saved_row = await ChatMessages.get_messages_by_chat_id('chat')
        assert all('output_journal' not in (message.meta or {}).get('agent_execution', {}) for message in saved_row)
        assert 'output_journal' not in await ChatMessages.get_unconfirmed_agent_execution('chat')
        assert 'output_journal' not in await Chats.get_agent_execution('chat', message_id)
        async with sessions() as session:
            projection = ChatMessages._select_public_message_rows(session).where(ChatMessage.id == f'chat-{message_id}')
            projected = (await session.execute(projection)).mappings().one()
            assert 'output_journal' not in projected['meta']['agent_execution']
        assert await Chats.get_agent_approval_output('chat', message_id, 'wrong-task') is None
        assert await Chats.get_agent_approval_output('chat', message_id, task_id) == []

        await ChatMessages.upsert_message(
            message_id,
            'chat',
            'owner',
            {'done': False, 'output': [{'type': 'function_call', 'status': 'pending', 'id': 'approval'}]},
        )
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, state='completed', complete_reply=True, remote_completed=True
        )
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, state='completed', complete_reply=True, output_published=True
        )
        assert not await Chats.update_agent_execution('chat', message_id, task_id, output_published=True)
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, output_published=True, output_cursor=0
        )
        frame_size = len(raw.encode('utf-8'))
        with monkeypatch.context() as limits:
            limits.setattr(chats, 'AGENT_NATIVE_OUTPUT_MAX_BYTES', frame_size * 2 - 1)
            assert (
                await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 2, raw)
                == 'capacity'
            )
        assert (await ChatMessages.get_unconfirmed_agent_execution('chat'))['output_capacity_reached'] is True
        assert (
            await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 2, raw) == 'capacity'
        )
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, output_published=True, output_cursor=1
        )
        recovery = await Chats.acquire_agent_stop('chat', message_id, task_id, 'attempt-a', recover_unpublished=True)
        assert recovery['output_cursor'] == 2 and recovery['stop_recovering'] is True
        assert not await Chats.acquire_agent_stop('chat', message_id, task_id, 'attempt-b', recover_unpublished=True)
        assert not await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 2, raw)
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, output_published=True, output_cursor=2
        )
        assert await Chats.update_agent_execution(
            'chat',
            message_id,
            task_id,
            output_published=True,
            output_cursor=2,
            output_incomplete=True,
            stop_attempt_id='attempt-a',
        )
        assert not await Chats.append_agent_output_frame('chat', message_id, task_id, 'remote-session', 3, raw)
        assert not await Chats.update_agent_execution('chat', message_id, task_id, remote_completed=True)
        assert not await Chats.update_agent_execution(
            'chat', message_id, task_id, state='completed', complete_reply=True, stop_attempt_id='attempt-a'
        )
        assert await Chats.update_agent_execution(
            'chat', message_id, task_id, state='unconfirmed', stop_attempt_id='attempt-a'
        )
        assert (await ChatMessages.get_unconfirmed_agent_execution('chat'))['state'] == 'unconfirmed'
        assert await Chats.update_agent_execution(
            'chat', message_id, task_id, stop_attempt_id='attempt-a', release_stop_attempt=True
        )
        assert await Chats.acquire_agent_stop('chat', message_id, task_id, 'attempt-b')
        assert await Chats.update_agent_execution(
            'chat',
            message_id,
            task_id,
            state='completed',
            remote_completed=True,
            complete_reply=True,
            stop_attempt_id='attempt-b',
        )
        assert await ChatMessages.get_unconfirmed_agent_execution('chat') is None
        async with sessions() as session:
            reply = await session.get(ChatMessage, f'chat-{message_id}')
            assert reply.done is True
            assert reply.error['code'] == 'agent_output_incomplete'
            assert reply.output[0]['status'] == 'incomplete'
            assert reply.meta['response_ended_at'] > 0
            assert 'complete_reply' not in reply.meta['agent_execution']
            assert reply.meta['agent_execution']['output_published'] is True
            assert reply.meta['agent_execution']['remote_completed'] is True
            assert reply.meta['agent_execution']['output_journal']['frames'] == [
                {'sequence': 1, 'data': raw},
                {'sequence': 2, 'data': raw},
            ]
            assert 'output_retention_blocked' not in reply.meta['agent_execution']
            chat = await session.get(Chat, 'chat')
            embedded = chat.chat['history']['messages'][message_id]
            assert embedded['done'] is True
            assert embedded['error'] == reply.error
            assert embedded['output'] == reply.output
            assert embedded['meta']['response_ended_at'] == reply.meta['response_ended_at']
            assert 'output_journal' not in repr(embedded)
        assert not await Chats.update_agent_execution('chat', message_id, task_id, state='running')
        assert not await Chats.claim_agent_execution('chat', message_id, record)
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()
