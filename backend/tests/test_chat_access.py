"""Exercise authorization with FastAPI, without importing application/database state."""

import ast
import asyncio
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.testclient import TestClient
from pydantic import BaseModel

BACKEND = Path(__file__).parents[1] / 'open_webui'


@pytest.fixture
def access(monkeypatch):
    path = BACKEND / 'utils/chat_access.py'
    assert path.exists(), 'Chat access policy has not been implemented'
    config = SimpleNamespace(get=AsyncMock(), get_many=AsyncMock())
    config.get.return_value = False
    config.get_many.return_value = {'chat.temporary.enable': False, 'chat.direct_api.enable': False}

    id_spec = importlib.util.spec_from_file_location('open_webui.utils.chat_id', BACKEND / 'utils/chat_id.py')
    id_module = importlib.util.module_from_spec(id_spec)
    id_spec.loader.exec_module(id_module)
    monkeypatch.setitem(sys.modules, 'open_webui.utils.chat_id', id_module)

    async def verified_user():
        return SimpleNamespace(id='user-1', role='user')

    for name, values in {
        'open_webui.models.config': {'Config': config},
        'open_webui.utils.auth': {'get_verified_user': verified_user},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(BACKEND.parent))
    spec = importlib.util.spec_from_file_location('chat_access_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, config, verified_user


def conversation(**overrides):
    return {
        'model': 'test-model',
        'chat_id': 'saved-chat',
        'id': 'assistant-1',
        'user_message': {'id': 'message-1', 'role': 'user', 'content': 'Hello'},
        **overrides,
    }


def client_for_policy(access, role='user'):
    module, _, _ = access
    app = FastAPI()

    @app.post('/chat')
    async def chat(form_data: dict):
        await module.check_chat_access(form_data, SimpleNamespace(id='user-1', role=role))
        return {'allowed': True}

    return TestClient(app)


@pytest.mark.parametrize('chat_id', ['temporary:socket-1', 'local:socket-1'])
@pytest.mark.parametrize('direct_enabled', [False, True])
def test_temporary_chat_is_independently_denied(access, chat_id, direct_enabled):
    access[1].get_many.return_value['chat.direct_api.enable'] = direct_enabled
    response = client_for_policy(access).post('/chat', json=conversation(chat_id=chat_id))
    assert response.status_code == 403
    assert 'Temporary' in response.json()['detail']


@pytest.mark.parametrize('temporary_enabled', [False, True])
def test_context_free_api_is_independently_denied(access, temporary_enabled):
    access[1].get_many.return_value['chat.temporary.enable'] = temporary_enabled
    response = client_for_policy(access).post('/chat', json={'model': 'test-model', 'messages': []})
    assert response.status_code == 403


@pytest.mark.parametrize('chat_id', ['temporary:socket-1', 'local:socket-1'])
def test_temporary_conversation_can_remain_enabled_without_direct_api(access, chat_id):
    access[1].get_many.return_value['chat.temporary.enable'] = True
    assert client_for_policy(access).post('/chat', json=conversation(chat_id=chat_id)).status_code == 200


def test_direct_api_can_remain_enabled_without_temporary_chat(access):
    access[1].get_many.return_value['chat.direct_api.enable'] = True
    assert client_for_policy(access).post('/chat', json={'model': 'test-model'}).status_code == 200


@pytest.mark.parametrize(
    'payload',
    [
        conversation(),
        conversation(chat_id=None, parent_id=None),
        conversation(chat_id='channel:channel-1', user_message=None),
    ],
)
def test_persisted_conversation_shapes_remain_allowed(access, payload):
    assert client_for_policy(access).post('/chat', json=payload).status_code == 200


@pytest.mark.parametrize(
    'changes',
    [
        {'chat_id': None},
        {'chat_id': ''},
        {'chat_id': []},
        {'chat_id': 12},
        {'id': None},
        {'id': ''},
        {'message_ids': []},
        {'message_ids': {}},
        {'message_ids': [{'model_id': 'test-model'}]},
        {'message_ids': [None]},
        {'message_ids': {'test-model': None}},
        {'user_message': None},
        {'user_message': {'id': 'message-1', 'role': 'assistant'}},
        {'user_message': {'role': 'user', 'content': 'Hello'}},
    ],
)
def test_partial_or_forged_context_does_not_enable_direct_api(access, changes):
    response = client_for_policy(access).post('/chat', json=conversation(**changes))
    assert response.status_code == 403


@pytest.mark.parametrize(
    'message_ids', [[{'model_id': 'test-model', 'message_id': 'assistant-1'}], {'test-model': 'assistant-1'}]
)
def test_multi_model_and_legacy_message_ids(access, message_ids):
    payload = conversation(id=None, message_ids=message_ids)
    assert client_for_policy(access).post('/chat', json=payload).status_code == 200


def test_parent_message_compatibility(access):
    payload = conversation()
    payload['parent_message'] = payload.pop('user_message')
    assert client_for_policy(access).post('/chat', json=payload).status_code == 200


def test_client_internal_metadata_does_not_bypass_policy(access):
    payload = {'model': 'test-model', 'internal': True, 'metadata': {'chat_id': 'saved-chat'}}
    assert client_for_policy(access).post('/chat', json=payload).status_code == 403


@pytest.mark.parametrize('payload', [{}, conversation(chat_id='temporary:socket-1')])
def test_admin_exemption(access, payload):
    assert client_for_policy(access, 'admin').post('/chat', json=payload).status_code == 200


def test_absent_settings_preserve_existing_behavior(access):
    access[1].get_many.return_value = {}
    assert client_for_policy(access).post('/chat', json={}).status_code == 200


PROXY_HANDLERS = {
    'openai': ['generate_chat_completion', 'responses', 'proxy'],
    'ollama': [
        'generate_completion',
        'generate_chat_completion',
        'generate_openai_completion',
        'generate_openai_chat_completion',
        'generate_anthropic_messages',
        'generate_responses',
    ],
}


@pytest.mark.parametrize('router_name,names', PROXY_HANDLERS.items())
def test_public_proxy_routes_deny_before_provider_dispatch(access, router_name, names):
    """Keep real route declarations/dependencies; replace only upstream handler bodies."""
    module, config, verified_user = access
    router = APIRouter()
    tree = ast.parse((BACKEND / f'routers/{router_name}.py').read_text(encoding='utf-8'))
    handlers = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    assert len(handlers) == len(names)
    for handler in handlers:
        handler.body = ast.parse("return {'provider_called': True}").body
        for arg in handler.args.args:
            if arg.arg == 'form_data':
                arg.annotation = ast.Name(id='dict', ctx=ast.Load())
    namespace = {
        'router': router,
        'Request': Request,
        'Depends': Depends,
        'get_verified_user': verified_user,
        'get_direct_chat_user': module.get_direct_chat_user,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=handlers, type_ignores=[])), '<routes>', 'exec'), namespace)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    for route in router.routes:
        path = route.path.replace('{url_idx}', '0').replace('{path:path}', 'v1/chat/completions')
        response = client.post(path, json=conversation(), headers={'Authorization': 'Bearer synthetic-jwt'})
        assert response.status_code == 403, (path, response.text)
    config.get.return_value = True
    assert client.post('/chat/completions' if router_name == 'openai' else '/api/chat', json={}).status_code == 200


