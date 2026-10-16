"""Exercise native connections at real router/header/replay boundaries, without app imports."""

import copy
import asyncio
import json
import logging
import re
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import aiohttp
from fastapi import Depends, HTTPException
from starlette.responses import JSONResponse, StreamingResponse

from open_webui.utils.model_ids import strip_provider_model_prefix
from test_connection_headers import assemble, headers_runtime
from test_web_tool_citations import load_functions
from open_webui.utils.native_providers import (
    build_request,
    make_binding,
    normalize_models,
    normalize_response,
    stream_response,
)
from test_tool_citation_replay import pipeline, citation_runtime, output_runtime, install_approval_store


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol,key_header', [('gemini', 'x-goog-api-key'), ('anthropic', 'x-api-key')])
@pytest.mark.parametrize('auth', [None, 'api_key'])
async def test_native_key_auth_and_custom_header_precedence(headers_runtime, protocol, key_header, auth):
    config = {'api_type': protocol, 'headers': {key_header.upper(): 'custom-key'}, 'user_agent': 'Native/1.0'}
    if auth:
        config['auth_type'] = auth
    original = copy.deepcopy(config)
    headers, _ = await assemble(headers_runtime, config, {'chat_id': 'chat-a'})
    assert [(name, value) for name, value in headers.items() if name.lower() == key_header] == [
        (key_header.upper(), 'custom-key')
    ]
    assert 'Authorization' not in headers
    assert headers['User-Agent'] == 'Native/1.0'
    if protocol == 'anthropic':
        assert headers['anthropic-version'] == '2023-06-01'
    assert config == original


@pytest.mark.asyncio
async def test_native_bearer_and_none_remain_explicit(headers_runtime):
    headers, _ = await assemble(headers_runtime, {'api_type': 'anthropic', 'auth_type': 'bearer'})
    assert headers['Authorization'] == 'Bearer synthetic-key'
    assert 'x-api-key' not in headers
    headers, _ = await assemble(headers_runtime, {'api_type': 'gemini', 'auth_type': 'none'})
    assert not any(name.lower() in {'authorization', 'x-goog-api-key'} for name in headers)


@pytest.mark.parametrize('name', ['x-goog-api-key', 'Anthropic-Version'])
def test_session_identity_cannot_replace_native_protocol_headers(headers_runtime, name):
    with pytest.raises(ValueError):
        headers_runtime['validate_connection_headers_config']({'enable_session_id': True, 'session_id_header': name})


class CapturedSend(BaseException):
    pass


