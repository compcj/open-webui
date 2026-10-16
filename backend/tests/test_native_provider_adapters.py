import copy
import json

import pytest

from open_webui.utils import native_providers as native


def binding(kind='gemini', model='models/example'):
    return native.make_binding(kind, 'https://provider.example/v1beta/', model)


def test_connection_validation_and_binding():
    config = {'api_type': 'gemini', 'auth_type': 'api_key', 'extra': {'a': 1}}
    assert native.validate_connection(config) == config
    assert native.validate_connection({}) == {}
    for config in ({'api_type': 'wat'}, {'api_type': 'gemini', 'azure': True}, {'auth_type': 'api_key'}):
        with pytest.raises(ValueError):
            native.validate_connection(config)
    assert binding() == native.make_binding('gemini', 'https://provider.example/v1beta', 'models/example')
    assert 'provider.example' not in json.dumps(binding())


@pytest.mark.parametrize('kind', ['gemini', 'anthropic'])
def test_payload_tools_system_order_images_and_no_mutation(kind):
    payload = {
        'model': 'models/example',
        'max_completion_tokens': 50,
        'stream': True,
        'messages': [
            {'role': 'system', 'content': 'prefix'},
            {'role': 'user', 'content': 'hi'},
            {'role': 'system', 'content': 'runtime'},
            {
                'role': 'assistant',
                'content': None,
                'tool_calls': [
                    {'id': 'call_1', 'type': 'function', 'function': {'name': 'fetch', 'arguments': '{"x":1}'}}
                ],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call_1',
                'content': [
                    {'type': 'text', 'text': 'ok'},
                    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}},
                ],
            },
        ],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'fetch',
                    'parameters': {
                        'type': 'object',
                        'properties': {'x': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]}},
                    },
                },
            }
        ],
        'tool_choice': 'required',
        'metadata': {'private': True},
    }
    before = copy.deepcopy(payload)
    prepared = native.prepare_payload(kind, payload)
    url, body = native.build_request(kind, 'https://provider.example/v1beta', prepared, binding(kind))
    assert payload == before
    assert 'metadata' not in body
    if kind == 'gemini':
        assert url == 'https://provider.example/v1beta/models/example:streamGenerateContent?alt=sse'
        assert body['systemInstruction'] == {'parts': [{'text': 'prefix'}]}
        assert body['contents'][0]['parts'][1]['text'] == '[System reminder]\nruntime'
        assert body['contents'][-1]['parts'][0]['functionResponse']['name'] == 'fetch'
        assert body['contents'][-1]['parts'][0]['functionResponse']['parts'][0]['inlineData']['data'] == 'YQ=='
        assert (
            body['tools'][0]['functionDeclarations'][0]['parametersJsonSchema']
            == payload['tools'][0]['function']['parameters']
        )
        assert body['generationConfig']['maxOutputTokens'] == 50
    else:
        assert url.endswith('/messages')
        assert body['system'] == [{'type': 'text', 'text': 'prefix'}]
        assert body['max_tokens'] == 50
        assert body['messages'][-1]['content'][0]['type'] == 'tool_result'
        assert body['messages'][-1]['content'][0]['content'][1]['source']['type'] == 'base64'


def test_defaults_config_models_and_unsupported():
    assert native.prepare_payload('anthropic', {})['max_tokens'] == 4096
    _, body = native.build_request(
        'gemini',
        'https://p/v1',
        {
            'model': 'publishers/google/models/a',
            'messages': [],
            'temperature': 0.5,
            'generationConfig': {'temperature': 0.2, 'thinkingConfig': {'thinkingBudget': 25}},
        },
        binding(model='publishers/google/models/a'),
    )
    assert body['generationConfig'] == {'temperature': 0.2, 'thinkingConfig': {'thinkingBudget': 25}}
    for kind in ('gemini', 'anthropic'):
        with pytest.raises(ValueError):
            native.build_request(
                kind,
                'https://p',
                {'model': 'x', 'messages': [{'role': 'user', 'content': [{'type': 'audio'}]}]},
                binding(kind),
            )
    assert (
        native.normalize_models(
            'gemini',
            {'models': [{'name': 'models/a'}, {'name': 'models/b', 'supportedGenerationMethods': ['embedContent']}]},
        )['data'][0]['id']
        == 'a'
    )
    assert (
        len(
            native.normalize_models(
                'gemini', {'models': [{'name': 'models/b', 'supportedGenerationMethods': ['embedContent']}]}
            )['data']
        )
        == 0
    )


