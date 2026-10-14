"""Provider-schema and original argument validation contracts (no live provider)."""

import copy
import ast
import json
import logging
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from open_webui.utils.gemini import normalize_gemini_tool_schemas
from open_webui.utils.gemini_schema import (
    GeminiSchemaError,
    compile_gemini_schema,
    resolve_gemini_schema,
    validate_gemini_tool_arguments,
)
from jsonschema import Draft202012Validator
from test_web_tool_citations import load_functions


def convert(schema):
    body = {'tools': [{'type': 'function', 'function': {'name': 'sample', 'parameters': schema}}]}
    original = copy.deepcopy(body)
    result = normalize_gemini_tool_schemas(body, compatibility='gemini')
    assert body == original
    assert normalize_gemini_tool_schemas(result, compatibility='gemini') == result
    return result['tools'][0]['function']['parameters']


def property_schema(schema):
    return convert({'type': 'object', 'properties': {'value': schema}})['properties']['value']


def test_local_references_resolve_against_original_document_before_pruning():
    schema = {
        '$defs': {'a/b~c': {'anyOf': [{'type': 'integer'}, {'type': 'string'}], 'enum': ['day']}},
        'type': 'object',
        'properties': {
            'count': {'$ref': '#/$defs/a~1b~0c/anyOf/0'},
            'kind': {'$ref': '#/$defs/a~1b~0c', 'description': 'Selected kind'},
        },
    }
    result = convert(schema)
    assert '$defs' not in result
    assert result['properties']['count'] == {'type': 'integer'}
    assert result['properties']['kind'] == {'type': 'string', 'enum': ['day'], 'description': 'Selected kind'}