@pytest.fixture
def sender(monkeypatch):
    captured = {}

    async def budget(request, user, payload, metadata, model_id):
        captured['budget'] = copy.deepcopy(payload)
        return payload

    async def send(**kwargs):
        captured['send'] = kwargs
        raise CapturedSend()

    dispatch = ModuleType('open_webui.utils.context_compaction_dispatch')
    dispatch.apply_context_compaction = budget
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={'connection.native-test': {'urlIdx': 0}})),
    )
    row = SimpleNamespace(
        id='connection.native-test',
        base_model_id=None,
        params=SimpleNamespace(model_dump=lambda: {}),
        meta=SimpleNamespace(tool_schema_compatibility='default'),
    )
    ns = {
        'router': SimpleNamespace(post=lambda *args, **kwargs: lambda fn: fn),
        'Request': object,
        'Depends': Depends,
        'get_direct_chat_user': lambda: None,
        'UserModel': object,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=row)),
        'check_model_access': AsyncMock(),
        'strip_provider_model_prefix': strip_provider_model_prefix,
        'is_openai_new_model': lambda model: False,
        'get_headers_and_cookies': AsyncMock(return_value=({}, {})),
        'get_session': AsyncMock(return_value=SimpleNamespace(request=send)),
        'get_client_timeout': lambda **kwargs: None,
        'cleanup_response': AsyncMock(),
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'JSONCodec': json,
        're': re,
        'log': logging.getLogger(__name__),
        'HTTPException': HTTPException,
        'JSONResponse': JSONResponse,
        'StreamingResponse': StreamingResponse,
    }
    load_functions(ns, 'routers/openai.py', {'generate_chat_completion'})
    return request, ns, captured


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
@pytest.mark.parametrize('stream', [True, False])
async def test_native_router_dispatches_after_access_and_budget(sender, protocol, stream):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(
        return_value=(
            'https://gateway.example.test/custom/v1',
            'synthetic',
            {'api_type': protocol, 'prefix_id': 'connection'},
        )
    )
    payload = {
        'model': 'connection.native-test',
        'stream': stream,
        'messages': [{'role': 'system', 'content': 'Frozen instructions'}, {'role': 'user', 'content': 'Hello'}],
    }
    with pytest.raises(CapturedSend):
        await ns['generate_chat_completion'](request, payload, SimpleNamespace(role='admin'))
    ns['check_model_access'].assert_awaited_once()
    sent = captured['send']
    wire = json.loads(sent['data'])
    if protocol == 'anthropic':
        assert sent['url'] == 'https://gateway.example.test/custom/v1/messages'
        assert wire['max_tokens'] == captured['budget']['max_tokens'] == 4096
        assert wire['stream'] is stream
    else:
        suffix = ':streamGenerateContent?alt=sse' if stream else ':generateContent'
        assert sent['url'] == f'https://gateway.example.test/custom/v1/models/native-test{suffix}'
        assert wire['contents'][0]['parts'] == [{'text': 'Hello'}]
    assert captured['budget']['_native_transport']['api_type'] == protocol
    assert '_native_transport' not in wire
    assert payload['model'] == 'connection.native-test'


@pytest.mark.asyncio
async def test_native_anthropic_passthrough_uses_explicit_protocol_not_host():
    ns = {'is_anthropic_url': lambda url: False}
    load_functions(ns, 'utils/anthropic.py', {'is_anthropic_messages_passthrough'})
    assert ns['is_anthropic_messages_passthrough']('https://gateway.example.test/v1', {'api_type': 'anthropic'})
    assert not ns['is_anthropic_messages_passthrough']('https://gateway.example.test/v1', {'api_type': 'gemini'})


@pytest.fixture
def transport():
    ns = {
        'asyncio': asyncio,
        'json': json,
        'aiohttp': aiohttp,
        'HTTPException': HTTPException,
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST': 10,
        'get_headers_and_cookies': AsyncMock(return_value=({'x-custom': 'retained'}, {'session': 'synthetic'})),
        'cleanup_response': AsyncMock(),
        'normalize_models': normalize_models,
        'stream_response': stream_response,
    }
    from open_webui.utils.native_providers import validate_connection

    ns['validate_connection'] = validate_connection
    load_functions(ns, 'utils/native_connections.py', {'discover_native_models', 'native_chat_stream'})
    return ns


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_discovery_paginates_with_shared_client_and_preserves_gateway_path(transport, protocol):
    pages = (
        [{'models': [{'name': 'models/one'}], 'nextPageToken': 'page2'}, {'models': [{'name': 'models/two'}]}]
        if protocol == 'gemini'
        else [
            {'data': [{'id': 'one'}], 'has_more': True, 'last_id': 'cursor'},
            {'data': [{'id': 'two'}], 'has_more': False},
        ]
    )
    responses = [SimpleNamespace(status=200, headers={}, json=AsyncMock(return_value=page)) for page in pages]
    send = AsyncMock(side_effect=responses)
    transport['get_session'] = AsyncMock(return_value=SimpleNamespace(request=send))
    result = await transport['discover_native_models'](
        object(), 'https://gateway.example.test/custom/v1/', 'key', {'api_type': protocol}
    )
    assert [model['id'] for model in result['data']] == ['one', 'two']
    assert send.await_count == transport['cleanup_response'].await_count == 2
    calls = [call.kwargs for call in send.await_args_list]
    assert all(call['url'] == 'https://gateway.example.test/custom/v1/models' for call in calls)
    assert all(
        call['cookies'] == {'session': 'synthetic'} and call['headers'] == {'x-custom': 'retained'} for call in calls
    )
    assert calls[1]['params']['pageToken' if protocol == 'gemini' else 'after_id'] == (
        'page2' if protocol == 'gemini' else 'cursor'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 429])