@pytest.mark.parametrize(
    'kind,blocks',
    [
        (
            'gemini',
            [
                {'text': 'thought', 'thought': True, 'thoughtSignature': 'sig'},
                {'text': 'answer'},
                {'functionCall': {'name': 'fetch', 'args': {'x': 1}}, 'thoughtSignature': 'call-sig'},
            ],
        ),
        (
            'anthropic',
            [
                {'type': 'thinking', 'thinking': 'thought', 'signature': 'sig'},
                {'type': 'redacted_thinking', 'data': 'opaque'},
                {'type': 'text', 'text': 'answer'},
                {'type': 'tool_use', 'id': 'call_1', 'name': 'fetch', 'input': {'x': 1}},
            ],
        ),
    ],
)
def test_signed_roundtrip_and_edited_projection(kind, blocks):
    b = binding(kind)
    data = (
        {'candidates': [{'content': {'parts': blocks}, 'finishReason': 'STOP'}]}
        if kind == 'gemini'
        else {'content': blocks, 'stop_reason': 'tool_use'}
    )
    message = native.normalize_response(kind, data, 'models/example', b)['choices'][0]['message']
    assert message['reasoning_content'] == 'thought'
    assert message['reasoning_details'][0]['data']['blocks'] == blocks
    _, body = native.build_request(kind, 'https://p', {'model': 'models/example', 'messages': [message]}, b)
    assert (body['contents'][0]['parts'] if kind == 'gemini' else body['messages'][0]['content']) == blocks
    message['content'] = 'edited'
    with pytest.raises(ValueError, match='edited'):
        native.build_request(kind, 'https://p', {'model': 'models/example', 'messages': [message]}, b)
    _, body = native.build_request(kind, 'https://p', {'model': 'other', 'messages': [message]}, binding(kind, 'other'))
    assert 'sig' not in json.dumps(body)


async def chunks(data):
    for byte in data:
        yield bytes([byte])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['gemini', 'anthropic'])
async def test_stream_split_signature_usage_and_finish(kind):
    if kind == 'gemini':
        events = [
            {'candidates': [{'content': {'parts': [{'text': '你'}]}}]},
            {
                'candidates': [{'content': {'parts': [{'thoughtSignature': 'sig'}]}, 'finishReason': 'STOP'}],
                'usageMetadata': {
                    'promptTokenCount': 3,
                    'candidatesTokenCount': 2,
                    'thoughtsTokenCount': 1,
                    'totalTokenCount': 6,
                },
            },
        ]
    else:
        events = [
            {'type': 'message_start', 'message': {'id': 'm', 'usage': {'input_tokens': 3}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': ''}},
            {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'sig'}},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'text', 'text': '你'}},
            {'type': 'content_block_stop', 'index': 1},
            {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 2}},
            {'type': 'message_stop'},
        ]
    raw = b': comment\r\n\r\n' + ''.join('data: ' + json.dumps(e, ensure_ascii=False) + '\n\n' for e in events).encode()
    output = [part async for part in native.stream_response(kind, chunks(raw), 'models/example', binding(kind))]
    assert output[-1] == b'data: [DONE]\n\n'
    parsed = [json.loads(part.decode().removeprefix('data: ')) for part in output[:-1]]
    assert ''.join(p['choices'][0]['delta'].get('content', '') for p in parsed if p['choices']) == '你'
    envelope = next(p for p in parsed if p['choices'] and 'reasoning_details' in p['choices'][0]['delta'])
    assert envelope['choices'][0]['delta']['reasoning_details'][0]['data']['blocks']
    assert parsed[-1]['choices'][0]['finish_reason'] == 'stop'
    usage = [p['usage'] for p in parsed if p.get('usage')]
    assert len(usage) == 1
    assert usage[0]['prompt_tokens'] == 3