def test_allof_intersects_overlapping_object_properties_and_required():
    result = convert(
        {
            'allOf': [
                {'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 1}}, 'required': ['count']},
                {'type': 'object', 'properties': {'count': {'maximum': 8}, 'label': {'type': 'string'}}},
            ]
        }
    )
    assert result['type'] == 'object'
    assert result['properties']['count'] == {'type': 'integer', 'minimum': 1, 'maximum': 8}
    assert result['required'] == ['count']
    assert result['properties']['label']['type'] == 'string'


def test_large_string_enum_intersection_preserves_order_and_values():
    left = [f'code-{index}' for index in range(4000)]
    right = left[2000:] + ['outside']
    result = property_schema({'allOf': [{'enum': left}, {'enum': right}]})
    assert result == {'type': 'string', 'enum': left[2000:]}


def test_complex_enum_intersection_has_a_work_limit_before_comparing_values():
    with pytest.raises(GeminiSchemaError, match='enum intersection work limit'):
        compile_gemini_schema({'allOf': [{'enum': list(range(100))}, {'enum': list(range(1, 101))}]})


@pytest.mark.parametrize('keyword', ['const', 'default', 'examples'])
def test_deep_literal_data_fails_with_a_safe_schema_error(keyword):
    literal = 'private-value'
    for _ in range(600):
        literal = {'value': literal}
    with pytest.raises(GeminiSchemaError) as error:
        compile_gemini_schema({'type': 'object', keyword: literal})
    assert 'private-value' not in str(error.value)


def test_type_array_and_nullable_do_not_add_fields_next_to_anyof():
    result = property_schema({'type': ['string', 'integer', 'null'], 'description': 'Optional code'})
    assert set(result) == {'anyOf'}
    assert {branch['type'] for branch in result['anyOf']} == {'string', 'integer'}
    assert all(branch['nullable'] is True for branch in result['anyOf'])
    assert all(branch['description'] == 'Optional code' for branch in result['anyOf'])


@pytest.mark.parametrize('value', [True, False, 1, 1.5, None, '', {'tag': 'x'}, [1, 'x']])
def test_constants_never_reach_native_schema_and_never_coerce_values(value):
    result = property_schema({'const': value})
    assert 'const' not in result
    assert 'enum' not in result or all(isinstance(item, str) for item in result['enum'])
    if isinstance(value, bool):
        assert result['type'] == 'boolean'
        assert json.dumps(value) in result['description']
    elif type(value) in (int, float):
        assert result['minimum'] == result['maximum'] == value
    elif value == '':
        assert result['minLength'] == result['maxLength'] == 0


def test_mixed_enum_keeps_json_types_distinct():
    result = property_schema({'enum': ['day', 1, True, None]})
    assert set(result) == {'anyOf'}
    branches = result['anyOf']
    assert {branch['type'] for branch in branches} == {'string', 'integer', 'boolean'}
    assert next(branch for branch in branches if branch['type'] == 'integer')['minimum'] == 1
    assert next(branch for branch in branches if branch['type'] == 'boolean')['description']
    assert all(branch['nullable'] for branch in branches)


def test_oneof_and_unsupported_constraints_are_hinted_not_sent_as_unknown_fields():
    result = property_schema(
        {
            'oneOf': [{'type': 'integer', 'multipleOf': 2}, {'type': 'integer', 'minimum': 10}],
            'examples': [2, 12],
            'deprecated': True,
        }
    )
    assert set(result) == {'anyOf'}
    assert all('multipleOf' not in branch and 'oneOf' not in branch for branch in result['anyOf'])
    assert all('oneOf' in branch['description'] for branch in result['anyOf'])


def test_literal_defaults_and_property_names_are_not_schema_keywords():
    literal = {'examples': [1], '$ref': 'private literal', 'const': True}
    result = convert(
        {
            'type': 'object',
            'properties': {
                'examples': {'type': 'object', 'default': literal, 'example': literal, 'examples': [literal]},
                'const': {'type': 'string'},
                '$ref': {'type': 'string'},
            },
            'additionalProperties': False,
        }
    )
    assert result['properties']['examples']['default'] == literal
    assert result['properties']['examples']['example'] == literal
    assert 'examples' not in result['properties']['examples']
    assert 'additionalProperties' not in result
    assert 'additionalProperties' in result['description']
    assert set(result['properties']) == {'examples', 'const', '$ref'}


def test_tuple_items_are_projected_with_length_and_original_order_constraint_hint():
    result = property_schema(
        {
            'type': 'array',
            'prefixItems': [{'type': 'string'}, {'type': 'integer'}],
            'items': False,
            'minItems': 2,
        }
    )
    assert result['minItems'] == result['maxItems'] == 2
    assert {item['type'] for item in result['items']['anyOf']} == {'string', 'integer'}
    assert 'prefixItems' in result['description']


@pytest.mark.parametrize(
    'schema,marker',
    [
        ({'$ref': 'https://example.invalid/private-schema'}, '$ref'),
        ({'$ref': '#/missing'}, '$ref'),
        ({'$ref': '#'}, '$ref'),
        ({'$defs': {'Loop': {'$ref': '#/$defs/Loop'}}, '$ref': '#/$defs/Loop'}, '$ref'),
        ({'$dynamicRef': '#node'}, '$dynamicRef'),
        ({'type': 'array'}, 'items'),
        ({'type': 'integer', 'const': 'secret-value'}, 'const'),
        ({'type': 'strng'}, 'type'),
    ],
)
def test_unrepresentable_or_invalid_schema_has_safe_local_diagnostic(schema, marker):
    with pytest.raises(HTTPException) as exc:
        property_schema(schema)
    assert exc.value.status_code == 400
    assert 'sample' in exc.value.detail
    assert marker in exc.value.detail
    assert 'secret-value' not in exc.value.detail
    assert 'https://example.invalid' not in exc.value.detail


def test_reference_expansion_and_depth_are_bounded():
    schema = {'type': 'string'}
    for _ in range(80):
        schema = {'type': 'array', 'items': schema}
    with pytest.raises(HTTPException, match='depth'):
        property_schema(schema)


def test_reference_expansion_has_a_byte_budget_not_only_node_count():
    schema = {
        'type': 'object',
        '$defs': {'Text': {'type': 'string', 'description': 'x' * 16384}},
        'properties': {f'p{i}': {'$ref': '#/$defs/Text'} for i in range(128)},
    }
    with pytest.raises(GeminiSchemaError, match='limit'):
        compile_gemini_schema(schema)


def test_numeric_enum_expansion_is_bounded():
    with pytest.raises(GeminiSchemaError, match='limit'):
        compile_gemini_schema({'enum': list(range(129))})


def test_impossible_branch_is_removed_but_impossible_schema_fails():
    assert property_schema({'anyOf': [{'type': 'integer'}, {'type': 'string'}], 'enum': ['day']}) == {
        'anyOf': [{'type': 'string', 'enum': ['day']}]
    }
    with pytest.raises(HTTPException):
        property_schema({'anyOf': [{'type': 'integer'}], 'enum': ['day']})


def execution_namespace():
    ns = {
        'ast': ast,
        'JSONCodec': json,
        'log': logging.getLogger(__name__),
        'is_saved_chat_id': lambda value: False,
        'get_updated_tool_function': AsyncMock(side_effect=lambda **kw: kw['function']),
        'uuid4': lambda: 'request-id',
    }
    load_functions(ns, 'utils/middleware.py', {'execute_tool_call'})
    return ns


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('arguments', [{'enabled': False}, {'enabled': 1}, {'enabled': True, 'extra': 'secret'}])
async def test_original_schema_rejects_invalid_calls_before_approval_or_execution(direct, arguments):
    ns = execution_namespace()
    callback = AsyncMock(return_value=True)
    function = AsyncMock(return_value='executed')
    tool = {
        'type': 'mcp',
        'direct': direct,
        'callable': function,
        'spec': {
            'parameters': {
                'type': 'object',
                'properties': {'enabled': {'const': True}},
                'required': ['enabled'],
                'additionalProperties': False,
            }
        },
    }
    metadata = {
        'tools': {'sample': tool},
        '_tool_schema_compatibility': 'gemini',
        'params': {'tool_approval_mode': 'ask'},
    }
    call = {'function': {'name': 'sample', 'arguments': json.dumps(arguments)}}
    _, result, *_ = await ns['execute_tool_call']({}, metadata, callback, call)
    assert 'Error:' in result and 'schema' in result.lower()
    assert 'secret' not in result
    function.assert_not_awaited()
    callback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'arguments',
    ["{1: 'value'}", "{'payload': {1: 'value'}}", "{'payload': (1, 2)}", "{'payload': b'value'}", '{"payload": NaN}'],
)
async def test_non_json_argument_values_return_errors_before_callbacks(arguments):
    ns = execution_namespace()
    function = AsyncMock(return_value='executed')
    callback = AsyncMock(return_value=True)
    metadata = {
        'tools': {'sample': {'callable': function, 'spec': {'parameters': {'type': 'object'}}}},
        '_tool_schema_compatibility': 'gemini',
        'params': {'tool_approval_mode': 'ask'},
    }
    _, result, *_ = await ns['execute_tool_call'](
        {}, metadata, callback, {'function': {'name': 'sample', 'arguments': arguments}}
    )
    assert result.startswith('Error:')
    function.assert_not_awaited()
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_ref_arguments_reach_tool_unchanged_including_allowed_extra_fields():
    ns = execution_namespace()
    function = AsyncMock(return_value='done')
    schema = {
        '$defs': {
            'Args': {
                'type': 'object',
                'properties': {'code': {'type': ['string', 'null']}},
                'required': ['code'],
                'additionalProperties': {'type': 'integer'},
            }
        },
        '$ref': '#/$defs/Args',
    }
    metadata = {
        'tools': {'sample': {'callable': function, 'spec': {'parameters': schema}}},
        '_tool_schema_compatibility': 'gemini',
    }
    call = {'function': {'name': 'sample', 'arguments': '{"code": null, "limit": 5}'}}
    _, result, *_ = await ns['execute_tool_call']({}, metadata, None, call)
    assert result == 'done'
    function.assert_awaited_once_with(code=None, limit=5)


