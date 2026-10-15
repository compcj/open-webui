"""Verify Bridge auth failures and live access checks without provider or app imports."""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode, urlsplit, urlunsplit

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
        'Chats': SimpleNamespace(get_chat_by_id=AsyncMock(return_value=SimpleNamespace(user_id='member'))),
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
    runtime.ns['Chats'].get_chat_by_id.assert_awaited_once_with('chat-1')
    runtime.ns['check_model_access'].assert_awaited_once()
    runtime.ns['Chats'].get_chat_by_id.return_value = None
    with pytest.raises(ValueError, match='Chat ownership revoked'):
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


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_live_call_rejects_nonowner_even_with_admin_access(runtime, role):
    runtime.user.role = role
    runtime.ns['BYPASS_ADMIN_ACCESS_CONTROL'] = True
    runtime.ns['Chats'].get_chat_by_id.return_value = SimpleNamespace(user_id='owner')
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    with pytest.raises(ValueError, match='Chat ownership revoked'):
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None, 'chat-1')


@pytest.mark.asyncio
async def test_live_call_model_acl_and_enabled_revocation(runtime):
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'model': {'id': 'model'}})))
    runtime.ns['check_model_access'].side_effect = HTTPException(403, 'private')
    with pytest.raises(ValueError, match='Chat model access denied'):
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None, 'chat-1', 'model')
    runtime.ns['Config'].get_many.return_value = {'audio.realtime.enabled': False}
    with pytest.raises(ValueError, match='Call authorization expired'):
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None)


@pytest.mark.asyncio
async def test_live_model_acl_service_failure_is_retryable(runtime):
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'model': {'id': 'model'}})))
    runtime.ns['check_model_access'].side_effect = HTTPException(503, 'retry')
    with pytest.raises(HTTPException) as caught:
        await runtime.ns['check_realtime_access'](ws, 'synthetic', None, 'chat-1', 'model')
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_new_or_temporary_call_does_not_require_saved_chat(runtime):
    ws = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={})))
    assert await runtime.ns['check_realtime_access'](ws, 'synthetic', None) == runtime.user
    runtime.ns['Chats'].get_chat_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,auth_type,headers',
    [
        ('openai', 'api_key', {'Authorization': 'Bearer synthetic-key'}),
        ('azure', 'api_key', {'api-key': 'synthetic-key'}),
        ('azure', 'microsoft_entra_id', {'Authorization': 'Bearer synthetic-entra'}),
    ],
)
async def test_provider_resolver_preserves_azure_and_entra(provider, auth_type, headers):
    ns = {
        'asyncio': asyncio,
        'urlencode': urlencode,
        'urlsplit': urlsplit,
        'urlunsplit': urlunsplit,
        'get_microsoft_entra_id_access_token': lambda: 'synthetic-entra',
    }
    load_functions('routers/audio/realtime.py', {'_get_realtime_connection'}, ns)
    url, actual = await ns['_get_realtime_connection'](
        {
            'audio.realtime.provider': provider,
            'audio.realtime.auth_type': auth_type,
            'audio.realtime.openai.api_base_url': 'https://voice.example.test/openai/v1/',
            'audio.realtime.openai.api_key': 'synthetic-key',
            'audio.realtime.model': 'voice model',
        }
    )
    assert url == 'wss://voice.example.test/openai/v1/realtime?model=voice+model'
    assert actual == headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'override',
    [
        {'audio.realtime.provider': 'invalid'},
        {'audio.realtime.auth_type': 'microsoft_entra_id'},
        {'audio.realtime.openai.api_base_url': 'https://voice.example.test/?secret=value'},
        {'audio.realtime.openai.api_key': ''},
    ],
)
async def test_provider_resolver_rejects_unsupported_or_incomplete_settings(override):
    ns = {'asyncio': asyncio, 'urlencode': urlencode, 'urlsplit': urlsplit, 'urlunsplit': urlunsplit}
    load_functions('routers/audio/realtime.py', {'_get_realtime_connection'}, ns)
    config = {
        'audio.realtime.openai.api_base_url': 'https://voice.example.test/v1',
        'audio.realtime.openai.api_key': 'synthetic-key',
        'audio.realtime.model': 'voice',
        **override,
    }
    with pytest.raises(ValueError):
        await ns['_get_realtime_connection'](config)


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_handshake_rejects_nonowner_before_provider_connection(runtime, role):
    runtime.user.role = role
    runtime.ns['Chats'].get_chat_by_id.return_value = SimpleNamespace(user_id='owner')
    ws = SimpleNamespace(
        accept=AsyncMock(),
        receive_text=AsyncMock(
            return_value=json.dumps(
                {
                    'type': 'auth',
                    'token': 'synthetic',
                    'chat_id': 'chat-1',
                    'model_id': 'model',
                }
            )
        ),
        send_json=AsyncMock(),
        close=AsyncMock(),
        app=SimpleNamespace(state=SimpleNamespace(redis=None)),
    )
    await runtime.ns['realtime_call'](ws)
    ws.close.assert_awaited_once_with(code=1008)
    assert 'owner' in ws.send_json.call_args.args[0]['message']