@pytest.mark.asyncio
async def test_truncated_and_error_streams_fail():
    with pytest.raises(ValueError, match='truncated'):
        _ = [
            p
            async for p in native.stream_response(
                'anthropic', chunks(b'data: {"type":"ping"}\n\n'), 'm', binding('anthropic')
            )
        ]
    with pytest.raises(ValueError):
        native.normalize_response('gemini', {'error': {'message': 'bad'}}, 'm', binding())


@pytest.mark.parametrize(
    'config',
    [
        {'api_type': 'anthropic', 'provider': 'azure'},
        {'api_type': 'gemini', 'auth_type': 'azure_ad'},
    ],
)
def test_native_azure_auth_conflicts(config):
    with pytest.raises(ValueError):
        native.validate_connection(config)


@pytest.mark.parametrize(
    'kind,payload',
    [
        ('anthropic', {'max_tokens': 0}),
        ('anthropic', {'thinking': {'type': 'enabled'}}),
        ('gemini', {'thinkingConfig': []}),
        ('gemini', {'generationConfig': {'thinkingConfig': {'thinkingBudget': 'many'}}}),
    ],
)
def test_native_configuration_validation(kind, payload):
    with pytest.raises(ValueError):
        native.prepare_payload(kind, payload)


def test_missing_anthropic_signature_rejected_and_provider_function_restored():
    b = binding('anthropic')
    message = native.normalize_response(
        'anthropic', {'content': [{'type': 'thinking', 'thinking': ''}]}, 'models/example', b
    )['choices'][0]['message']
    with pytest.raises(ValueError, match='signature'):
        native.build_request('anthropic', 'https://p', {'model': 'models/example', 'messages': [message]}, b)
    message = {
        'role': 'assistant',
        'content': '',
        'tool_calls': [
            {
                'id': 'a',
                'type': 'function',
                'function': {'name': 'resolved', 'arguments': '{}'},
                'provider_function': {'name': 'wrapper', 'arguments': '{"name":"resolved"}'},
            }
        ],
    }
    _, body = native.build_request('anthropic', 'https://p', {'model': 'models/example', 'messages': [message]}, b)
    assert body['messages'][0]['content'][0]['name'] == 'wrapper'


def test_empty_assistant_text_is_not_sent_with_tools():
    _, body = native.build_request(
        'anthropic',
        'https://p',
        {
            'model': 'models/example',
            'messages': [
                {
                    'role': 'assistant',
                    'content': '',
                    'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'fetch', 'arguments': '{}'}}],
                }
            ],
        },
        binding('anthropic'),
    )
    assert body['messages'][0]['content'] == [{'type': 'tool_use', 'id': 'a', 'name': 'fetch', 'input': {}}]


@pytest.mark.asyncio
async def test_gemini_stream_call_ids_match_nonstream_despite_text_fragmentation():
    fc = {'functionCall': {'name': 'fetch', 'args': {}}, 'thoughtSignature': 'sig'}
    events = [
        {'candidates': [{'content': {'parts': [{'text': 'a'}]}}]},
        {'candidates': [{'content': {'parts': [{'text': 'b'}, fc]}, 'finishReason': 'STOP'}]},
    ]
    raw = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
    output = [
        json.loads(p.decode()[6:])
        async for p in native.stream_response('gemini', chunks(raw), 'models/example', binding())
        if b'[DONE]' not in p
    ]
    streamed_call = next(
        p['choices'][0]['delta']['tool_calls'][0] for p in output if 'tool_calls' in p['choices'][0]['delta']
    )
    response = native.normalize_response(
        'gemini',
        {'candidates': [{'content': {'parts': [{'text': 'ab'}, fc]}, 'finishReason': 'STOP'}]},
        'models/example',
        binding(),
    )
    assert streamed_call['id'] == response['choices'][0]['message']['tool_calls'][0]['id']


