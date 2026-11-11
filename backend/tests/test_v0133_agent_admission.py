"""Exercise Agent admission before completion can change saved history."""

import ast
import asyncio
import copy
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models import chat_messages, chats
from open_webui.models.chat_messages import ChatMessage
from open_webui.models.chats import Chat, Chats
from open_webui.utils import tool_permissions
from open_webui.utils.agents import discovery
from open_webui.utils.chat_id import is_saved_chat_id, is_temporary_chat_id
from open_webui.utils.context_store_errors import ContextStoreError

AGENT = 'agent:terminal:codex:model'
ACTOR = SimpleNamespace(id='owner', name='Owner', role='admin')


@pytest.fixture
def completion_chat_access():
    return AsyncMock(return_value=SimpleNamespace(user_id=ACTOR.id, variables={}))


@pytest.fixture
def completion_client(monkeypatch, completion_chat_access):
    # Load the production endpoint without running main's app or startup hooks.
    path = Path(__file__).parents[1] / 'open_webui/main.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    endpoint = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    app = FastAPI()
    app.state.MODELS = {'ordinary': {'id': 'ordinary'}}
    chat = SimpleNamespace(user_id=ACTOR.id, variables={})
    monkeypatch.setattr(discovery, 'get_models', AsyncMock(return_value=[{'id': AGENT, 'agent': {}}]))
    monkeypatch.setattr(tool_permissions, 'resolve_tool_approval_mode', AsyncMock(return_value='off'))

    async def verified_user():
        return ACTOR

    async def unexpected_write(*args, **kwargs):
        raise HTTPException(418, 'Reached chat mutation')

    @asynccontextmanager
    async def edit_message_output(*args, **kwargs):
        raise HTTPException(418, 'Reached historical reply mutation')
        yield

    chat_table = SimpleNamespace(
        get_chat_by_id=AsyncMock(return_value=chat),
        get_accessible_chat_by_id=completion_chat_access,
        get_message_by_id_and_message_id=AsyncMock(
            return_value={'id': 'old-reply', 'model': AGENT, 'user_id': ACTOR.id, 'parentId': 'old-user'}
        ),
        insert_chat_turn=unexpected_write,
        edit_message_output=edit_message_output,
        update_chat_by_id=unexpected_write,
        upsert_message_to_chat_by_id_and_message_id=unexpected_write,
    )
    namespace = {
        'app': app,
        'Request': Request,
        'Depends': Depends,
        'get_verified_user': verified_user,
        'HTTPException': HTTPException,
        'ERROR_MESSAGES': SimpleNamespace(NOT_FOUND='Not found'),
        'ContextStoreError': ContextStoreError,
        'status': status,
        'check_chat_access': AsyncMock(),
        '_check_voice_chat_access': AsyncMock(),
        '_set_direct_model': AsyncMock(),
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'Config': SimpleNamespace(get=AsyncMock(return_value={})),
        'Chats': chat_table,
        'ChatMessages': SimpleNamespace(get_unconfirmed_agent_execution=AsyncMock(return_value=None)),
        'BYPASS_MODEL_ACCESS_CONTROL': True,
        'merge_model_params': lambda defaults, overrides: {**defaults, **overrides},
        'normalize_chat_variables': lambda variables: variables,
        'is_saved_chat_id': is_saved_chat_id,
        'is_temporary_chat_id': is_temporary_chat_id,
        'temporary_request_store': lambda request: None,
        'uuid4': lambda: 'synthetic-task',
        'copy': copy,
        'log': logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(path), 'exec'), namespace)
    return TestClient(app)


@pytest.mark.parametrize('model_id', [AGENT, 'ordinary'])
@pytest.mark.parametrize('action', ['assistant_message_id', 'regeneration_message_id'])
def test_agent_reply_cannot_be_continued_or_regenerated(completion_client, model_id, action):
    response = completion_client.post(
        '/api/chat/completions',
        json={
            'chat_id': 'chat',
            'model': model_id,
            'session_id': 'socket',
            'id': 'old-reply' if action == 'assistant_message_id' else 'new-reply',
            'user_message': {'id': 'old-user', 'role': 'user', 'content': 'Original'},
            action: 'old-reply',
        },
    )
    assert response.status_code == 400
    assert response.json()['detail'] == 'Continue an agent conversation with a new message'