async def test_discovery_error_status_and_retry_headers_survive(transport, status):
    response = SimpleNamespace(
        status=status, headers={'Retry-After': '2'}, json=AsyncMock(return_value={'error': {'message': 'test'}})
    )
    transport['get_session'] = AsyncMock(return_value=SimpleNamespace(request=AsyncMock(return_value=response)))
    with pytest.raises(HTTPException) as raised:
        await transport['discover_native_models'](object(), 'https://example.test/v1', 'key', {'api_type': 'anthropic'})
    assert raised.value.status_code == status
    assert raised.value.headers == {'Retry-After': '2'}
    transport['cleanup_response'].assert_awaited_once_with(response)


@pytest.mark.asyncio
async def test_discovery_rejects_repeated_cursor_instead_of_looping(transport):
    response = SimpleNamespace(
        status=200, headers={}, json=AsyncMock(return_value={'models': [], 'nextPageToken': 'same'})
    )
    send = AsyncMock(return_value=response)
    transport['get_session'] = AsyncMock(return_value=SimpleNamespace(request=send))
    with pytest.raises(HTTPException, match='pagination'):
        await transport['discover_native_models'](object(), 'https://example.test/v1', 'key', {'api_type': 'gemini'})
    assert send.await_count == transport['cleanup_response'].await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['truncated', 'provider', 'cancel'])
