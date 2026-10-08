"""Exercise real auth helpers without importing database/config side effects."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace
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
        'get_verified_user_by_token': AsyncMock(side_effect=HTTPException(503, 'Temporarily unavailable')),
    }
    load_functions('routers/terminals.py', {'_resolve_terminal_access'}, ns)
    assert await ns['_resolve_terminal_access'](ws, 'terminal-1', 'synthetic') is None
    assert ws.close.call_args.kwargs['code'] == 1013
