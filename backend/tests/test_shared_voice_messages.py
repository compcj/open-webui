"""Shared voice HTTP writes against real message persistence in isolated SQLite."""

import ast
import copy
import json
import logging
import sys
import time
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.attributes import flag_modified

BACKEND = Path(__file__).parents[1] / 'open_webui'


def load_source(namespace, path, classes=(), functions=(), methods=None):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    selected = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in classes:
            if methods is not None:
                original = node.body
                node.body = [child for child in original if getattr(child, 'name', None) in methods]
                # Keep the actual normalized row-to-history column mappings.
                node.body.extend(child for child in original if isinstance(child, ast.Assign))
                if not node.body:
                    node.body = [ast.Pass()]
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
            selected.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(path), 'exec'), namespace)


@pytest_asyncio.fixture
async def runtime(tmp_path, monkeypatch):
    # No config/app imports: these paths are defensive isolation if a future helper imports them.
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('STATIC_DIR', str(tmp_path / 'static'))
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'voice.db'))
    base = declarative_base()
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'voice.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
        else:
            async with sessions() as session:
                yield session

    state = SimpleNamespace(
        actor=SimpleNamespace(id='writer', name='Writer', role='user'),
        members={'writer', 'other'},
        share_mode='continue',
        folder=False,
        checks=[],
        emitted=AsyncMock(),
        publish=AsyncMock(),
    )

    async def grant(**kwargs):
        state.checks.append(kwargs)
        return kwargs['user_id'] in state.members and kwargs['permission'] == 'read'

    shared_module = ModuleType('open_webui.models.shared_chats')
    shared_module.SharedChats = SimpleNamespace(
        get_by_chat_id=AsyncMock(
            side_effect=lambda *args, **kwargs: (
                None if state.folder else SimpleNamespace(chat={'share_mode': state.share_mode})
            )
        )
    )
    monkeypatch.setitem(sys.modules, shared_module.__name__, shared_module)
    folder_module = ModuleType('open_webui.utils.access_control.folders')

    async def folder_access(user_id, folder, permission, db):
        return await grant(user_id=user_id, resource_type='folder', permission=permission, db=db)

    folder_module.has_folder_access = folder_access
    monkeypatch.setitem(sys.modules, folder_module.__name__, folder_module)
    namespace = {
        '__name__': __name__,
        **{
            name: getattr(sa, name)
            for name in (
                'Column',
                'String',
                'Text',
                'JSON',
                'BigInteger',
                'Boolean',
                'Index',
                'ForeignKey',
                'select',
                'update',
                'text',
            )
        },
        'Base': base,
        'BaseModel': BaseModel,
        'Literal': Literal,
        'AsyncSession': AsyncSession,
        'ConfigDict': ConfigDict,
        'field_validator': field_validator,
        'Field': Field,
        'asynccontextmanager': asynccontextmanager,
        'nullcontext': nullcontext,
        'get_async_db': context,
        'get_async_db_context': context,
        'copy': copy,
        'deepcopy': copy.deepcopy,
        'time': time,
        'log': logging.getLogger(__name__),
        'flag_modified': flag_modified,
        'get_usage': lambda data: None,
        'get_output_text': lambda output: '',
        'sanitize_data_for_db': copy.deepcopy,
        'ENABLE_ADMIN_CHAT_ACCESS': False,
        'AccessGrants': SimpleNamespace(has_access=grant),
        'Folders': SimpleNamespace(
            get_folder_by_id=AsyncMock(
                side_effect=lambda *args, **kwargs: SimpleNamespace(data={'share_mode': state.share_mode})
            )
        ),
    }
    load_source(
        namespace,
        'models/chats.py',
        classes={'Chat', 'ChatModel', 'ChatResponse', 'ChatForm'},
        functions={'is_internal_chat'},
    )
    load_source(namespace, 'models/chat_messages.py', classes={'ChatMessage'})
    load_source(
        namespace,
        'models/chat_messages.py',
        classes={'ChatMessageTable'},
        methods={'_apply_message_data', '_build_message', '_lock_message_writes', 'upsert_message'},
    )
    namespace['ChatMessageModel'] = SimpleNamespace(model_validate=lambda row: row)
    namespace['ChatMessages'] = namespace['ChatMessageTable']()
    load_source(
        namespace,
        'models/chats.py',
        classes={'ChatTable'},
        methods={
            '_chat_transaction',
            '_clean_null_bytes',
            '_add_child_id_to_parent',
            'upsert_message_to_history',
            'get_accessible_chat_by_id',
            'get_chat_by_id_and_user_id',
            'insert_voice_message',
            'update_voice_message',
            'upsert_message_to_chat_by_id_and_message_id',
        },
    )
    table = namespace['ChatTable']()
    table._repair_chat_current_id = lambda chat: None
    table.get_current_message_id = lambda chat: (chat.get('history') or {}).get('currentId')

    async def get_chat(chat_id, db=None):
        async with context(db) as session:
            row = await session.get(namespace['Chat'], chat_id)
            return namespace['ChatModel'].model_validate(row) if row else None

    async def get_message(chat_id, message_id):
        chat = await get_chat(chat_id)
        return chat.chat['history']['messages'].get(message_id) if chat else None

    table.get_chat_by_id = get_chat
    table.get_message_by_id_and_message_id = get_message
    namespace.update(
        router=APIRouter(),
        Depends=Depends,
        Request=Request,
        HTTPException=HTTPException,
        status=status,
        JSONCodec=json,
        Chats=table,
        ERROR_MESSAGES=SimpleNamespace(ACCESS_PROHIBITED='Forbidden', NOT_FOUND='Not found'),
        get_verified_user=lambda: state.actor,
        get_async_session=lambda: None,
        get_event_emitter=AsyncMock(return_value=state.emitted),
        publish_event=state.publish,
        EVENTS=SimpleNamespace(MESSAGE_UPDATED='updated', CHAT_UPDATED='chat-updated'),
    )
    load_source(
        namespace,
        'routers/chats.py',
        classes={'MessageForm', 'VoiceMessageForm', 'VoiceMetadataForm'},
        functions={
            'chat_response_for_user',
            'voice_message_response_for_user',
            'insert_chat_voice_message',
            'update_chat_voice_message',
            'update_chat_by_id',
            'update_chat_message_by_id',
        },
    )
    app = FastAPI()
    app.include_router(namespace['router'])
    async with engine.begin() as connection:
        await connection.run_sync(base.metadata.create_all)
    raw = {}
    previous = None
    for mid, role, author in [
        ('owner-u', 'user', 'owner'),
        ('writer-u', 'user', 'writer'),
        ('writer-a', 'assistant', 'writer'),
    ]:
        raw[mid] = {
            'id': mid,
            'role': role,
            'user_id': author,
            'parentId': previous,
            'childrenIds': [],
            'content': f'{mid} original',
            'done': role != 'assistant',
            'meta': {
                'request_context': {'private': author},
                'voice': {'speech': [{'item_id': 'old', 'transcript': 'old words'}]},
            },
            'output': [
                {
                    'type': 'function_call_output',
                    'call_id': 'call',
                    'output': 'raw evidence',
                    'citation_context': 'frozen citations',
                }
            ],
        }
        if previous:
            raw[previous]['childrenIds'].append(mid)
        previous = mid
    async with sessions() as session:
        session.add(
            namespace['Chat'](
                id='chat',
                user_id='owner',
                title='Existing title',
                created_at=1,
                updated_at=2,
                variables={'owner': 'private'},
                archived=False,
                chat={
                    'title': 'Existing title',
                    'params': {'system': 'owner private'},
                    'history': {'messages': raw, 'currentId': 'writer-a'},
                },
            )
        )
        await session.flush()
        for mid, message in raw.items():
            await namespace['ChatMessages'].upsert_message(mid, 'chat', message['user_id'], message, db=session)
        await session.commit()
    state.namespace, state.table, state.sessions = namespace, table, sessions
    state.chat = lambda: get_chat('chat')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            state.client = client
            yield state
    finally:
        await engine.dispose()