def test_unsupported_output_does_not_hide_beside_text():
    with pytest.raises(ValueError, match='Unsupported'):
        native.normalize_response(
            'gemini',
            {'candidates': [{'content': {'parts': [{'text': 'a', 'inlineData': {'data': 'x'}}]}}]},
            'm',
            binding(),
        )


@pytest.mark.asyncio
async def test_anthropic_tool_stream_and_parallel_results():
    events = [{'type': 'message_start', 'message': {'usage': {'input_tokens': 10, 'cache_read_input_tokens': 5}}}]
    for index, call_id in enumerate(['one', 'two']):
        events += [
            {
                'type': 'content_block_start',
                'index': index,
                'content_block': {'type': 'tool_use', 'id': call_id, 'name': 'fetch', 'input': {}},
            },
            {
                'type': 'content_block_delta',
                'index': index,
                'delta': {'type': 'input_json_delta', 'partial_json': '{"x":'},
            },
            {
                'type': 'content_block_delta',
                'index': index,
                'delta': {'type': 'input_json_delta', 'partial_json': str(index) + '}'},
            },
            {'type': 'content_block_stop', 'index': index},
        ]
    events += [
        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 9}},
        {'type': 'message_stop'},
    ]
    raw = ''.join('event: unused\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
    output = [
        json.loads(p.decode()[6:])
        async for p in native.stream_response('anthropic', chunks(raw), 'models/example', binding('anthropic'))
        if b'[DONE]' not in p
    ]
    assert output[-1]['usage'] == {
        'prompt_tokens': 15,
        'completion_tokens': 9,
        'total_tokens': 24,
        'prompt_tokens_details': {'cached_tokens': 5},
    }
    details = output[-2]['choices'][0]['delta']['reasoning_details']
    blocks = details[0]['data']['blocks']
    assert [b['input'] for b in blocks] == [{'x': 0}, {'x': 1}]
    response = native.normalize_response(
        'anthropic', {'content': blocks, 'stop_reason': 'tool_use'}, 'models/example', binding('anthropic')
    )
    message = response['choices'][0]['message']
    _, body = native.build_request(
        'anthropic',
        'https://p',
        {
            'model': 'models/example',
            'messages': [
                message,
                {'role': 'tool', 'tool_call_id': 'one', 'content': '1'},
                {'role': 'tool', 'tool_call_id': 'two', 'content': '2'},
            ],
        },
        binding('anthropic'),
    )
    assert len(body['messages'][1]['content']) == 2


@pytest.mark.asyncio
async def test_stream_errors_and_cancellation_propagate():
    import asyncio

    async def cancelled():
        yield b'data: {"type":"ping"}\n\n'
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _ = [p async for p in native.stream_response('anthropic', cancelled(), 'm', binding('anthropic'))]
    with pytest.raises(ValueError, match='error'):
        _ = [
            p
            async for p in native.stream_response(
                'gemini', chunks(b'data: {"error":{"message":"bad"}}\n\n'), 'm', binding()
            )
        ]


@pytest.mark.asyncio
async def test_empty_tool_arguments_and_multiline_sse_cr_lines():
    raw = (
        b'event: message\rdata: {"type":"content_block_start",\r'
        b'data: "index":0,"content_block":{"type":"tool_use","id":"c","name":"fetch","input":{}}}\r\r'
        b'data: {"type":"content_block_stop","index":0}\r\r'
        b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\r\r'
        b'data: {"type":"message_stop"}\r\r'
    )
    output = [
        json.loads(p.decode()[6:])
        async for p in native.stream_response('anthropic', chunks(raw), 'm', binding('anthropic'))
        if b'[DONE]' not in p
    ]
    arguments = ''.join(
        c['function']['arguments'] for p in output for c in p['choices'][0]['delta'].get('tool_calls', [])
    )
    assert arguments == '{}'


def test_gemini_response_identity_prevents_cross_reply_tool_id_collision():
    data = {
        'responseId': 'first',
        'candidates': [{'content': {'parts': [{'functionCall': {'name': 'f', 'args': {}}}]}}],
    }
    first = native.normalize_response('gemini', data, 'models/example', binding())['choices'][0]['message']
    data['responseId'] = 'second'
    second = native.normalize_response('gemini', data, 'models/example', binding())['choices'][0]['message']
    assert first['tool_calls'][0]['id'] != second['tool_calls'][0]['id']


def test_unsigned_native_text_can_be_edited_without_replaying_old_blocks():
    b = binding('anthropic')
    message = native.normalize_response(
        'anthropic', {'content': [{'type': 'text', 'text': 'old'}]}, 'models/example', b
    )['choices'][0]['message']
    message['content'] = 'new'
    _, body = native.build_request('anthropic', 'https://p', {'model': 'models/example', 'messages': [message]}, b)
    assert body['messages'][0]['content'] == [{'type': 'text', 'text': 'new'}]


@pytest.mark.parametrize('kind', ['gemini', 'anthropic'])
@pytest.mark.parametrize('edit', ['signature', 'hidden', 'call'])
def test_native_replay_detects_tampered_blocks(kind, edit):
    if kind == 'gemini':
        blocks = [
            {'text': 'secret', 'thought': True, 'thoughtSignature': 's'},
            {'text': 'visible'},
            {'functionCall': {'name': 'fetch', 'args': {}}, 'thoughtSignature': 't'},
        ]
        data = {'responseId': 'r', 'candidates': [{'content': {'parts': blocks}}]}
    else:
        blocks = [
            {'type': 'thinking', 'thinking': 'secret', 'signature': 's'},
            {'type': 'text', 'text': 'visible'},
            {'type': 'tool_use', 'id': 'c', 'name': 'fetch', 'input': {}},
        ]
        data = {'content': blocks}
    message = native.normalize_response(kind, data, 'models/example', binding(kind))['choices'][0]['message']
    stored = message['reasoning_details'][0]['data']['blocks']
    if edit == 'signature':
        del stored[0]['thoughtSignature' if kind == 'gemini' else 'signature']
    elif edit == 'hidden':
        stored[0]['text' if kind == 'gemini' else 'thinking'] = 'edited hidden'
    elif kind == 'gemini':
        stored[2]['functionCall']['name'] = 'other'
    else:
        stored[2]['name'] = 'other'
    with pytest.raises(ValueError, match='signature|integrity|projection'):
        native.build_request(kind, 'https://p', {'model': 'models/example', 'messages': [message]}, binding(kind))


def test_manual_schema_field_and_explicit_json_schema():
    payload = {
        'model': 'models/example',
        'messages': [],
        '_native_tool_schema_compatibility': 'gemini',
        'tools': [{'type': 'function', 'function': {'name': 'f', 'parameters': {'type': 'OBJECT', 'nullable': True}}}],
    }
    _, body = native.build_request('gemini', 'https://p', payload, binding())
    assert body['tools'][0]['functionDeclarations'][0]['parameters'] == {'type': 'OBJECT', 'nullable': True}
    payload['tools'][0]['function']['parametersJsonSchema'] = {'type': 'object', 'additionalProperties': False}
    _, body = native.build_request('gemini', 'https://p', payload, binding())
    declaration = body['tools'][0]['functionDeclarations'][0]
    assert declaration['parametersJsonSchema'] == {'type': 'object', 'additionalProperties': False}
    assert 'parameters' not in declaration


def test_native_output_cap_is_canonical_before_budget_and_budget_can_reduce_it():
    original = {
        'model': 'models/example',
        'messages': [],
        'max_tokens': 10,
        'generationConfig': {'maxOutputTokens': 20},
    }
    prepared = native.prepare_payload('gemini', original)
    assert prepared['max_tokens'] == 20
    assert original['generationConfig']['maxOutputTokens'] == 20
    prepared['max_tokens'] = 5
    _, body = native.build_request('gemini', 'https://p', prepared, binding())
    assert body['generationConfig']['maxOutputTokens'] == 5


def test_replay_compares_native_call_projection_even_with_recomputed_digest():
    import hashlib

    b = binding('anthropic')
    data = {'content': [{'type': 'tool_use', 'id': 'c', 'name': 'fetch', 'input': {'x': 1}}]}
    message = native.normalize_response('anthropic', data, 'models/example', b)['choices'][0]['message']
    envelope = message['reasoning_details'][0]['data']
    envelope['blocks'][0]['input']['x'] = 2
    envelope['block_integrity'] = hashlib.sha256(
        json.dumps(envelope['blocks'], ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    ).hexdigest()
    with pytest.raises(ValueError, match='projection'):
        native.build_request('anthropic', 'https://p', {'model': 'models/example', 'messages': [message]}, b)


def test_native_custom_fields_are_preserved_after_existing_params_flattening():
    payload = {
        'model': 'models/example',
        'messages': [],
        'max_completion_tokens': 6000,
        'thinking': {'type': 'enabled', 'budget_tokens': 2000},
        'output_config': {'effort': 'high'},
    }
    before = copy.deepcopy(payload)
    prepared = native.prepare_payload('anthropic', payload)
    _, body = native.build_request('anthropic', 'https://p', prepared, binding('anthropic'))
    assert body['max_tokens'] == 6000
    assert body['thinking'] == payload['thinking']
    assert body['output_config'] == payload['output_config']
    assert payload == before


def test_manual_gemini_normalizer_output_keeps_native_schema_dialect():
    from open_webui.utils.gemini import normalize_gemini_tool_schemas

    payload = {
        'model': 'models/example',
        'messages': [],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'f',
                    'parameters': {'type': 'object', 'properties': {'x': {'type': ['string', 'null']}}},
                },
            }
        ],
    }
    normalized = normalize_gemini_tool_schemas(payload, compatibility='gemini')
    normalized['_native_tool_schema_compatibility'] = 'gemini'
    _, body = native.build_request('gemini', 'https://p', normalized, binding())
    assert body['tools'][0]['functionDeclarations'][0]['parameters'] == normalized['tools'][0]['function']['parameters']
    assert payload['tools'][0]['function']['parameters']['properties']['x']['type'] == ['string', 'null']


