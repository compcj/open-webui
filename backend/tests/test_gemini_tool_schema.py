"""Exercise Gemini tool schemas at real send boundaries without importing the app."""

import copy
import json
import logging
import re
import sys
import uuid
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, HTTPException

from open_webui.utils.gemini import normalize_gemini_tool_schemas
from open_webui.utils.model_ids import strip_provider_model_prefix
from open_webui.utils.responses_reasoning import remap_reasoning_effort_for_responses
from test_web_tool_citations import load_functions


def tushare_tool():
    return {
        'type': 'function',
        'function': {
            'name': 'tushare_stk_mins',
            'description': 'Get stock minute bars.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'freq': {
                        'anyOf': [{'type': 'string'}, {'type': 'integer'}],
                        'description': 'Bar frequency in minutes.',
                        'enum': ['1min', '5min', '15min', '30min', '60min'],
                        'default': '1min',
                        'title': 'Frequency',
                        'examples': ['1min'],
                    },
                    'requests': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {'kind': {'type': 'string', 'const': 'minute', 'examples': ['minute']}},
                        },
                    },
                },
                'required': ['freq'],
            },
        },
    }


class CapturedRequest(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_model', ['gemini-4-argon', 'google/gemini-4-argon', 'opaque-alias'])
@pytest.mark.parametrize('compatibility', [None, 'default', 'gemini'])
@pytest.mark.parametrize('preset', [False, True])
@pytest.mark.parametrize('api_type', ['chat_completions', 'responses'])
async def test_server_send_normalizes_gemini_tools_before_budgeting(
    monkeypatch, provider_model, compatibility, preset, api_type
):
    base_id = f'connection.{provider_model}'
    selected_id = 'my-preset' if preset else base_id
    row = SimpleNamespace(
        id=selected_id,
        base_model_id=base_id if preset else None,
        params=SimpleNamespace(model_dump=lambda: {}),
        meta=SimpleNamespace(tool_schema_compatibility=compatibility),
    )
    base_row = SimpleNamespace(base_model_id=None, meta=SimpleNamespace(tool_schema_compatibility='gemini'))
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={base_id: {'urlIdx': 0}})),
    )
    tool = tushare_tool()
    original = copy.deepcopy(tool)
    body = {'model': selected_id, 'messages': [{'role': 'user', 'content': 'Get minute bars.'}], 'tools': [tool]}
    # Routing makes shallow metadata copies. The execution context must see
    # the final sender's mode even when an inlet selected a different model.
    validation_state = {selected_id: 'default'}
    body['metadata'] = {'_tool_schema_validation': validation_state}
    captured = {}
    sent = {}

    async def capture(request, user, payload, metadata, model_id):
        captured.update(copy.deepcopy(payload))
        assert model_id == selected_id
        return payload

    async def send(**kwargs):
        sent.update(json.loads(kwargs['data']))
        assert kwargs['url'].endswith('/responses' if api_type == 'responses' else '/chat/completions')
        raise CapturedRequest()

    dispatch = ModuleType('open_webui.utils.context_compaction_dispatch')
    dispatch.apply_context_compaction = capture
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    namespace = {
        'router': SimpleNamespace(post=lambda *a, **kw: lambda f: f),
        'Depends': Depends,
        'get_direct_chat_user': lambda: None,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Models': SimpleNamespace(
            get_model_by_id=AsyncMock(
                side_effect=lambda id: row if id == selected_id else base_row if id == base_id else None
            )
        ),
        'check_model_access': AsyncMock(),
        'get_openai_connection': AsyncMock(
            return_value=('https://provider.invalid/v1', '', {'prefix_id': 'connection', 'api_type': api_type})
        ),
        'strip_provider_model_prefix': strip_provider_model_prefix,
        'is_openai_new_model': lambda model: False,
        'get_headers_and_cookies': AsyncMock(return_value=({}, {})),
        'get_session': AsyncMock(return_value=SimpleNamespace(request=send)),
        'get_client_timeout': lambda **kwargs: None,
        'cleanup_response': AsyncMock(),
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'JSONCodec': json,
        'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses,
        're': re,
    }
    load_functions(namespace, 'routers/openai.py', {'generate_chat_completion', 'convert_to_responses_payload'})

    with pytest.raises(CapturedRequest):
        await namespace['generate_chat_completion'](request, body, SimpleNamespace(role='admin'))

    assert captured['model'] == provider_model
    expected_mode = 'gemini' if compatibility == 'gemini' or (preset and compatibility is None) else 'default'
    assert validation_state[selected_id] == expected_mode
    sent_function = sent['tools'][0] if api_type == 'responses' else sent['tools'][0]['function']
    assert sent_function['parameters'] == captured['tools'][0]['function']['parameters']
    assert tool == original
    assert 'tool_schema_compatibility' not in captured
    if compatibility == 'gemini' or (preset and compatibility is None):
        freq = captured['tools'][0]['function']['parameters']['properties']['freq']
        assert set(freq) == {'anyOf'}
        assert [branch['type'] for branch in freq['anyOf']] == ['string']
        for branch in freq['anyOf']:
            assert branch['description'] == original['function']['parameters']['properties']['freq']['description']
            assert branch['enum'] == ['1min', '5min', '15min', '30min', '60min']
            assert 'examples' not in branch
        assert sent_function['parameters']['properties']['requests']['items']['properties']['kind'] == {
            'type': 'string',
            'enum': ['minute'],
        }
    else:
        assert captured['tools'] == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'model_id,provider_id,compatibility',
    [
        ('connection.google/gemini-4-argon', 'google/gemini-4-argon', None),
        ('gemini.gpt-control', 'gpt-control', 'gemini'),
        ('connection.gemini-4-argon', None, 'default'),
        ('connection.opaque-alias', 'opaque-alias', 'gemini'),
    ],
)
async def test_direct_send_normalizes_gemini_tools_before_budgeting(monkeypatch, model_id, provider_id, compatibility):
    tool = tushare_tool()
    original = copy.deepcopy(tool)
    body = {'model': model_id, 'tools': [tool]}
    validation_state = {model_id: 'default'}
    body['metadata'] = {'_tool_schema_validation': validation_state}
    captured = {}

    async def capture(request, user, payload, metadata, model_id):
        captured.update(copy.deepcopy(payload))
        return payload

    dispatch = ModuleType('open_webui.utils.context_compaction_dispatch')
    dispatch.apply_context_compaction = capture
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    event_caller = AsyncMock(return_value={'ok': True})
    namespace = {
        'log': logging.getLogger(__name__),
        'logging': logging,
        'uuid': uuid,
        'get_event_call': AsyncMock(return_value=event_caller),
    }
    load_functions(namespace, 'utils/chat.py', {'generate_direct_chat_completion'})

    model = {'id': model_id, 'openai': {'id': provider_id}} if provider_id else {'id': model_id}
    model['info'] = {'meta': {'tool_schema_compatibility': compatibility}}
    result = await namespace['generate_direct_chat_completion'](
        SimpleNamespace(), body, SimpleNamespace(), {model_id: model}
    )

    assert result == {'ok': True}
    assert event_caller.call_args.args[0]['data']['form_data'] == captured
    assert captured['model'] == model_id
    assert validation_state[model_id] == ('gemini' if compatibility == 'gemini' else 'default')
    if compatibility == 'gemini':
        assert set(captured['tools'][0]['function']['parameters']['properties']['freq']) == {'anyOf'}
        assert captured['tools'][0]['function']['parameters']['properties']['requests']['items']['properties'][
            'kind'
        ] == {
            'type': 'string',
            'enum': ['minute'],
        }
    else:
        assert captured['tools'] == [original]
    assert tool == original


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_id', ['opaque-alias', 'gemini-test'])
@pytest.mark.parametrize('compatibility', [None, 'default', 'gemini'])
async def test_native_responses_send_uses_saved_manual_configuration(provider_id, compatibility):
    selected_id = f'connection.{provider_id}'
    row = SimpleNamespace(meta=SimpleNamespace(tool_schema_compatibility=compatibility))
    tool = {'type': 'function', **tushare_tool()['function']}
    body = {'model': selected_id, 'input': 'Get minute bars.', 'tools': [tool]}
    original = copy.deepcopy(body)
    sent = {}

    async def send(**kwargs):
        sent.update(json.loads(kwargs['data']))
        assert kwargs['url'] == 'https://provider.invalid/v1/responses'
        raise CapturedRequest()

    namespace = {
        'router': SimpleNamespace(post=lambda *a, **kw: lambda f: f),
        'Depends': Depends,
        'HTTPException': HTTPException,
        'get_direct_chat_user': lambda: None,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=row)),
        'check_model_access': AsyncMock(),
        'get_openai_connection': AsyncMock(
            return_value=('https://provider.invalid/v1', '', {'prefix_id': 'connection'})
        ),
        'strip_provider_model_prefix': strip_provider_model_prefix,
        'get_headers_and_cookies': AsyncMock(return_value=({}, {})),
        'get_session': AsyncMock(return_value=SimpleNamespace(request=send)),
        'get_client_timeout': lambda **kwargs: None,
        'cleanup_response': AsyncMock(),
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'JSONCodec': json,
    }
    load_functions(namespace, 'routers/openai.py', {'responses'})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={selected_id: {'urlIdx': 0}})))
    form = SimpleNamespace(model=selected_id, model_dump=lambda **kwargs: copy.deepcopy(body))
    with pytest.raises(CapturedRequest):
        await namespace['responses'](request, form, SimpleNamespace(role='admin'))

    expected = normalize_gemini_tool_schemas({**body, 'model': provider_id}, compatibility=compatibility)
    assert sent == expected
    assert body == original
    namespace['check_model_access'].assert_awaited_once()