async def test_native_stream_closes_source_and_never_completes_partial_tools(transport, failure):
    closed, output = [], []

    async def source(response, passthrough=False):
        assert passthrough
        try:
            yield b'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"f","args":{}}}]}}]}\n\n'
            if failure == 'provider':
                yield b'data: {"error":{"message":"rate limit"}}\n\n'
            elif failure == 'cancel':
                raise asyncio.CancelledError()
        finally:
            closed.append(True)

    transport['stream_wrapper'] = source
    binding = make_binding('gemini', 'https://example.test', 'native')
    with pytest.raises(asyncio.CancelledError if failure == 'cancel' else ValueError):
        async for chunk in transport['native_chat_stream'](object(), 'gemini', 'native', binding):
            output.append(chunk)
    assert closed == [True]
    assert not any(b'[DONE]' in chunk for chunk in output)
    if failure != 'cancel':
        assert b'native_provider_error' in output[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_native_nonstream_router_normalizes_response_and_cleans_up(sender, protocol):
    request, ns, captured = sender
    response_data = (
        {'candidates': [{'content': {'parts': [{'text': 'Answer'}]}, 'finishReason': 'STOP'}]}
        if protocol == 'gemini'
        else {'id': 'msg-1', 'content': [{'type': 'text', 'text': 'Answer'}], 'stop_reason': 'end_turn'}
    )
    response = SimpleNamespace(
        status=200, headers={'Content-Type': 'application/json'}, json=AsyncMock(return_value=response_data)
    )
    ns['get_session'] = AsyncMock(return_value=SimpleNamespace(request=AsyncMock(return_value=response)))
    ns['get_openai_connection'] = AsyncMock(
        return_value=('https://example.test/v1', 'key', {'api_type': protocol, 'prefix_id': 'connection'})
    )
    result = await ns['generate_chat_completion'](
        request,
        {'model': 'connection.native-test', 'messages': [{'role': 'user', 'content': 'Hi'}]},
        SimpleNamespace(role='admin'),
    )
    assert result['choices'][0]['message']['content'] == 'Answer'
    assert result['choices'][0]['message']['reasoning_details'][0]['data']['binding']['connection_id'] == '0'
    ns['cleanup_response'].assert_awaited_once_with(response)


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['lmstudio', 'llama.cpp'])
async def test_native_management_rejects_retained_provider_before_routing(provider):
    ns = {
        'HTTPException': HTTPException,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'get_openai_connection': AsyncMock(
            return_value=('https://example.test/v1', 'key', {'api_type': 'gemini', 'provider': provider})
        ),
        'MODEL_MANAGEMENT_ENDPOINTS': {provider: {}},
        'get_model_management_root_url': lambda *args: 'wrong endpoint',
    }
    load_functions(ns, 'routers/openai.py', {'get_model_management_connection'})
    with pytest.raises(HTTPException) as raised:
        await ns['get_model_management_connection'](0)
    assert raised.value.status_code == 501


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['embeddings', 'responses', 'proxy', 'speech'])
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_native_connections_reject_unsupported_endpoints_before_http(sender, endpoint, protocol):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://example.test/v1', 'key', {'api_type': protocol}))
    ns['ResponsesForm'] = object
    ns['get_verified_user'] = lambda: None
    ns['router'].api_route = lambda *args, **kwargs: lambda fn: fn
    ns['ENABLE_OPENAI_API_PASSTHROUGH'] = True
    load_functions(ns, 'routers/openai.py', {endpoint})
    payload = {'model': 'connection.native-test', 'input': 'test'}
    request.body = AsyncMock(return_value=json.dumps(payload).encode())
    form = SimpleNamespace(model=payload['model'], model_dump=lambda **kwargs: payload)
    with pytest.raises(HTTPException) as raised:
        if endpoint == 'proxy':
            await ns[endpoint]('audio/transcriptions', request, SimpleNamespace(role='admin'))
        elif endpoint == 'speech':
            await ns[endpoint](request, SimpleNamespace(role='admin'))
        else:
            await ns[endpoint](request, form if endpoint == 'responses' else payload, SimpleNamespace(role='admin'))
    assert raised.value.status_code == 501
    ns['get_session'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_approval_refresh_replays_native_blocks_without_executing_tool_twice(pipeline, protocol):
    ns = pipeline.ns
    binding = make_binding(protocol, 'https://example.test/v1', 'synthetic')
    blocks = (
        [
            {
                'functionCall': {'name': 'fetch_url', 'args': {'url': 'https://example.test/2'}},
                'thoughtSignature': 'synthetic-signature',
            }
        ]
        if protocol == 'gemini'
        else [
            {'type': 'thinking', 'thinking': 'Thought', 'signature': 'synthetic-signature'},
            {'type': 'tool_use', 'id': 'approved', 'name': 'fetch_url', 'input': {'url': 'https://example.test/2'}},
        ]
    )
    data = (
        {'candidates': [{'content': {'parts': blocks}, 'finishReason': 'STOP'}]}
        if protocol == 'gemini'
        else {'content': blocks, 'stop_reason': 'tool_use'}
    )
    native = normalize_response(protocol, data, 'synthetic', binding)['choices'][0]['message']
    call = native['tool_calls'][0]
    message = {
        'role': 'assistant',
        'model': 'synthetic',
        'output': [
            {'type': 'function_call', 'call_id': call['id'], **call['function'], 'status': 'queued', 'approved': True},
            {'type': 'reasoning', 'content': [], 'reasoning_details': native['reasoning_details']},
        ],
    }
    # Simulates persisted/refresh recovery, retaining only JSON-compatible output.
    message = json.loads(json.dumps(message))
    form = {'model': 'synthetic', 'messages': copy.deepcopy(ns['form_data']['messages'])}
    prefix = copy.deepcopy(form['messages'])

    async def save(chat_id, message_id, update, **kwargs):
        message.update(copy.deepcopy(update))

    ns.update(
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(side_effect=lambda *args: message),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
        is_saved_chat_id=lambda value: value == 'chat',
        ENABLE_PLUGINS=False,
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(
            return_value={'tool_call_id': call['id'], 'content': 'Fetched evidence'}
        ),
    )
    install_approval_store(ns, message)
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'assistant', 'sources': []}
    for _ in range(2):
        await ns['resume_tool_calls'](None, form, ns['user'], ns['model'], metadata, message)
    await ns['project_approved_response'](None, form, ns['user'], ns['model'], metadata, message)
    ns['execute_tool_call_for_output'].assert_awaited_once()
    assert form['messages'][: len(prefix)] == prefix
    _, wire = build_request(protocol, 'https://example.test/v1', form, binding)
    native_messages = wire['contents'] if protocol == 'gemini' else wire['messages']
    assistant = next(m for m in native_messages if m['role'] in ('model', 'assistant'))
    assert assistant['parts' if protocol == 'gemini' else 'content'] == blocks
    assert 'Fetched evidence' in json.dumps(native_messages[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['gemini', 'anthropic'])
async def test_manual_native_models_keep_prefix_tags_and_skip_discovery(protocol):
    ns = {
        'asyncio': asyncio,
        'log': logging.getLogger(__name__),
        'get_openai_runtime_config': AsyncMock(
            return_value=(
                True,
                ['https://example.test/v1'],
                ['synthetic'],
                {
                    '0': {
                        'api_type': protocol,
                        'model_ids': ['custom-model'],
                        'prefix_id': 'prefix',
                        'tags': [{'name': 'Team'}],
                    },
                },
            )
        ),
        'get_models_request': AsyncMock(),
    }
    load_functions(ns, 'routers/openai.py', {'get_all_models_responses'})
    responses = await ns['get_all_models_responses'](object(), SimpleNamespace(role='admin'))
    model = responses[0]['data'][0]
    assert model['id'] == 'prefix.custom-model'
    assert model['tags'] == [{'name': 'Team'}]
    assert model['connection_type'] == 'external'
    ns['get_models_request'].assert_not_awaited()


@pytest.mark.asyncio
async def test_native_model_permission_denial_precedes_transport(sender):
    request, ns, captured = sender
    ns['check_model_access'].side_effect = HTTPException(status_code=403, detail='denied')
    ns['get_openai_connection'] = AsyncMock(return_value=('https://example.test/v1', 'key', {'api_type': 'gemini'}))
    with pytest.raises(HTTPException) as raised:
        await ns['generate_chat_completion'](
            request, {'model': 'connection.native-test', 'messages': []}, SimpleNamespace(role='user')
        )
    assert raised.value.status_code == 403
    ns['get_openai_connection'].assert_not_awaited()
    ns['get_session'].assert_not_awaited()
    assert not captured


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [401, 429])
@pytest.mark.parametrize('content_type', ['application/json', 'text/event-stream'])
async def test_native_chat_error_status_retry_and_cleanup(sender, status, content_type):
    request, ns, captured = sender
    error = {'error': {'message': 'synthetic upstream failure'}}
    response = SimpleNamespace(
        status=status,
        headers={'Content-Type': content_type, 'Retry-After': '2'},
        json=AsyncMock(return_value=error),
        text=AsyncMock(return_value=json.dumps(error)),
    )
    ns['get_session'] = AsyncMock(return_value=SimpleNamespace(request=AsyncMock(return_value=response)))
    ns['publish_model_provider_request_failed'] = AsyncMock()
    ns['get_openai_connection'] = AsyncMock(
        return_value=('https://example.test/v1', 'key', {'api_type': 'anthropic', 'prefix_id': 'connection'})
    )
    result = await ns['generate_chat_completion'](
        request,
        {'model': 'connection.native-test', 'messages': [{'role': 'user', 'content': 'Hi'}]},
        SimpleNamespace(role='admin'),
    )
    assert result.status_code == status
    assert result.headers['retry-after'] == '2'
    assert json.loads(result.body) == error
    ns['cleanup_response'].assert_awaited_once_with(response)
