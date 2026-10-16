"""Connection header behavior without importing app configuration or a database."""

import ast
import json
import logging
import re
import sys
from copy import deepcopy
from string import punctuation
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ValidationError, field_validator

from open_webui.utils.model_ids import strip_provider_model_prefix
from open_webui.utils.responses_reasoning import remap_reasoning_effort_for_responses
from test_web_tool_citations import BACKEND, load_functions


@pytest.fixture
def headers_runtime():
    namespace = {
        'log': logging.getLogger(__name__),
        're': re,
        'quote': quote,
        'punctuation': punctuation,
        'ENABLE_FORWARD_USER_INFO_HEADERS': False,
        'USER_GROUPS_PLACEHOLDERS': ('{{USER_GROUPS}}', '{{USER_GROUP_IDS}}'),
        'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
    }
    load_functions(
        namespace,
        'utils/headers.py',
        {
            'get_headers_and_cookies',
            'get_custom_headers',
            'parse_custom_headers',
            'get_user_groups_for_custom_headers',
            'custom_headers_require_user_groups',
            'validate_connection_headers_config',
            'get_connection_headers',
        },
    )
    return namespace


async def assemble(runtime, config=None, metadata=None):
    return await runtime['get_headers_and_cookies'](
        SimpleNamespace(cookies={'test-cookie': 'synthetic'}, headers={}, state=SimpleNamespace()),
        'https://provider.invalid/v1',
        'synthetic-key',
        config,
        metadata,
    )


@pytest.mark.asyncio
async def test_old_connection_defaults_and_custom_headers_are_unchanged(headers_runtime):
    config = {'headers': {'user-agent': 'Existing/1.0', 'x-session-id': 'manual', 'X-Other': 'kept'}}
    headers, cookies = await assemble(headers_runtime, config, {'chat_id': 'chat-a'})
    assert headers == {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer synthetic-key',
        **config['headers'],
    }
    assert cookies == {}


@pytest.mark.asyncio
async def test_explicit_user_agent_replaces_case_variants_without_mutating_config(headers_runtime):
    config = {
        'user_agent': 'My Client/2.0 (custom)',
        'headers': {'user-agent': 'old', 'USER-AGENT': 'also-old', 'X-Other': 'kept'},
        'forward_cookies': True,
    }
    original = deepcopy(config)
    headers, cookies = await assemble(headers_runtime, config)
    assert [(key, value) for key, value in headers.items() if key.lower() == 'user-agent'] == [
        ('User-Agent', 'My Client/2.0 (custom)')
    ]
    assert headers['X-Other'] == 'kept'
    assert headers['Authorization'] == 'Bearer synthetic-key'
    assert cookies == {'test-cookie': 'synthetic'}
    assert config == original


@pytest.mark.asyncio
async def test_session_header_is_opt_in_and_stable_across_turns_and_sockets(headers_runtime):
    config = {'enable_session_id': True}
    for socket_id, message_id in [('socket-a', 'reply-1'), ('socket-a', 'reply-2'), ('socket-b', 'reply-2')]:
        headers, _ = await assemble(
            headers_runtime, config, {'chat_id': 'chat-a', 'session_id': socket_id, 'message_id': message_id}
        )
        assert headers['x-session-id'] == 'chat-a'
    headers, _ = await assemble(headers_runtime, config, {'chat_id': 'chat-b'})
    assert headers['x-session-id'] == 'chat-b'
    headers, _ = await assemble(headers_runtime, {'enable_session_id': False}, {'chat_id': 'chat-a'})
    assert 'x-session-id' not in headers


@pytest.mark.asyncio
async def test_custom_session_header_replaces_case_variants(headers_runtime):
    config = {
        'enable_session_id': True,
        'session_id_header': 'X-Provider-Session',
        'headers': {'x-provider-session': 'stale', 'X-Other': 'kept'},
    }
    headers, _ = await assemble(headers_runtime, config, {'chat_id': 'chat-a'})
    assert headers['X-Provider-Session'] == 'chat-a'
    assert 'x-provider-session' not in headers
    assert 'x-session-id' not in headers
    assert headers['X-Other'] == 'kept'


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata', [None, {}, {'session_id': 'socket-only'}, {'chat_id': ''}])
async def test_no_session_header_for_verification_or_requests_without_chat(headers_runtime, metadata):
    headers, _ = await assemble(headers_runtime, {'enable_session_id': True}, metadata)
    assert 'x-session-id' not in headers