def normalized_parameters(parameters):
    tool = tushare_tool()
    tool['function']['parameters'] = parameters
    payload = {'model': 'gemini-4-argon', 'tools': [tool], 'messages': [{'role': 'user', 'content': 'Hello'}]}
    original = copy.deepcopy(payload)
    result = normalize_gemini_tool_schemas(payload, compatibility='gemini')
    assert payload == original
    assert normalize_gemini_tool_schemas(result, compatibility='gemini') == result
    assert result['messages'] is payload['messages']
    return result['tools'][0]['function']['parameters']


@pytest.mark.parametrize('model', ['gemini-4-argon', 'google/gemini-4-argon', 'gpt-control', '', None])
@pytest.mark.parametrize('compatibility', [None, 'default', 'auto', True])
def test_model_names_never_enable_conversion(model, compatibility):
    payload = {'model': model, 'tools': [tushare_tool()]}
    assert normalize_gemini_tool_schemas(payload, compatibility=compatibility) is payload


@pytest.mark.parametrize('model', ['gemini-4-argon', 'models/gemini-4-argon', 'google/gemini-4-argon', 'GEMINI-test'])
def test_response_tools_and_other_tool_kinds(model):
    function = {'type': 'function', **tushare_tool()['function']}
    other = {'type': 'web_search', 'options': {'anyOf': ['literal'], 'description': 'Literal data.'}}
    payload = {'model': model, 'tools': [other, function, {'type': 'function', 'name': 'no_parameters'}]}
    original = copy.deepcopy(payload)
    result = normalize_gemini_tool_schemas(payload, compatibility='gemini')
    assert payload == original
    assert result['tools'][0] is other
    assert result['tools'][1]['name'] == function['name']
    assert set(result['tools'][1]['parameters']['properties']['freq']) == {'anyOf'}
    assert result['tools'][2] == payload['tools'][2]


