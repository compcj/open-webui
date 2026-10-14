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
from fastapi import Depends

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
                    }
                },
                'required': ['freq'],
            },
        },
    }


class CapturedRequest(BaseException):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_model', ['gemini-4-argon', 'google/gemini-4-argon', 'gpt-control'])
@pytest.mark.parametrize('preset', [False, True])
@pytest.mark.parametrize('api_type', ['chat_completions', 'responses'])
async def test_server_send_normalizes_gemini_tools_before_budgeting(monkeypatch, provider_model, preset, api_type):
    base_id = f'connection.{provider_model}'
    selected_id = 'my-preset' if preset else base_id
    row = SimpleNamespace(base_model_id=base_id, params=SimpleNamespace(model_dump=lambda: {})) if preset else None
    request = SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(OPENAI_MODELS={base_id: {'urlIdx': 0}})),
    )
    tool = tushare_tool()
    original = copy.deepcopy(tool)
    body = {'model': selected_id, 'messages': [{'role': 'user', 'content': 'Get minute bars.'}], 'tools': [tool]}
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
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=row)),
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
    sent_function = sent['tools'][0] if api_type == 'responses' else sent['tools'][0]['function']
    assert sent_function['parameters'] == captured['tools'][0]['function']['parameters']
    assert tool == original
    if 'gemini' in provider_model:
        freq = captured['tools'][0]['function']['parameters']['properties']['freq']
        assert set(freq) == {'anyOf'}
        assert [branch['type'] for branch in freq['anyOf']] == ['string']
        for branch in freq['anyOf']:
            assert branch['description'] == original['function']['parameters']['properties']['freq']['description']
            assert branch['enum'] == ['1min', '5min', '15min', '30min', '60min']
    else:
        assert captured['tools'] == [original]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'model_id,provider_id,is_gemini',
    [
        ('connection.google/gemini-4-argon', 'google/gemini-4-argon', True),
        ('gemini.gpt-control', 'gpt-control', False),
        ('connection.gemini-4-argon', None, True),
    ],
)
async def test_direct_send_normalizes_gemini_tools_before_budgeting(monkeypatch, model_id, provider_id, is_gemini):
    tool = tushare_tool()
    original = copy.deepcopy(tool)
    body = {'model': model_id, 'tools': [tool]}
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
    result = await namespace['generate_direct_chat_completion'](
        SimpleNamespace(), body, SimpleNamespace(), {model_id: model}
    )

    assert result == {'ok': True}
    assert event_caller.call_args.args[0]['data']['form_data'] == captured
    assert captured['model'] == model_id
    if is_gemini:
        assert set(captured['tools'][0]['function']['parameters']['properties']['freq']) == {'anyOf'}
    else:
        assert captured['tools'] == [original]
    assert tool == original


def normalized_parameters(parameters):
    tool = tushare_tool()
    tool['function']['parameters'] = parameters
    payload = {'model': 'gemini-4-argon', 'tools': [tool], 'messages': [{'role': 'user', 'content': 'Hello'}]}
    original = copy.deepcopy(payload)
    result = normalize_gemini_tool_schemas(payload)
    assert payload == original
    assert normalize_gemini_tool_schemas(result) == result
    assert result['messages'] is payload['messages']
    return result['tools'][0]['function']['parameters']


@pytest.mark.parametrize(
    'path',
    [
        ('properties', 'freq'),
        ('$defs', 'Frequency'),
        ('definitions', 'Frequency'),
        ('items',),
        ('additionalProperties',),
        ('anyOf', 0),
        ('oneOf', 0),
        ('allOf', 0),
        ('prefixItems', 0),
    ],
)
def test_nested_union_keeps_types_nullability_defaults_and_descriptions(path):
    schema = {
        'anyOf': [{'type': 'string', 'description': 'Named frequency.'}, {'type': 'integer'}, {'type': 'null'}],
        'description': 'Select a frequency.',
        'default': None,
    }
    parameters = schema
    for part in reversed(path):
        parameters = [parameters] if isinstance(part, int) else {part: parameters}
    normalized = normalized_parameters(parameters)
    for part in path:
        normalized = normalized[part]

    assert normalized == {
        'anyOf': [
            {'type': 'string', 'description': 'Select a frequency.\nNamed frequency.', 'default': None},
            {'type': 'integer', 'description': 'Select a frequency.', 'default': None},
            {'type': 'null', 'description': 'Select a frequency.', 'default': None},
        ]
    }


def test_property_names_and_default_example_data_are_not_schemas():
    literal = {'anyOf': [{'type': 'string'}, {'type': 'integer'}], 'description': 'Literal data.', '$ref': 'literal'}
    parameters = {
        'type': 'object',
        'properties': {
            'anyOf': {'type': 'string'},
            'description': {'type': 'string'},
            'default': {'type': 'object', 'default': literal, 'examples': [literal], 'enum': [literal]},
        },
        'required': ['anyOf', 'description'],
        'additionalProperties': False,
    }
    assert normalized_parameters(parameters) == parameters