@pytest.mark.asyncio
async def test_default_mode_preserves_existing_argument_filtering():
    ns = execution_namespace()
    function = AsyncMock(return_value='done')
    metadata = {
        'tools': {
            'sample': {
                'callable': function,
                'spec': {
                    'parameters': {
                        'type': 'object',
                        'properties': {'enabled': {'const': True}},
                    }
                },
            }
        }
    }
    await ns['execute_tool_call'](
        {}, metadata, None, {'function': {'name': 'sample', 'arguments': '{"enabled": false, "extra": 1}'}}
    )
    function.assert_awaited_once_with(enabled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('reserved', ['__user__', '__metadata__', '__event_call__', '__files__', '__messages__'])
async def test_gemini_arguments_cannot_override_injected_runtime_context(reserved):
    ns = execution_namespace()
    function = AsyncMock(return_value='done')
    metadata = {
        'tools': {
            'sample': {
                'callable': function,
                'spec': {
                    'parameters': {
                        'type': 'object',
                        'properties': {'query': {'type': 'string'}},
                    }
                },
            }
        },
        '_tool_schema_compatibility': 'gemini',
    }
    _, result, *_ = await ns['execute_tool_call'](
        {},
        metadata,
        None,
        {'function': {'name': 'sample', 'arguments': json.dumps({'query': 'query', reserved: {'role': 'admin'}})}},
    )
    assert 'Error:' in result
    function.assert_not_awaited()


@pytest.mark.parametrize(
    'schema,valid,invalid',
    [
        ({'type': ['string', 'null']}, ['a', None], [1]),
        ({'enum': ['a', None]}, ['a', None], ['b', False]),
        ({'type': 'string', 'nullable': True}, ['a', None], [False]),
        ({'const': True}, [True], [False, 1]),
        ({'const': ''}, [''], ['a', None]),
        ({'const': 1}, [1, 1.0], [True, 2]),
        ({'enum': [1, True, None]}, [1, True, None], [False, 2]),
        ({'const': {'a': 1}}, [{'a': 1}], [{'a': True}, {'a': 1, 'b': 2}]),
        ({'const': [1, 'a']}, [[1, 'a']], [['a', 1], [1, 'b']]),
        ({'type': 'array', 'items': {'type': 'integer'}, 'uniqueItems': True}, [[1, 2]], [[1, 1]]),
        ({'type': 'array', 'items': False}, [[]], [[1]]),
        (
            {'type': 'array', 'items': [{'type': 'string'}, {'type': 'integer'}], 'additionalItems': False},
            [[], ['a'], ['a', 1]],
            [[1], ['a', 1, 2]],
        ),
        ({'oneOf': [{'type': 'integer'}, {'type': 'number'}]}, [1.5], [1, True]),
        ({'type': 'integer', 'multipleOf': 2}, [2, 4], [3]),
        ({'type': 'integer', 'exclusiveMinimum': 1.5, 'exclusiveMaximum': 4}, [2, 3], [1, 4]),
        ({'type': 'number', 'exclusiveMinimum': 1}, [1.01], [1]),
        ({'type': 'string', 'not': {'enum': ['bad']}}, ['good'], ['bad']),
        (
            {'type': 'object', 'properties': {'a': {'type': 'integer'}}, 'additionalProperties': False},
            [{}, {'a': 2}],
            [{'a': 2, 'extra': True}],
        ),
        (
            {
                'type': 'object',
                'properties': {'a': {'type': 'integer'}, 'b': {'type': 'boolean'}},
                'dependentRequired': {'a': ['b']},
            },
            [{}, {'a': 1, 'b': True}],
            [{'a': 1}],
        ),
        (
            {
                'type': 'object',
                'properties': {'a': {'type': 'integer'}},
                'if': {'required': ['a']},
                'then': {'properties': {'a': {'minimum': 1}}},
            },
            [{}, {'a': 1}],
            [{'a': 0}],
        ),
        ({'type': 'array', 'items': {'type': 'integer'}, 'contains': {'const': 2}}, [[1, 2]], [[1, 3]]),
        ({'allOf': [{'type': 'string', 'pattern': '^a'}, {'pattern': 'z$'}]}, ['az'], ['ab', 'zz']),
        (
            {
                'allOf': [
                    {'type': 'array', 'prefixItems': [{'type': 'boolean'}], 'items': {'type': 'number'}},
                    {
                        'type': 'array',
                        'prefixItems': [{'type': 'boolean'}, {'type': 'number'}],
                        'items': {'type': 'string'},
                    },
                ]
            },
            [[True, 2]],
            [[True, 2, 3]],
        ),
    ],
)
def test_projection_accepts_every_valid_example_and_execution_keeps_original_constraints(schema, valid, invalid):
    compiled = property_schema(schema)
    projection = Draft202012Validator(resolve_gemini_schema(compiled))
    for value in valid:
        assert validate_gemini_tool_arguments(schema, value) is None
        assert projection.is_valid(value), (schema, compiled, value)
    for value in invalid:
        assert validate_gemini_tool_arguments(schema, value) is not None


@pytest.mark.parametrize(
    'schema',
    [
        {'dependencies': None},
        {'type': 'invalid'},
        {'anyOf': []},
        {'enum': None},
        {'type': 'object', 'properties': []},
        {'const': 1, 'enum': [True]},
        {'allOf': [{'const': True}, {'const': 1}]},
        {'properties': {'a': {'type': 'string'}}},
        {'type': 'integer', 'exclusiveMinimum': 1, 'maximum': 1},
        {'type': 'array', 'prefixItems': [{'type': 'string'}], 'items': False, 'minItems': 2},
    ],
)
def test_invalid_or_untyped_property_schemas_fail_locally(schema):
    with pytest.raises(HTTPException):
        normalize_gemini_tool_schemas(
            {
                'tools': [
                    {
                        'type': 'function',
                        'function': {
                            'name': 'sample',
                            'parameters': {'type': 'object', 'properties': {'value': schema}},
                        },
                    }
                ]
            },
            compatibility='gemini',
        )


def test_supported_schema_grammar_is_closed_at_every_nested_node():
    fields = set(
        'type format title description nullable enum maxItems minItems properties required minProperties '
        'maxProperties minLength maxLength pattern example anyOf propertyOrdering default items minimum maximum'.split()
    )
    source = {
        'type': 'object',
        'properties': {
            'value': {'oneOf': [{'const': True}, {'type': 'array', 'items': {'enum': [1, 2]}}]},
            'map': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
        },
        'additionalProperties': False,
    }
    result = convert(source)

    def check(node):
        assert set(node) <= fields
        if 'anyOf' in node:
            assert set(node) == {'anyOf'}
            for branch in node['anyOf']:
                check(branch)
        if 'enum' in node:
            assert node['type'] == 'string'
            assert all(isinstance(value, str) and value != '' for value in node['enum'])
        for child in node.get('properties', {}).values():
            check(child)
        if 'items' in node:
            check(node['items'])

    check(result)
    assert compile_gemini_schema(source).diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode,legacy,expected', [('gemini', False, 'gemini'), ('default', False, 'default'), ('gemini', True, 'default')]
)
async def test_selected_manual_mode_overwrites_untrusted_execution_metadata(mode, legacy, expected):
    from open_webui.utils.gemini import configure_gemini_tool_validation

    metadata = {
        '_tool_schema_compatibility': 'untrusted',
        'params': {'function_calling': 'legacy' if legacy else 'native'},
    }
    await configure_gemini_tool_validation(metadata, {'info': {'meta': {'tool_schema_compatibility': mode}}})
    assert metadata['_tool_schema_compatibility'] == expected