@pytest.mark.parametrize('tools', [None, {}, [], [{'type': 'function', 'function': None}], [None]])
def test_absent_empty_or_malformed_tools_do_not_break_requests(tools):
    payload = {'model': 'gemini-4-argon', 'tools': tools}
    assert normalize_gemini_tool_schemas(payload, compatibility='gemini') == payload


def test_literal_conversion_preserves_anyof_only_nodes():
    parameters = {
        'anyOf': [{'type': 'string', 'const': 'minute'}, {'type': 'string', 'const': 'daily'}],
        'description': 'Request kind.',
        'examples': ['minute'],
    }
    assert normalized_parameters(parameters) == {
        'anyOf': [
            {'type': 'string', 'enum': ['minute'], 'description': 'Request kind.'},
            {'type': 'string', 'enum': ['daily'], 'description': 'Request kind.'},
        ]
    }


@pytest.mark.parametrize('explicit_type', [False, True])
def test_parent_string_constant_prunes_incompatible_union_types(explicit_type):
    parameters = {
        'anyOf': [{'type': 'string'}, {'type': 'integer'}],
        'const': 'minute',
        'description': 'Request kind.',
    }
    if explicit_type:
        parameters['type'] = 'string'
    assert normalized_parameters(parameters) == {
        'anyOf': [{'type': 'string', 'enum': ['minute'], 'description': 'Request kind.'}]
    }