def test_direct_api_dependency_exempts_admin(access):
    module, _, _ = access
    user = SimpleNamespace(role='admin')
    assert asyncio.run(module.get_direct_chat_user(user)) is user


@pytest.mark.parametrize('path', ['/api/chat/completions', '/api/v1/chat/completions'])
def test_main_endpoints_deny_before_loading_models(access, path):
    module, _, verified_user = access
    app = FastAPI()
    app.state.MODELS = {}

    async def load_models(*args, **kwargs):
        raise HTTPException(status_code=418, detail='Reached model loading')

    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    namespace = {
        'app': app,
        'Request': Request,
        'Depends': Depends,
        'get_verified_user': verified_user,
        'check_chat_access': module.check_chat_access,
        'get_all_models': load_models,
    }
    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<main>', 'exec'), namespace)
    client = TestClient(app)
    assert client.post(path, json={'model': 'test-model', 'messages': []}).status_code == 403
    assert client.post(path, json=conversation()).status_code == 418


def test_anthropic_compatibility_route_denies_before_passthrough(access):
    module, _, verified_user = access
    app = FastAPI()
    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'generate_messages'
    )
    handler.body = ast.parse("return {'provider_called': True}").body
    namespace = {
        'app': app,
        'Request': Request,
        'Depends': Depends,
        'get_verified_user': verified_user,
        'get_direct_chat_user': module.get_direct_chat_user,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])), '<main>', 'exec'), namespace)
    client = TestClient(app)
    assert client.post('/api/v1/messages', json={'model': 'test-model', 'messages': []}).status_code == 403