@pytest.mark.asyncio
async def test_temporary_chats_use_root_message_identity_instead_of_shared_socket(headers_runtime):
    config = {'enable_session_id': True}
    for prefix in ('temporary:', 'local:'):
        for root in ('root-a', 'root-b'):
            headers, _ = await assemble(
                headers_runtime, config, {'chat_id': f'{prefix}socket-a', 'connection_session_id': root}
            )
            assert headers['x-session-id'] == root
        headers, _ = await assemble(headers_runtime, config, {'chat_id': f'{prefix}socket-a'})
        assert 'x-session-id' not in headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'config',
    [
        {'user_agent': 'agent\r\nAuthorization: injected'},
        {'user_agent': 'agent\x00'},
        {'user_agent': 123},
        {'enable_session_id': 'false'},
        {'enable_session_id': True, 'session_id_header': 'Bad Header'},
        {'enable_session_id': True, 'session_id_header': 'X-Test\r\nInjected'},
        {'enable_session_id': True, 'session_id_header': 'Authorization'},
        {'enable_session_id': True, 'session_id_header': 'content-length'},
        {'enable_session_id': True, 'session_id_header': 'USER-AGENT'},
        {'enable_session_id': True, 'session_id_header': 'X-Title'},
    ],
)
async def test_invalid_options_are_rejected_before_sending(headers_runtime, config):
    with pytest.raises(ValueError):
        await assemble(headers_runtime, config, {'chat_id': 'chat-a'})


@pytest.mark.parametrize('provider', ['openai', 'ollama'])
def test_config_and_verification_forms_validate_and_round_trip_options(headers_runtime, provider):
    namespace = {
        'BaseModel': BaseModel,
        'field_validator': field_validator,
        'validate_connection_headers_config': headers_runtime['validate_connection_headers_config'],
    }
    path = BACKEND / 'routers' / f'{provider}.py'
    names = {'ConnectionVerificationForm', 'OpenAIConfigForm', 'OllamaConfigForm'}
    nodes = [
        node
        for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, ast.ClassDef) and node.name in names
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    prefix = provider.upper()
    form = namespace['OpenAIConfigForm' if provider == 'openai' else 'OllamaConfigForm']
    base = {
        f'{prefix}_API_CONFIGS': {'0': {}},
        f'{prefix}_API_BASE_URLS' if provider == 'openai' else 'OLLAMA_BASE_URLS': ['https://provider.invalid'],
    }
    if provider == 'openai':
        base['OPENAI_API_KEYS'] = ['synthetic-key']
    options = {'user_agent': 'Custom/2.0', 'enable_session_id': True, 'session_id_header': 'x-routing-session'}
    payload = {**base, f'{prefix}_API_CONFIGS': {'0': options}}
    assert form(**payload).model_dump()[f'{prefix}_API_CONFIGS']['0'] == options
    verify = namespace['ConnectionVerificationForm']
    assert verify(url='https://provider.invalid', key='', config=options).config == options
    for invalid in ({'user_agent': 'bad\r\n'}, {'enable_session_id': True, 'session_id_header': 'Authorization'}):
        with pytest.raises(ValidationError):
            form(**{**base, f'{prefix}_API_CONFIGS': {'0': invalid}})
        with pytest.raises(ValidationError):
            verify(url='https://provider.invalid', key='', config=invalid)
    assert form(**base).model_dump()[f'{prefix}_API_CONFIGS']['0'] == {}