def test_agent_request_cannot_dispatch_an_ordinary_response_entry(completion_client):
    response = completion_client.post(
        '/api/chat/completions',
        json={
            'chat_id': 'chat',
            'model': AGENT,
            'session_id': 'socket',
            'user_message': {'id': 'new-user', 'role': 'user', 'content': 'Next'},
            'message_ids': [{'model_id': 'ordinary', 'message_id': 'new-reply'}],
        },
    )
    assert response.status_code == 400
    assert response.json()['detail'] == 'Use one agent at a time in a conversation'


def test_direct_provider_cannot_supply_native_agent_metadata(completion_client):
    response = completion_client.post(
        '/api/chat/completions',
        json={
            'chat_id': 'chat',
            'model': 'ordinary',
            'model_item': {
                'id': 'ordinary',
                'direct': True,
                'agent': {'terminal_id': 'terminal', 'provider': 'codex', 'model': 'model'},
            },
            'session_id': 'socket',
            'id': 'new-reply',
            'user_message': {'id': 'new-user', 'role': 'user', 'content': 'Next'},
        },
    )
    assert response.status_code == 400
    assert response.json()['detail'] == 'Select an authorized terminal agent model'


@pytest.mark.parametrize(
    'overrides,detail',
    [
        ({'chat_id': 'temporary:scope'}, 'Agent conversations require a saved chat'),
        ({'chat_id': 'channel:room'}, 'Agent conversations require a saved chat'),
        ({'session_id': None}, 'Agent conversations require a new saved turn'),
        ({'id': None}, 'Agent conversations require a new saved turn'),
        ({'user_message': None}, 'Agent conversations require a new saved turn'),
    ],
)
def test_agent_requires_complete_saved_turn_before_mutation(completion_client, overrides, detail):
    response = completion_client.post(
        '/api/chat/completions',
        json={
            'chat_id': 'chat',
            'model': AGENT,
            'session_id': 'socket',
            'id': 'new-reply',
            'user_message': {'id': 'new-user', 'role': 'user', 'content': 'Next'},
            **overrides,
        },
    )
    assert response.status_code == 400
    assert response.json()['detail'] == detail


def test_agent_completion_checks_chat_write_access_before_discovery(completion_client, completion_chat_access):
    completion_chat_access.return_value = None
    response = completion_client.post(
        '/api/chat/completions',
        json={
            'chat_id': 'another-chat',
            'model': AGENT,
            'terminal_id': 'terminal',
            'session_id': 'socket',
            'id': 'new-reply',
            'user_message': {'id': 'new-user', 'role': 'user', 'content': 'Next'},
        },
    )
    assert response.status_code == 404
    completion_chat_access.assert_awaited_once_with('another-chat', ACTOR, permission='write')
    discovery.get_models.assert_not_awaited()


@pytest.mark.parametrize(
    'chat_id,access,allowed',
    [
        (None, False, True),
        ('other-chat', False, False),
        ('shared-chat', True, True),
        ('temporary:scope', True, False),
        ('channel:room', True, False),
    ],
)
@pytest.mark.parametrize('role', ['user', 'admin'])
def test_terminal_model_discovery_requires_live_chat_read_access(monkeypatch, chat_id, access, allowed, role):
    path = Path(__file__).parents[1] / 'open_webui/main.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    endpoint = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_models')
    app = FastAPI()
    read_access = AsyncMock(return_value=access)
    agent_models = AsyncMock(return_value=[{'id': AGENT}])
    monkeypatch.setattr(discovery, 'get_models', agent_models)
    actor = SimpleNamespace(id='viewer', role=role)

    async def verified_user():
        return actor

    namespace = {
        'app': app,
        'Request': Request,
        'Depends': Depends,
        'get_verified_user': verified_user,
        'get_all_models': AsyncMock(return_value=[{'id': 'ordinary'}]),
        'get_filtered_models': AsyncMock(return_value=[{'id': 'ordinary'}]),
        'Config': SimpleNamespace(get=AsyncMock(return_value=None)),
        'Chats': SimpleNamespace(has_chat_access=read_access),
        'is_saved_chat_id': is_saved_chat_id,
        'normalize_model_tags': lambda value: [],
        'logging': logging,
        'log': logging.getLogger(__name__),
    }
    exec(compile(ast.Module(body=[endpoint], type_ignores=[]), str(path), 'exec'), namespace)
    params = {'terminal_id': 'terminal'}
    if chat_id:
        params['chat_id'] = chat_id
    response = TestClient(app).get('/api/models', params=params)
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['data']] == (['ordinary', AGENT] if allowed else ['ordinary'])
    if is_saved_chat_id(chat_id):
        read_access.assert_awaited_once_with(chat_id, actor, permission='read')
    else:
        read_access.assert_not_awaited()
    if allowed:
        assert agent_models.await_args.args[2] == {'terminal_id': 'terminal', 'chat_id': chat_id}
    else:
        agent_models.assert_not_awaited()