@pytest.mark.asyncio
async def test_frozen_call_requires_validation_after_switching_to_a_default_model():
    from open_webui.utils.gemini import mark_gemini_tool_calls

    function = AsyncMock(return_value='must not execute')
    metadata = {
        'tools': {
            'sample': {
                'callable': function,
                'spec': {
                    'parameters': {
                        'type': 'object',
                        'properties': {'enabled': {'const': True}},
                    }
                },
            }
        },
        '_tool_schema_validation': {'first': 'default', 'second': 'gemini'},
    }
    call = {'id': 'call', 'function': {'name': 'sample', 'arguments': '{"enabled":false}'}}
    output = [{'type': 'function_call', 'call_id': 'call', 'name': 'sample', 'arguments': '{"enabled":false}'}]
    mark_gemini_tool_calls([call], output, metadata, 'second')
    assert output[0]['tool_schema_compatibility'] == 'gemini'
    ns = execution_namespace()
    _, result, *_ = await ns['execute_tool_call']({'model': 'first'}, metadata, None, call)
    assert 'schema' in result
    function.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_call_keeps_default_behavior_when_outer_request_was_gemini():
    from open_webui.utils.gemini import mark_gemini_tool_calls

    function = AsyncMock(return_value='default behavior')
    metadata = {
        'tools': {
            'sample': {
                'callable': function,
                'spec': {
                    'parameters': {
                        'type': 'object',
                        'properties': {'enabled': {'const': True}},
                    }
                },
            }
        },
        '_tool_schema_validation': {'first': 'gemini', 'second': 'default'},
    }
    call = {'id': 'call', 'function': {'name': 'sample', 'arguments': '{"enabled":false}'}}
    output = [{'type': 'function_call', 'call_id': 'call', 'name': 'sample', 'arguments': '{"enabled":false}'}]
    mark_gemini_tool_calls([call], output, metadata, 'second')
    ns = execution_namespace()
    _, result, *_ = await ns['execute_tool_call']({'model': 'first'}, metadata, None, call)
    assert result == 'default behavior'
    function.assert_awaited_once_with(enabled=False)
    assert output[0]['tool_schema_compatibility'] == 'default'


def test_saved_schema_marker_is_local_only_on_responses_replay():
    # Use the production Responses allowlist rather than a fixture copy.
    ns = {}
    tree = load_functions(ns, 'routers/openai.py', {'_normalize_stored_item'})
    import ast

    allowed = next(
        n
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, 'id', '') == 'RESPONSES_ALLOWED_FIELDS'
    )
    exec(compile(ast.Module(body=[allowed], type_ignores=[]), 'response_allowlist', 'exec'), ns)
    stored = {
        'type': 'function_call',
        'call_id': 'call',
        'name': 'sample',
        'arguments': '{}',
        'tool_schema_compatibility': 'gemini',
    }
    assert 'tool_schema_compatibility' not in ns['_normalize_stored_item'](stored)
