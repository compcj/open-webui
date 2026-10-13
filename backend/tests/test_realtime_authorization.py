"""Verify Bridge auth failures and live access checks without provider or app imports."""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, WebSocketDisconnect

from test_auth_revocation import load_functions


@pytest.fixture
def runtime():
    user = SimpleNamespace(id='member', role='user')
    ns = {
        'asyncio': asyncio,
        'contextlib': contextlib,
        'JSONCodec': json,
        'HTTPException': HTTPException,
        'WebSocketDisconnect': WebSocketDisconnect,
        'log': logging.getLogger(__name__),
        'get_verified_user_by_token': AsyncMock(return_value=user),
        'Config': SimpleNamespace(
            get_many=AsyncMock(return_value={'audio.realtime.enabled': True, 'user.permissions': {}})
        ),
        'Chats': SimpleNamespace(get_accessible_chat_by_id=AsyncMock(return_value=object())),
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'has_permission': AsyncMock(return_value=True),
        'check_model_access': AsyncMock(),
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'BYPASS_ADMIN_ACCESS_CONTROL': False,
    }
    load_functions('routers/audio/realtime.py', {'realtime_call', 'check_realtime_access'}, ns)
    return SimpleNamespace(ns=ns, user=user)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error,code', [(HTTPException(503, 'sensitive-internal-error'), 1013), (HTTPException(401, 'invalid'), 1008)]
)
async def test_handshake_failure_preserves_retryable_vs_invalid_identity(runtime, error, code):
    runtime.ns['get_verified_user_by_token'].side_effect = error
    ws = SimpleNamespace(
        accept=AsyncMock(),
        receive_text=AsyncMock(return_value=json.dumps({'type': 'auth', 'token': 'synthetic'})),
        send_json=AsyncMock(),
        close=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(redis=object())),
    )
    await runtime.ns['realtime_call'](ws)
    ws.close.assert_awaited_once_with(code=code)
    assert 'sensitive-internal-error' not in ws.send_json.call_args.args[0]['message']


@pytest.mark.asyncio
async def test_live_call_rechecks_shared_chat_and_model_access(runtime):
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'model': {'id': 'model'}})))
    assert await runtime.ns['check_realtime_access'](ws, 'synthetic', None, 'chat-1', 'model') == runtime.user
    runtime.ns['Chats'].get_accessible_chat_by_id.assert_awaited_once_with('chat-1', runtime.user, permission='write')
    runtime.ns['check_model_access'].assert_awaited_once()
    runtime.ns['Chats'].get_accessible_chat_by_id.return_value = None
    with pytest.raises(ValueError, match='Chat access revoked'):
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None, 'chat-1', 'model')


@pytest.mark.asyncio
async def test_live_permission_and_token_revocation_fail_closed(runtime):
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    runtime.ns['has_permission'].return_value = False
    with pytest.raises(ValueError, match='Call permission revoked'):
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None)
    runtime.ns['get_verified_user_by_token'].side_effect = HTTPException(503, 'retry')
    with pytest.raises(HTTPException) as caught:
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None)
    assert caught.value.status_code == 503
