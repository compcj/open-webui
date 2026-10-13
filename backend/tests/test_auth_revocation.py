"""Exercise real auth helpers without importing database/config side effects."""

import ast
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, status

BACKEND = Path(__file__).parents[1] / 'open_webui'


class RedisError(Exception):
    pass


def load_functions(path, names, namespace):
    nodes = []
    for node in ast.parse((BACKEND / path).read_text(encoding='utf-8')).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
            nodes.append(node)
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)
    return namespace


@pytest.fixture(autouse=True)
def persistent_auth(monkeypatch):
    module = ModuleType('open_webui.utils.mfa')
    module.get_auth = AsyncMock(return_value=SimpleNamespace(session_stamp=None, mfa=None))
    module.get_mfa_config = AsyncMock(return_value=SimpleNamespace(ENABLE_MFA=False))
    module.is_mfa_required = lambda method, config: config.ENABLE_MFA
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def auth_runtime(redis):
    decoded = {'id': 'user-1', 'jti': 'token-1', 'iat': 20}
    ns = {
        'HTTPException': HTTPException,
        'status': status,
        'RedisError': RedisError,
        'REDIS_KEY_PREFIX': 'test',
        'revocation_log': logging.getLogger('test.revocation'),
        'decode_token': lambda _: decoded,
    }
    load_functions('utils/auth.py', {'is_valid_token', 'get_current_user'}, ns)
    request = SimpleNamespace(
        cookies={'token': 'synthetic-token', 'oauth_id_token': 'synthetic-oauth'},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(redis=redis)),
    )
    return ns, decoded, request


@pytest.mark.asyncio
async def test_redis_outage_rejects_with_retriable_http_error():
    redis = SimpleNamespace(get=AsyncMock(side_effect=RedisError('unavailable')))
    ns, decoded, _ = auth_runtime(redis)
    with pytest.raises(HTTPException) as caught:
        await ns['is_valid_token'](decoded, redis)
    assert caught.value.status_code == 503
    assert caught.value.headers.get('Retry-After')


@pytest.mark.asyncio
async def test_temporary_auth_outage_does_not_delete_session_cookies():
    redis = SimpleNamespace(get=AsyncMock(side_effect=RedisError('unavailable')))
    ns, _, request = auth_runtime(redis)
    ns['is_valid_token'] = AsyncMock(side_effect=HTTPException(503, 'Temporarily unavailable'))
    response = Mock()
    with pytest.raises(HTTPException) as caught:
        await ns['get_current_user'](request, response, None, None)
    assert caught.value.status_code == 503
    response.delete_cookie.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'values,expected', [([None, None], True), (['revoked'], False), ([None, '20'], False), ([None, '19'], True)]
)
async def test_revocation_and_recovery(values, expected):
    redis = SimpleNamespace(get=AsyncMock(side_effect=values))
    ns, decoded, _ = auth_runtime(redis)
    assert await ns['is_valid_token'](decoded, redis) is expected


@pytest.mark.asyncio
async def test_no_redis_keeps_existing_deployment_behavior():
    ns, decoded, _ = auth_runtime(None)
    assert await ns['is_valid_token'](decoded, None) is True


@pytest.mark.asyncio
async def test_terminal_auth_outage_closes_with_retry_code():
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=object())), close=AsyncMock())
    ns = {
        'HTTPException': HTTPException,
        'ENABLE_TOOL_SERVERS': True,
        'get_verified_user_by_token': AsyncMock(side_effect=HTTPException(503, 'Temporarily unavailable')),
    }
    load_functions('routers/terminals.py', {'_resolve_terminal_access'}, ns)
    assert await ns['_resolve_terminal_access'](ws, 'terminal-1', 'synthetic') is None
    assert ws.close.call_args.kwargs['code'] == 1013


@pytest.mark.asyncio
async def test_persistent_stamp_revokes_without_redis(persistent_auth):
    ns, decoded, _ = auth_runtime(None)
    persistent_auth.get_auth.return_value.session_stamp = 'new-stamp'
    assert await ns['is_valid_token'](decoded) is False


@pytest.mark.asyncio
async def test_mfa_enforcement_rejects_existing_unverified_session(persistent_auth):
    ns, decoded, _ = auth_runtime(None)
    persistent_auth.get_mfa_config.return_value.ENABLE_MFA = True
    assert await ns['is_valid_token'](decoded) is False
    decoded['mfa_verified'] = True
    persistent_auth.get_auth.return_value.mfa = SimpleNamespace(secret='encrypted-synthetic', reset_required=False)
    assert await ns['is_valid_token'](decoded) is True
    persistent_auth.get_auth.return_value.mfa.reset_required = True
    assert await ns['is_valid_token'](decoded) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('typ,expected', [('automation', True), ('subagent', True), ('mfa', False)])
async def test_token_purpose_still_rejects_challenge_tokens(persistent_auth, typ, expected):
    ns, decoded, _ = auth_runtime(None)
    decoded['typ'] = typ
    persistent_auth.get_mfa_config.return_value.ENABLE_MFA = True
    assert await ns['is_valid_token'](decoded) is expected


@pytest.mark.asyncio
async def test_optional_auth_preserves_retryable_failure():
    ns = {
        'get_http_authorization_cred': lambda _: None,
        'HTTPException': HTTPException,
        'get_verified_user_by_token': AsyncMock(side_effect=HTTPException(503, 'retry')),
    }
    load_functions('utils/auth.py', {'get_optional_verified_user_from_request'}, ns)
    request = SimpleNamespace(
        headers={},
        cookies={'token': 'synthetic'},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    )
    with pytest.raises(HTTPException) as caught:
        await ns['get_optional_verified_user_from_request'](request)
    assert caught.value.status_code == 503