def transcript(mid='voice-u', **changes):
    return {
        'id': mid,
        'role': 'user',
        'content': 'spoken transcript',
        'parent_id': 'writer-a',
        'branch_id': 'writer-a',
        'voice': {'model': 'voice-model'},
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('folder', [False, True])
async def test_member_saves_their_voice_turn_with_server_author(runtime, folder):
    runtime.folder = folder
    if folder:
        async with runtime.sessions() as session:
            (await session.get(runtime.namespace['Chat'], 'chat')).folder_id = 'folder'
            await session.commit()
    before = copy.deepcopy((await runtime.chat()).chat)
    response = await runtime.client.post(
        '/chat/voice/messages', json=transcript(user_id='owner', user={'id': 'owner'}, output=['forged'])
    )
    assert response.status_code == 200, response.text
    saved = response.json()['messages']['voice-u']
    assert saved['user_id'] == 'writer'
    assert saved['user'] == {'id': 'writer', 'name': 'Writer'}
    assert 'output' not in saved
    assert saved['meta']['voice']['model'] == 'voice-model'
    response = await runtime.client.post(
        '/chat/voice/messages', json=transcript('voice-a', role='assistant', parent_id='voice-u', branch_id='voice-u')
    )
    assert response.status_code == 200, response.text
    chat = await runtime.chat()
    messages = chat.chat['history']['messages']
    assert messages['voice-a']['user_id'] == 'writer'
    assert messages['voice-a']['parentId'] == 'voice-u'
    assert messages['voice-a']['model'] == 'voice-model'
    assert messages['voice-u']['childrenIds'] == ['voice-a']
    assert chat.chat['history']['currentId'] == 'voice-a'
    assert messages['writer-a']['output'] == before['history']['messages']['writer-a']['output']
    assert messages['owner-u'] == before['history']['messages']['owner-u']
    assert chat.chat['params'] == before['params']
    assert chat.title == 'Existing title'
    assert all(check['db'] is not None for check in runtime.checks)
    for event in runtime.emitted.await_args_list:
        assert event.args[0]['type'] == 'chat:messages'
        assert all('meta' not in message for message in event.args[0]['data']['messages'].values())
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-voice-a')
        assert row.user_id == 'writer' and row.parent_id == 'voice-u'


@pytest.mark.asyncio
async def test_voice_update_preserves_private_tool_metadata_and_current_normalized_output(runtime):
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        row.output = [
            {
                'type': 'function_call_output',
                'call_id': 'new-call',
                'output': 'concurrent raw',
                'citation_context': 'new frozen citations',
            }
        ]
        row.content = 'concurrent answer'
        row.meta = {**row.meta, 'tool_budget_accounting': {'model': {'recorded_at': 42}}, 'pinned': 'actor snapshot'}
        await session.commit()
    response = await runtime.client.post(
        '/chat/messages/writer-a/voice', json={'voice': {'speech': [{'item_id': 'new', 'transcript': 'new words'}]}}
    )
    assert response.status_code == 200, response.text
    assert 'meta' not in response.json()['chat']['history']['messages']['owner-u']
    saved = (await runtime.chat()).chat['history']['messages']['writer-a']
    assert saved['meta']['request_context'] == {'private': 'writer'}
    assert saved['meta']['pinned'] == 'actor snapshot'
    assert saved['meta']['tool_budget_accounting'] == {'model': {'recorded_at': 42}}
    assert [item['item_id'] for item in saved['meta']['voice']['speech']] == ['old', 'new']
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        assert row.content == 'concurrent answer'
        assert row.output[0]['output'] == 'concurrent raw'
        assert row.output[0]['citation_context'] == 'new frozen citations'
        assert row.meta == saved['meta']
    emitter_info = runtime.namespace['get_event_emitter'].await_args.args[0]
    assert emitter_info['user_id'] == 'writer'
    assert runtime.emitted.await_args.args[0]['type'] == 'chat:message:voice'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'actor,mode', [('writer', 'clone'), ('stranger', 'continue'), ('revoked', 'continue'), ('pending', 'continue')]
)
@pytest.mark.parametrize('operation', ['insert', 'metadata'])
async def test_non_continuation_member_is_denied_without_persistence(runtime, actor, mode, operation):
    runtime.actor = SimpleNamespace(id=actor, name=actor, role='pending' if actor == 'pending' else 'user')
    runtime.share_mode = mode
    before = (await runtime.chat()).model_dump()
    path, payload = (
        ('/chat/voice/messages', transcript())
        if operation == 'insert'
        else ('/chat/messages/writer-a/voice', {'voice': {'speech': []}})
    )
    response = await runtime.client.post(path, json=payload)
    assert response.status_code == 403, response.text
    assert (await runtime.chat()).model_dump() == before
    runtime.emitted.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_cannot_update_another_author_or_gain_generic_write_access(runtime):
    response = await runtime.client.post('/chat/messages/owner-u/voice', json={'voice': {'speech': []}})
    assert response.status_code == 403, response.text
    response = await runtime.client.post('/chat/messages/writer-a', json={'content': 'forged generic edit'})
    assert response.status_code == 401
    response = await runtime.client.post('/chat', json={'chat': {'params': {'system': 'forged'}}})
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'changes,status_code',
    [
        ({'id': 'writer-u'}, 409),
        ({'parent_id': 'missing'}, 409),
        ({'parent_id': 'other-chat-message'}, 409),
        ({'branch_id': 'missing'}, 409),
        ({'role': 'system'}, 422),
        ({'id': 'voice' + chr(0)}, 422),
    ],
)
async def test_invalid_or_existing_message_cannot_rewrite_history(runtime, changes, status_code):
    before = (await runtime.chat()).model_dump()
    response = await runtime.client.post('/chat/voice/messages', json=transcript(**changes))
    assert response.status_code == status_code, response.text
    assert (await runtime.chat()).model_dump() == before