def test_branch_string_constants_intersect_with_a_shared_enum_before_conversion():
    parameters = {
        'anyOf': [
            {'type': 'string', 'const': 'minute'},
            {'type': 'string', 'const': 'daily'},
            {'type': 'string', 'const': 'weekly'},
        ],
        'enum': ['minute', 'daily'],
        'description': 'Request kind.',
    }
    assert normalized_parameters(parameters) == {
        'anyOf': [
            {'type': 'string', 'enum': ['minute'], 'description': 'Request kind.'},
            {'type': 'string', 'enum': ['daily'], 'description': 'Request kind.'},
        ]
    }


def test_parent_and_branch_string_constants_prune_only_impossible_branches():
    parameters = {'anyOf': [{'const': 'daily'}, {'const': 'minute'}], 'const': 'minute'}
    assert normalized_parameters(parameters) == {'anyOf': [{'type': 'string', 'enum': ['minute']}]}


def test_string_constant_preserves_the_intersection_of_different_enum_lists():
    parameters = {
        'anyOf': [{'type': 'string', 'enum': ['minute', 'daily']}],
        'const': 'minute',
        'enum': ['minute', 'weekly'],
    }
    assert normalized_parameters(parameters) == {'anyOf': [{'type': 'string', 'enum': ['minute']}]}


def test_resolved_tushare_union_keeps_both_types_and_property_descriptions():
    # The shared StrOrInt from the representative tool collection, once its
    # definition is available inline in the standalone function parameters.
    parameters = {
        'type': 'object',
        'required': [],
        'properties': {
            'exchange': {
                'anyOf': [{'type': 'string'}, {'type': 'integer'}],
                'description': 'Exchange: SSE, SZSE or BSE.',
            },
            'fields': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Selected output fields.'},
        },
    }
    expected = copy.deepcopy(parameters)
    expected['properties']['exchange'] = {
        'anyOf': [
            {'type': 'string', 'description': 'Exchange: SSE, SZSE or BSE.'},
            {'type': 'integer', 'description': 'Exchange: SSE, SZSE or BSE.'},
        ]
    }
    assert normalized_parameters(parameters) == expected


def test_string_patterns_only_apply_to_string_branches():
    parameters = {
        'anyOf': [{'type': 'string'}, {'type': 'integer'}],
        'pattern': r'^\d+\.SH$',
        'description': 'Stock code.',
    }
    assert normalized_parameters(parameters) == {
        'anyOf': [
            {'type': 'string', 'pattern': r'^\d+\.SH$', 'description': 'Stock code.'},
            {'type': 'integer', 'description': 'Stock code.'},
        ]
    }


def test_every_tool_in_a_large_batch_is_adapted_without_changing_shared_definitions():
    # A provider rejects the entire declaration batch, even when the bad schema
    # belongs to a function the model has not called yet.
    tools = [tushare_tool() for _ in range(305)]
    for index, tool in enumerate(tools):
        tool['function']['name'] = f'synthetic_tool_{index}'
    payload = {'model': 'gemini-4-argon', 'tools': tools}
    original = copy.deepcopy(payload)
    result = normalize_gemini_tool_schemas(payload, compatibility='gemini')
    assert payload == original
    assert [tool['function']['name'] for tool in result['tools']] == [tool['function']['name'] for tool in tools]
    for tool in result['tools']:
        properties = tool['function']['parameters']['properties']
        assert properties['requests']['items']['properties']['kind'] == {'type': 'string', 'enum': ['minute']}
        assert all('examples' not in branch for branch in properties['freq']['anyOf'])
