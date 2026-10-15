"""Execute Standard call authorization before the completion route's model/persistence work."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

SOURCE = Path(__file__).parents[1] / 'open_webui' / 'main.py'


@pytest.fixture
def boundary():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    route = copy.deepcopy(
        next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'chat_completion')
    )
    route.decorator_list = []
    route.args.defaults = []
    cutoff = next(
        i
        for i, n in enumerate(route.body)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'model_id' for t in n.targets)
    )
    route.body = [*route.body[:cutoff], ast.Return(ast.Constant('accepted'))]
    helpers = [n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_check_voice_chat_access']
    ns = dict(
        HTTPException=HTTPException,
        check_chat_access=AsyncMock(),
        Config=SimpleNamespace(get=AsyncMock(return_value={})),
        has_permission=AsyncMock(return_value=True),
        Chats=SimpleNamespace(is_chat_owner=AsyncMock(return_value=False)),
        is_saved_chat_id=lambda value: (
            isinstance(value, str) and bool(value) and not value.startswith(('temporary:', 'local:', 'channel:'))
        ),
        get_all_models=AsyncMock(),
    )
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *helpers, route]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), ns)
    ns['request'] = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'model': {}})))
    return ns


async def invoke(ns, payload, role='user'):
    return await ns['chat_completion'](ns['request'], payload, SimpleNamespace(id='actor', role=role))


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize(
    'features',
    [
        {'features': {'voice': True}},
        {'metadata': {'features': {'voice': True}}},
        {'features': {'voice': False}, 'metadata': {'features': {'voice': True}}},
    ],
)
async def test_nonowner_cannot_start_standard_call_before_model_or_storage_work(boundary, role, features):
    with pytest.raises(HTTPException) as exc:
        await invoke(boundary, {'chat_id': 'saved-chat', **features}, role)
    assert exc.value.status_code == 403
    boundary['Chats'].is_chat_owner.assert_awaited_once_with('saved-chat', 'actor')
    boundary['get_all_models'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_owner_call_keeps_role_permission_semantics(boundary, role):
    boundary['Chats'].is_chat_owner.return_value = True
    assert await invoke(boundary, {'chat_id': 'saved-chat', 'features': {'voice': True}}, role) == 'accepted'
    if role == 'user':
        boundary['has_permission'].assert_awaited_once_with('actor', 'chat.call', {})
    else:
        boundary['has_permission'].assert_not_awaited()


@pytest.mark.asyncio
async def test_call_permission_is_required_even_for_chat_owner(boundary):
    boundary['Chats'].is_chat_owner.return_value = True
    boundary['has_permission'].return_value = False
    with pytest.raises(HTTPException) as exc:
        await invoke(boundary, {'chat_id': 'saved-chat', 'features': {'voice': True}})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('chat_id', [None, '', 'temporary:session', 'local:session'])
async def test_new_and_temporary_calls_keep_existing_policy(boundary, chat_id):
    assert await invoke(boundary, {'chat_id': chat_id, 'features': {'voice': True}}) == 'accepted'
    boundary['Chats'].is_chat_owner.assert_not_awaited()
    boundary['check_chat_access'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('features', [{}, {'voice': False}, {'audio': True}])
async def test_noncall_chat_and_dictation_payloads_do_not_gain_owner_restriction(boundary, features):
    assert await invoke(boundary, {'chat_id': 'saved-chat', 'features': features}) == 'accepted'
    boundary['Chats'].is_chat_owner.assert_not_awaited()
    boundary['has_permission'].assert_not_awaited()