@pytest.mark.asyncio
async def test_missing_voice_metadata_target_does_not_create_message(runtime):
    response = await runtime.client.post('/chat/messages/missing/voice', json={'voice': {}})
    assert response.status_code == 404
    assert 'missing' not in (await runtime.chat()).chat['history']['messages']


@pytest.mark.asyncio
@pytest.mark.parametrize('next_author', ['writer', 'other'])
async def test_delayed_reply_reparents_only_the_same_actors_next_turn(runtime, next_author):
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['Chat'], 'chat')
        history = copy.deepcopy(row.chat['history'])
        next_message = {
            'id': 'next',
            'role': 'user',
            'parentId': 'writer-a',
            'childrenIds': [],
            'content': 'next question',
            'user_id': next_author,
            'meta': {'private': next_author},
        }
        history['messages']['next'] = next_message
        history['messages']['writer-a']['childrenIds'] = ['next']
        history['currentId'] = 'next'
        row.chat = {**row.chat, 'history': history}
        await runtime.namespace['ChatMessages'].upsert_message('next', 'chat', next_author, next_message, db=session)
        await session.commit()
    response = await runtime.client.post(
        '/chat/voice/messages', json=transcript('voice-a', role='assistant', parent_id='writer-u', branch_id='next')
    )
    assert response.status_code == 200, response.text
    history = (await runtime.chat()).chat['history']
    assert history['messages']['voice-a']['parentId'] == 'writer-a'
    assert history['currentId'] == 'next'
    if next_author == 'writer':
        assert history['messages']['next']['parentId'] == 'voice-a'
        assert history['messages']['writer-a']['childrenIds'] == ['voice-a']
        assert history['messages']['voice-a']['childrenIds'] == ['next']
    else:
        assert history['messages']['next'] == next_message
        assert history['messages']['writer-a']['childrenIds'] == ['next', 'voice-a']
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-next')
        assert row.parent_id == ('voice-a' if next_author == 'writer' else 'writer-a')
        assert row.content == 'next question'
        assert row.meta == {'private': next_author}