def test_constraints_at_union_and_branch_levels_are_retained():
    parameters = {
        'anyOf': [{'type': 'string', 'minLength': 1}, {'type': 'integer', 'minimum': 1}],
        'enum': ['1min', '5min', 1, 5],
        'title': 'Frequency',
    }
    assert normalized_parameters(parameters) == {
        'anyOf': [
            {'type': 'string', 'minLength': 1, 'enum': ['1min', '5min'], 'title': 'Frequency'},
            {'type': 'integer', 'minimum': 1, 'enum': [1, 5], 'title': 'Frequency'},
        ]
    }


@pytest.mark.parametrize(
    'schema_type,expected',
    [
        ('string', ['1min']),
        ('integer', [1, 1.0]),
        ('number', [1, 1.0, 1.5]),
        ('boolean', [True, False]),
        ('null', [None]),
        ('array', [[]]),
        ('object', [{}]),
    ],
)
def test_finite_enums_only_keep_values_accepted_by_each_branch_type(schema_type, expected):
    parameters = {
        'anyOf': [{'type': schema_type}],
        'enum': ['1min', 1, 1.0, 1.5, True, False, None, [], {}],
    }
    result = normalized_parameters(parameters)
    assert result == {'anyOf': [{'type': schema_type, 'enum': expected}]}


def test_an_impossible_union_remains_invalid_instead_of_becoming_permissive():
    parameters = {'anyOf': [{'type': 'integer'}], 'enum': ['1min']}
    assert normalized_parameters(parameters) == parameters


def test_references_to_union_branches_keep_their_original_target_and_meaning():
    parameters = {
        'type': 'object',
        'definitions': {
            'frequency': {'anyOf': [{'type': 'integer'}, {'type': 'string'}], 'enum': ['1min']},
        },
        'properties': {
            'count': {'$ref': '#/definitions/frequency/anyOf/0'},
            'freq': {'$ref': '#/definitions/frequency'},
        },
    }
    assert normalized_parameters(parameters) == parameters


@pytest.mark.parametrize('reference_key', ['$ref', '$dynamicRef', '$recursiveRef', '$id', '$anchor', '$dynamicAnchor'])
def test_addressable_schema_documents_are_not_restructured(reference_key):
    parameters = {
        'anyOf': [{'type': 'integer', reference_key: 'frequency'}, {'type': 'string'}],
        'enum': ['1min'],
    }
    assert normalized_parameters(parameters) == parameters


@pytest.mark.parametrize(
    'parameters',
    [
        {'anyOf': [{'type': 'string'}, {'type': 'integer'}], 'type': 'string'},
        {'anyOf': [{'enum': ['1min']}, {'enum': ['5min']}], 'enum': ['1min', '5min']},
        {'anyOf': [{'const': 1}, {'const': True}], 'const': True},
        {'anyOf': [{'enum': [1]}, {'enum': [True]}], 'enum': [True]},
        {'anyOf': [{'$ref': '#/definitions/Frequency'}, {'type': 'integer'}], 'enum': ['1min', '5min']},
        {'anyOf': [{'$ref': '#/$defs/Frequency'}], '$defs': {'Frequency': {'type': 'string'}}},
        {'anyOf': [{'properties': {'freq': {'type': 'string'}}}], 'additionalProperties': False},
        {'anyOf': [True, False], 'description': 'Boolean schemas.'},
        {'anyOf': [], 'description': 'Invalid empty union.'},
    ],
)
def test_unsupported_intersections_are_not_silently_weakened(parameters):
    assert normalized_parameters(parameters) == parameters


@pytest.mark.parametrize('model', ['gpt-control', 'not-gemini', 'geminious', '', None])
def test_non_gemini_payload_is_unchanged(model):
    payload = {'model': model, 'tools': [tushare_tool()]}
    assert normalize_gemini_tool_schemas(payload) is payload


@pytest.mark.parametrize('model', ['gemini-4-argon', 'models/gemini-4-argon', 'google/gemini-4-argon', 'GEMINI-test'])
def test_response_tools_and_other_tool_kinds(model):
    function = {'type': 'function', **tushare_tool()['function']}
    other = {'type': 'web_search', 'options': {'anyOf': ['literal'], 'description': 'Literal data.'}}
    payload = {'model': model, 'tools': [other, function, {'type': 'function', 'name': 'no_parameters'}]}
    original = copy.deepcopy(payload)
    result = normalize_gemini_tool_schemas(payload)
    assert payload == original
    assert result['tools'][0] is other
    assert result['tools'][1]['name'] == function['name']
    assert set(result['tools'][1]['parameters']['properties']['freq']) == {'anyOf'}
    assert result['tools'][2] == payload['tools'][2]


@pytest.mark.parametrize('tools', [None, {}, [], [{'type': 'function', 'function': None}], [None]])
def test_absent_empty_or_malformed_tools_do_not_break_requests(tools):
    payload = {'model': 'gemini-4-argon', 'tools': tools}
    assert normalize_gemini_tool_schemas(payload) == payload