@pytest_asyncio.fixture
async def agent_database(tmp_path, monkeypatch):
    engine = create_async_engine(f'sqlite+aiosqlite:///{(tmp_path / "agent-turns.db").as_posix()}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_context(db=None):
        async with sessions() as session:
            yield session

    monkeypatch.setattr(chats, 'get_async_db', session_context)
    monkeypatch.setattr(chat_messages, 'get_async_db_context', session_context)
    monkeypatch.setattr(Chats, 'has_chat_access', AsyncMock(return_value=True))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Chat.__table__.create)
            await connection.run_sync(ChatMessage.__table__.create)
        yield sessions
    finally:
        await engine.dispose()


async def seed_agent_chat(sessions, *, placeholder=False):
    user_message = {'id': 'first-user', 'role': 'user', 'content': 'First', 'childrenIds': ['previous']}
    previous = {
        'id': 'previous',
        'role': 'assistant',
        'parentId': 'first-user',
        'model': AGENT,
        'childrenIds': [],
        'content': '' if placeholder else 'Answer',
        'done': not placeholder,
    }
    history = {'currentId': 'previous', 'messages': {'first-user': user_message, 'previous': previous}}
    async with sessions() as session:
        session.add(
            Chat(id='chat', user_id=ACTOR.id, title='Agent chat', chat={'history': history}, created_at=1, updated_at=1)
        )
        await session.commit()
    return user_message


@pytest.mark.asyncio
async def test_two_simultaneous_agent_turns_cannot_create_siblings(agent_database):
    await seed_agent_chat(agent_database)

    async def insert(suffix):
        return await Chats.insert_chat_turn(
            'chat',
            ACTOR,
            {'id': f'user-{suffix}', 'role': 'user', 'parentId': 'previous', 'content': 'Next'},
            [{'model_id': AGENT, 'message_id': f'assistant-{suffix}'}],
            agent_turn=True,
        )

    outcomes = await asyncio.gather(insert('a'), insert('b'), return_exceptions=True)
    failures = [item for item in outcomes if isinstance(item, HTTPException)]
    assert len(failures) == 1 and failures[0].status_code == 409, outcomes
    async with agent_database() as session:
        saved = await session.get(Chat, 'chat')
        history = saved.chat['history']
        assert len(history['messages']['previous']['childrenIds']) == 1
        assert len(history['messages']) == 4


@pytest.mark.asyncio
async def test_ordinary_model_cannot_regenerate_agent_reply_by_reusing_user_turn(agent_database):
    user_message = await seed_agent_chat(agent_database)
    with pytest.raises(HTTPException) as raised:
        await Chats.insert_chat_turn('chat', ACTOR, user_message, [{'model_id': 'ordinary', 'message_id': 'sibling'}])
    assert raised.value.status_code == 409

    # The user may switch models and continue with a distinct new turn.
    turn = await Chats.insert_chat_turn(
        'chat',
        ACTOR,
        {'id': 'next-user', 'role': 'user', 'parentId': 'previous', 'content': 'Next'},
        [{'model_id': 'ordinary', 'message_id': 'next-reply'}],
    )
    assert turn['messages']['next-reply']['model'] == 'ordinary'


@pytest.mark.asyncio
async def test_agent_automation_can_admit_its_precreated_reply(agent_database):
    user_message = await seed_agent_chat(agent_database, placeholder=True)
    turn = await Chats.insert_chat_turn(
        'chat', ACTOR, user_message, [{'model_id': AGENT, 'message_id': 'previous'}], agent_turn=True
    )
    assert turn['currentId'] == 'previous'
    assert turn['messages']['first-user']['childrenIds'] == ['previous']