@pytest.mark.asyncio
async def test_owner_can_save_own_voice_and_uses_existing_generic_content_api(runtime):
    runtime.actor = SimpleNamespace(id='owner', name='Owner', role='user')
    response = await runtime.client.post('/chat/voice/messages', json=transcript())
    assert response.status_code == 200, response.text
    assert response.json()['messages']['voice-u']['user_id'] == 'owner'
    response = await runtime.client.post('/chat/messages/voice-u/voice', json={'voice': {'speech': []}})
    assert response.status_code == 200, response.text
    response = await runtime.client.post('/chat/messages/owner-u', json={'content': 'owner edit'})
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['insert', 'metadata'])
async def test_revocation_after_initial_check_is_rechecked_inside_transaction(runtime, operation):
    original = runtime.table._chat_transaction

    @asynccontextmanager
    async def revoke_then_lock(chat_id):
        runtime.members.discard('writer')
        async with original(chat_id) as locked:
            yield locked

    runtime.table._chat_transaction = revoke_then_lock
    before = (await runtime.chat()).model_dump()
    path, payload = (
        ('/chat/voice/messages', transcript())
        if operation == 'insert'
        else ('/chat/messages/writer-a/voice', {'voice': {}})
    )
    response = await runtime.client.post(path, json=payload)
    assert response.status_code == 403, response.text
    assert (await runtime.chat()).model_dump() == before