@pytest.mark.parametrize('kind', ['gemini', 'anthropic'])
@pytest.mark.parametrize('url', ['data:image/png;base64,YQ==', 'https://images.example.test/result.png'])
def test_real_tool_output_conversion_preserves_native_images(kind, url):
    from test_web_tool_citations import load_functions

    ns = {'copy': copy}
    load_functions(
        ns, 'utils/misc.py', {'convert_output_to_messages', 'get_paired_tool_call_ids', 'reconcile_tool_pairs'}
    )
    output = [
        {'type': 'function_call', 'call_id': 'call-image', 'status': 'completed', 'name': 'fetch', 'arguments': '{}'},
        {
            'type': 'function_call_output',
            'call_id': 'call-image',
            'output': [{'type': 'input_text', 'text': 'image result'}, {'type': 'input_image', 'image_url': url}],
        },
    ]
    before = copy.deepcopy(output)
    messages = ns['convert_output_to_messages'](output, raw=True)
    assert messages[1]['content'][1] == {'type': 'input_image', 'image_url': url}
    _, body = native.build_request(kind, 'https://p', {'model': 'models/example', 'messages': messages}, binding(kind))
    assert output == before
    if kind == 'anthropic':
        result = body['messages'][1]['content'][0]
        assert result['tool_use_id'] == 'call-image'
        assert result['content'][0] == {'type': 'text', 'text': 'image result'}
        expected = (
            {'type': 'base64', 'media_type': 'image/png', 'data': 'YQ=='}
            if url.startswith('data:')
            else {'type': 'url', 'url': url}
        )
        assert result['content'][1] == {'type': 'image', 'source': expected}
    else:
        parts = body['contents'][1]['parts']
        result = parts[0]['functionResponse']
        assert result['id'] == 'call-image'
        assert result['response'] == {'result': 'image result'}
        if url.startswith('data:'):
            assert result['parts'] == [{'inlineData': {'mimeType': 'image/png', 'data': 'YQ=='}}]
        else:
            assert 'parts' not in result
            assert parts[1] == {'fileData': {'fileUri': url}}