@pytest.mark.parametrize(
    'target,expected',
    [
        (None, 404),
        (SimpleNamespace(channel_id='channel-2', user_id='user-1'), 403),
        (SimpleNamespace(channel_id='channel-1', user_id='user-2'), 403),
        (SimpleNamespace(channel_id='channel-1', user_id='user-1'), 200),
    ],
)
def test_channel_context_requires_an_existing_owned_response(access, target, expected):
    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    gate = next(
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.If) and ast.unparse(node.test) == "chat_id.startswith('channel:')"
    )
    wrapper = ast.parse('async def check_channel(chat_id, message_ids, user):\n    pass').body[0]
    wrapper.body = [gate]
    namespace = {
        'HTTPException': HTTPException,
        'status': status,
        'ERROR_MESSAGES': SimpleNamespace(NOT_FOUND='Not found', DEFAULT=lambda: 'Forbidden'),
        'Channels': SimpleNamespace(
            get_channel_by_id=AsyncMock(return_value=SimpleNamespace(id='channel-1', type='group')),
            is_user_channel_member=AsyncMock(return_value=True),
        ),
        'Messages': SimpleNamespace(get_message_by_id=AsyncMock(return_value=target)),
    }
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), '<channel-gate>', 'exec'),
        namespace,
    )
    module = access[0]
    app = FastAPI()

    @app.post('/channel')
    async def channel(form_data: dict):
        user = SimpleNamespace(id='user-1', role='user')
        await module.check_chat_access(form_data, user)
        await namespace['check_channel'](form_data['chat_id'], [{'message_id': form_data['id']}], user)
        return {'allowed': True}

    response = TestClient(app).post('/channel', json=conversation(chat_id='channel:channel-1', user_message=None))
    assert response.status_code == expected


@pytest.mark.parametrize('value,expected', [(None, True), ('True', True), ('False', False)])
def test_environment_defaults_are_registered(monkeypatch, value, expected):
    names = {'ENABLE_TEMPORARY_CHATS', 'ENABLE_DIRECT_API_CHAT'}
    for name in names:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    tree = ast.parse((BACKEND / 'config.py').read_text(encoding='utf-8'))
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
    ]
    namespace = {'os': os}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), '<config>', 'exec'), namespace)
    assert all(namespace[name] is expected for name in names)
    defaults = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'DEFAULT_CONFIG' for target in node.targets)
    )
    mapping = {
        key.value: val.id
        for key, val in zip(defaults.keys, defaults.values)
        if isinstance(key, ast.Constant) and key.value in ('chat.temporary.enable', 'chat.direct_api.enable')
    }
    assert mapping == {
        'chat.temporary.enable': 'ENABLE_TEMPORARY_CHATS',
        'chat.direct_api.enable': 'ENABLE_DIRECT_API_CHAT',
    }


def test_admin_settings_round_trip_and_old_client_compatibility():
    """Run the real admin schema/mapping/handlers against an in-memory config store."""
    router = APIRouter()
    stored = {'chat.temporary.enable': True, 'chat.direct_api.enable': True}

    async def get_many(*keys):
        return {key: stored[key] for key in keys if key in stored}

    async def upsert(updates):
        stored.update(updates)

    async def admin_user():
        return SimpleNamespace(id='admin-1', role='admin')

    names = {'get_config_values', 'config_updates', 'get_admin_config', 'AdminConfig', 'update_admin_config'}
    tree = ast.parse((BACKEND / 'routers/auths.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if getattr(node, 'name', None) in names
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'ADMIN_CONFIG_KEYS' for target in node.targets)
        )
    ]
    namespace = {
        'router': router,
        'Request': Request,
        'Depends': Depends,
        'BaseModel': BaseModel,
        're': re,
        'get_admin_user': admin_user,
        'Config': SimpleNamespace(get_many=get_many, upsert=upsert),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<auths>', 'exec'), namespace)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    schema = namespace['AdminConfig']
    payload = {
        name: False if field.annotation is bool else ''
        for name, field in schema.model_fields.items()
        if field.is_required()
    }
    payload.update(DEFAULT_USER_ROLE='user', JWT_EXPIRES_IN='4w')

    for temporary, direct in [(False, True), (True, False), (False, False), (True, True)]:
        updated = client.post(
            '/admin/config', json={**payload, 'ENABLE_TEMPORARY_CHATS': temporary, 'ENABLE_DIRECT_API_CHAT': direct}
        )
        assert updated.status_code == 200
        fetched = client.get('/admin/config').json()
        assert fetched['ENABLE_TEMPORARY_CHATS'] is temporary
        assert fetched['ENABLE_DIRECT_API_CHAT'] is direct

    stored.update({'chat.temporary.enable': False, 'chat.direct_api.enable': False})
    assert client.post('/admin/config', json=payload).status_code == 200
    assert stored['chat.temporary.enable'] is False
    assert stored['chat.direct_api.enable'] is False