@pytest.mark.asyncio
async def test_new_voice_route_does_not_reserve_an_existing_message_id(runtime):
    runtime.actor = SimpleNamespace(id='owner', name='Owner', role='user')
    response = await runtime.client.post('/chat/messages/voice', json={'content': 'owner legacy message'})
    assert response.status_code == 200, response.text
    assert (await runtime.chat()).chat['history']['messages']['voice']['content'] == 'owner legacy message'


@pytest.mark.asyncio
async def test_normalized_author_is_authoritative_for_metadata(runtime):
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        row.user_id = 'other'
        await session.commit()
    before = (await runtime.chat()).model_dump()
    response = await runtime.client.post('/chat/messages/writer-a/voice', json={'voice': {'speech': []}})
    assert response.status_code == 403, response.text
    assert (await runtime.chat()).model_dump() == before


@pytest.mark.asyncio
async def test_cross_chat_parent_is_denied_even_if_the_foreign_message_exists(runtime):
    async with runtime.sessions() as session:
        session.add(
            runtime.namespace['Chat'](
                id='another-chat',
                user_id='writer',
                title='Other',
                created_at=1,
                updated_at=2,
                chat={'history': {'messages': {}, 'currentId': None}},
            )
        )
        await runtime.namespace['ChatMessages'].upsert_message(
            'foreign', 'another-chat', 'writer', {'role': 'user', 'content': 'other chat'}, db=session
        )
        await session.commit()
    response = await runtime.client.post('/chat/voice/messages', json=transcript(parent_id='foreign'))
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
async def test_metadata_backfill_preserves_legacy_tool_output_and_citations(runtime):
    before = (await runtime.chat()).chat['history']['messages']['writer-a']
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        await session.delete(row)
        await session.commit()
    response = await runtime.client.post('/chat/messages/writer-a/voice', json={'voice': {'speech': []}})
    assert response.status_code == 200, response.text
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        assert row.output == before['output']
        assert row.content == before['content']
        assert row.meta['request_context'] == before['meta']['request_context']
        assert row.parent_id == before['parentId']


@pytest.mark.asyncio
async def test_delayed_voice_reparent_preserves_legacy_next_turn(runtime):
    next_message = {
        'id': 'legacy-next',
        'role': 'user',
        'user_id': 'writer',
        'parentId': 'writer-a',
        'childrenIds': [],
        'content': 'next spoken question',
        'files': [{'id': 'attachment'}],
        'meta': {'request_context': {'private': 'writer'}},
    }
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['Chat'], 'chat')
        history = copy.deepcopy(row.chat['history'])
        history['messages'][next_message['id']] = next_message
        history['messages']['writer-a']['childrenIds'] = [next_message['id']]
        history['currentId'] = next_message['id']
        row.chat = {**row.chat, 'history': history}
        await session.commit()
    response = await runtime.client.post(
        '/chat/voice/messages',
        json=transcript('voice-a', role='assistant', parent_id='writer-u', branch_id='legacy-next'),
    )
    assert response.status_code == 200, response.text
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-legacy-next')
        assert row.content == next_message['content']
        assert row.files == next_message['files']
        assert row.meta == next_message['meta']
        assert row.parent_id == 'voice-a'


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_voice', ['legacy scalar', {'speech': None}, {'speech': {'legacy': True}}])
async def test_voice_metadata_update_tolerates_malformed_historical_fields(runtime, legacy_voice):
    async with runtime.sessions() as session:
        chat = await session.get(runtime.namespace['Chat'], 'chat')
        history = copy.deepcopy(chat.chat['history'])
        history['messages']['writer-a']['meta']['voice'] = legacy_voice
        chat.chat = {**chat.chat, 'history': history}
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        row.meta = {**row.meta, 'voice': legacy_voice}
        await session.commit()
    response = await runtime.client.post(
        '/chat/messages/writer-a/voice', json={'voice': {'speech': [{'item_id': 'new', 'transcript': 'new words'}]}}
    )
    assert response.status_code == 200, response.text
    saved = (await runtime.chat()).chat['history']['messages']['writer-a']
    assert saved['meta']['request_context'] == {'private': 'writer'}
    assert saved['meta']['voice']['speech'] == [{'item_id': 'new', 'transcript': 'new words'}]
    async with runtime.sessions() as session:
        row = await session.get(runtime.namespace['ChatMessage'], 'chat-writer-a')
        assert row.output == saved['output']
        assert row.meta == saved['meta']