@pytest.mark.asyncio
async def test_late_gemini_response_id_keeps_emitted_call_identity_for_replay():
    events = [
        {
            'candidates': [
                {'content': {'parts': [{'functionCall': {'name': 'fetch', 'args': {'x': 1}}, 'thoughtSignature': 's'}]}}
            ]
        },
        {'responseId': 'late-id', 'candidates': [{'finishReason': 'STOP'}]},
    ]
    raw = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode()
    frames = [
        json.loads(p.decode()[6:])
        async for p in native.stream_response('gemini', chunks(raw), 'models/example', binding())
        if b'[DONE]' not in p
    ]
    call = next(p['choices'][0]['delta']['tool_calls'][0] for p in frames if 'tool_calls' in p['choices'][0]['delta'])
    call = {k: v for k, v in call.items() if k != 'index'}
    details = next(
        p['choices'][0]['delta']['reasoning_details'] for p in frames if 'reasoning_details' in p['choices'][0]['delta']
    )
    assert frames[-1]['id'] == 'late-id'
    message = {'role': 'assistant', 'content': '', 'tool_calls': [call], 'reasoning_details': details}
    _, body = native.build_request(
        'gemini',
        'https://p',
        {
            'model': 'models/example',
            'messages': [message, {'role': 'tool', 'tool_call_id': call['id'], 'content': 'ok'}],
        },
        binding(),
    )
    assert body['contents'][0]['parts'][0]['thoughtSignature'] == 's'
    assert body['contents'][1]['parts'][0]['functionResponse']['name'] == 'fetch'