class CapturedSend(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('api_type', ['chat_completions', 'responses', 'ollama'])
@pytest.mark.parametrize('continuation', [False, True])
async def test_real_send_boundary_keeps_connection_headers(headers_runtime, monkeypatch, api_type, continuation):
    captured = {}

    async def send(*args, **kwargs):
        captured.update(kwargs)
        raise CapturedSend()

    config = {
        'user_agent': 'Custom Client/3.0',
        'enable_session_id': True,
        'session_id_header': 'X-Session-Id',
        'api_type': api_type,
    }
    metadata = {'chat_id': 'chat-a', 'session_id': 'socket-b' if continuation else 'socket-a'}
    request = SimpleNamespace(
        state=SimpleNamespace(),
        cookies={},
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'synthetic-model': {'urlIdx': 0}})),
    )
    namespace = {
        **headers_runtime,
        'router': SimpleNamespace(post=lambda *a, **kw: lambda function: function),
        'Depends': Depends,
        'HTTPException': HTTPException,
        'get_direct_chat_user': lambda: None,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'check_model_access': AsyncMock(),
        'get_openai_connection': AsyncMock(return_value=('https://provider.invalid/v1', 'synthetic-key', config)),
        'strip_provider_model_prefix': strip_provider_model_prefix,
        'is_openai_new_model': lambda model: False,
        'get_session': AsyncMock(return_value=SimpleNamespace(request=send)),
        'get_client_timeout': lambda **kwargs: None,
        'cleanup_response': AsyncMock(),
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'JSONCodec': json,
        'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses,
    }
    dispatch = ModuleType('open_webui.utils.context_compaction_dispatch')
    dispatch.apply_context_compaction = AsyncMock(
        side_effect=lambda request, user, payload, metadata, model_id: payload
    )
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    payload = {'model': 'synthetic-model', 'messages': [{'role': 'user', 'content': 'synthetic'}], 'metadata': metadata}
    if continuation:
        payload.update(
            previous_response_id='response-before',
            messages=[{'role': 'tool', 'tool_call_id': 'call-a', 'content': 'synthetic result'}],
        )

    with pytest.raises(CapturedSend):
        if api_type == 'ollama':
            load_functions(namespace, 'routers/ollama.py', {'send_request'})
            await namespace['send_request'](
                'https://provider.invalid/api/chat',
                payload='{}',
                key='synthetic-key',
                api_config=config,
                metadata=metadata,
                request=request,
            )
        else:
            load_functions(namespace, 'routers/openai.py', {'generate_chat_completion', 'convert_to_responses_payload'})
            await namespace['generate_chat_completion'](request, payload, SimpleNamespace(role='admin'))
    if api_type != 'ollama':
        assert captured['url'].endswith('/responses' if api_type == 'responses' else '/chat/completions')
        assert 'metadata' not in json.loads(captured['data'])
    assert captured['headers']['User-Agent'] == 'Custom Client/3.0'
    assert captured['headers']['X-Session-Id'] == 'chat-a'
    assert captured['headers']['Authorization'] == 'Bearer synthetic-key'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['get_models_request', 'get_models', 'verify_connection'])
async def test_native_anthropic_discovery_and_verify_use_configured_user_agent(headers_runtime, operation):
    captured = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self):
            return {'data': [{'id': 'synthetic-model'}], 'has_more': False}

    class Session(Response):
        def get(self, url, **kwargs):
            captured.append(kwargs)
            return Response()

    config = {'user_agent': 'My-Configured-UA/1.0', 'enable_session_id': True}
    url = 'https://api.anthropic.com/v1'
    namespace = {
        **headers_runtime,
        'aiohttp': SimpleNamespace(
            ClientTimeout=lambda **kwargs: None, ClientSession=lambda **kwargs: Session(), ClientError=ConnectionError
        ),
        'AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST': 10,
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'ANTHROPIC_VERSION': '2023-06-01',
        '_MODEL_LIST_TIMEOUT': None,
        'router': SimpleNamespace(get=lambda *a, **kw: lambda f: f, post=lambda *a, **kw: lambda f: f),
        'Depends': Depends,
        'get_verified_user': lambda: None,
        'get_admin_user': lambda: None,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'get_openai_connection': AsyncMock(return_value=(url, 'synthetic-key', config)),
        'HTTPException': HTTPException,
        'ERROR_MESSAGES': SimpleNamespace(SERVER_CONNECTION_ERROR='connection failed'),
    }
    load_functions(namespace, 'utils/anthropic.py', {'get_anthropic_models', 'is_anthropic_url'})
    load_functions(namespace, 'routers/openai.py', {operation})
    request = SimpleNamespace(cookies={}, headers={}, state=SimpleNamespace())
    user = SimpleNamespace(role='admin')
    if operation == 'get_models_request':
        result = await namespace[operation](request, url, 'synthetic-key', user, config)
    elif operation == 'get_models':
        result = await namespace[operation](request, 0, user)
    else:
        result = await namespace[operation](request, SimpleNamespace(url=url, key='synthetic-key', config=config), user)
    assert result['data'][0]['id'] == 'synthetic-model'
    assert len(captured) == 1
    assert captured[0]['headers']['User-Agent'] == 'My-Configured-UA/1.0'
    assert captured[0]['headers']['x-api-key'] == 'synthetic-key'
    assert captured[0]['headers']['anthropic-version'] == '2023-06-01'
    assert 'x-session-id' not in captured[0]['headers']