@pytest.mark.parametrize('extra', [{'partialArgs': [{'jsonPath': '$.x', 'numberValue': 1}]}, {'willContinue': False}])
def test_gemini_unsupported_call_fragments_fail_before_normalized_call(extra):
    part = {'functionCall': {'name': 'fetch', **extra}}
    with pytest.raises(ValueError, match='fragment'):
        native.normalize_response(
            'gemini', {'candidates': [{'content': {'parts': [part]}}]}, 'models/example', binding()
        )


@pytest.mark.parametrize(
    'call',
    [
        {'args': {}},
        {'name': '', 'args': {}},
        {'name': 3, 'args': {}},
        {'name': 'fetch', 'args': []},
        {'name': 'fetch', 'args': 'not-json'},
    ],
)
def test_gemini_malformed_function_calls_fail(call):
    with pytest.raises(ValueError, match='name|arguments'):
        native.normalize_response(
            'gemini', {'candidates': [{'content': {'parts': [{'functionCall': call}]}}]}, 'models/example', binding()
        )


@pytest.mark.asyncio
async def test_gemini_fragment_stream_never_emits_a_complete_tool_call():
    data = {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'fetch', 'partialArgs': []}}]}}]}
    emitted = []
    with pytest.raises(ValueError, match='fragment'):
        async for part in native.stream_response(
            'gemini', chunks(('data: ' + json.dumps(data) + '\n\n').encode()), 'models/example', binding()
        ):
            emitted.append(part)
    assert emitted == []
