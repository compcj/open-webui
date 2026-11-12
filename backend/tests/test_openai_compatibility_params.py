import importlib
import json
import logging
from copy import deepcopy
from typing import Callable

import pytest
from pydantic import ValidationError

from test_model_controls_compat import helper, model_controls_schema


STRING_PARAMETERS = (
    'prompt_cache_key',
    'prompt_cache_retention',
    'session_id',
    'user',
    'safety_identifier',
    'service_tier',
)


def apply_parameters(transport, params, body=None):
    scope = helper(
        'utils/payload.py',
        {
            'apply_params_to_form_data',
            'apply_model_params_to_body',
            'remove_open_webui_params',
            'apply_model_params_to_body_openai',
            'apply_model_params_to_body_ollama',
        },
        {'JSONCodec': json, 'Callable': Callable, 'log': logging.getLogger(__name__)},
    )
    body = {'model': 'synthetic-model', **(body or {})}
    if transport.startswith('form_'):
        owner = transport.removeprefix('form_')
        model = {'pipe': {}} if owner == 'pipe' else {'owned_by': owner}
        return scope['apply_params_to_form_data'](body, model, params)
    return scope['apply_model_params_to_body_' + transport.removeprefix('model_')](params, body)


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai'])
@pytest.mark.parametrize('key', STRING_PARAMETERS)
@pytest.mark.parametrize('value', ['12345', 'true', 'false', 'null', '1.5', '{"value":1}', '[1,2]'])
def test_string_parameters_keep_json_looking_ui_text(transport, key, value):
    result = apply_parameters(transport, {'custom_params': {key: value}})

    assert result[key] == value
    assert isinstance(result[key], str)


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai'])
@pytest.mark.parametrize('key', STRING_PARAMETERS)
@pytest.mark.parametrize('value', ['12345', 'true', 'quoted "identifier"', 'session-测试', ' line\nnext '])
def test_explicitly_json_quoted_string_parameters_still_decode(transport, key, value):
    result = apply_parameters(transport, {'custom_params': {key: json.dumps(value)}})

    assert result[key] == value


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai'])
@pytest.mark.parametrize('value', ['12345', 'true', 'null', '"quoted"', '  false  '])
def test_reasoning_effort_remains_an_exact_literal(transport, value):
    result = apply_parameters(transport, {'custom_params': {'reasoning_effort': value}})

    assert result['reasoning_effort'] == value


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai', 'form_ollama', 'model_ollama'])
def test_generic_custom_parameters_keep_json_types(transport):
    params = {
        'custom_params': {
            'structured': '{"enabled":true,"count":3,"options":[1,null]}',
            'number': '12345',
            'flag': 'false',
            'fraction': '1.5',
            'plain_text': 'not-json',
        }
    }

    result = apply_parameters(transport, params)
    output = result['options'] if transport.endswith('ollama') else result

    assert output['structured'] == {'enabled': True, 'count': 3, 'options': [1, None]}
    assert output['number'] == 12345
    assert output['flag'] is False
    assert output['fraction'] == 1.5
    assert output['plain_text'] == 'not-json'


@pytest.mark.parametrize('transport', ['form_ollama', 'model_ollama'])
def test_ollama_custom_string_names_keep_existing_json_interpretation(transport):
    result = apply_parameters(transport, {'custom_params': {'user': '12345', 'session_id': 'false'}})

    assert result['options']['user'] == 12345
    assert result['options']['session_id'] is False


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai'])
def test_custom_parameter_parsing_does_not_mutate_nested_caller_values(transport):
    custom = {'prompt_cache_key': '12345', 'extra_body': '{"nested":{"values":[1,2]}}'}
    original = deepcopy(custom)

    result = apply_parameters(transport, {'custom_params': custom})
    result['extra_body']['nested']['values'].append(3)

    assert custom == original


@pytest.mark.parametrize('transport', ['form_openai', 'form_pipe', 'model_openai'])
def test_request_top_level_strings_win_without_interpolation_or_generated_keys(transport):
    result = apply_parameters(
        transport,
        {'custom_params': {'prompt_cache_key': '12345'}},
        {'prompt_cache_key': '{{chat_id}}-${user.id}'},
    )

    assert result['prompt_cache_key'] == '{{chat_id}}-${user.id}'
    assert not set(STRING_PARAMETERS).difference({'prompt_cache_key'}).intersection(result)


def expand(payload, enabled=False):
    module = importlib.import_module('open_webui.utils.openai_compatibility')
    return module.expand_extra_body(payload, enabled=enabled)


@pytest.mark.parametrize('extra', [{'prompt_cache_key': '12345'}, 'provider-owned', None, [], False])
def test_extra_body_expansion_defaults_off(extra):
    payload = {'model': 'synthetic-model', 'extra_body': deepcopy(extra)}
    original = deepcopy(payload)

    assert expand(payload) == original
    assert payload == original


def test_extra_body_expansion_keeps_explicit_top_level_values_wholesale():
    payload = {
        'model': 'synthetic-model',
        'prompt_cache_key': 'top-level',
        'flag': False,
        'nullable': None,
        'nested': {},
        'extra_body': {
            'model': 'other-model',
            'prompt_cache_key': 'nested',
            'flag': True,
            'nullable': 'nested',
            'nested': {'must_not_merge': True},
            'provider_options': {'enabled': True},
        },
    }
    original = deepcopy(payload)

    result = expand(payload, enabled=True)

    assert result == {
        'model': 'synthetic-model',
        'prompt_cache_key': 'top-level',
        'flag': False,
        'nullable': None,
        'nested': {},
        'provider_options': {'enabled': True},
    }
    result['provider_options']['enabled'] = False
    result['nested']['local_only'] = True
    assert payload == original


@pytest.mark.parametrize('extra', ['{}', 'invalid', None, [], 1, False])
def test_enabled_extra_body_expansion_rejects_non_objects_without_mutation(extra):
    payload = {'model': 'synthetic-model', 'extra_body': extra}
    original = deepcopy(payload)

    with pytest.raises(ValueError, match='extra_body must be a JSON object'):
        expand(payload, enabled=True)

    assert payload == original


def test_extra_body_expansion_is_idempotent_and_does_not_interpolate_or_invent_keys():
    payload = {'extra_body': {'prompt_cache_key': '{{chat_id}}', 'session_id': '${session.id}'}}

    result = expand(payload, enabled=True)

    assert result == {'prompt_cache_key': '{{chat_id}}', 'session_id': '${session.id}'}
    assert expand(result, enabled=True) == result
    assert expand({'model': 'synthetic-model'}, enabled=True) == {'model': 'synthetic-model'}


def test_model_metadata_compatibility_defaults_are_explicit_booleans():
    meta = model_controls_schema()['ModelMeta']()

    assert meta.reasoning_content_replay is True
    assert meta.extra_body_compatibility is False


@pytest.mark.parametrize('reasoning_replay', [False, True])
@pytest.mark.parametrize('extra_body', [False, True])
def test_model_metadata_compatibility_roundtrip_preserves_explicit_false(reasoning_replay, extra_body):
    schema = model_controls_schema()
    form = schema['ModelForm'](
        id='synthetic-model',
        name='Synthetic model',
        params={},
        meta={'reasoning_content_replay': reasoning_replay, 'extra_body_compatibility': extra_body},
    )
    serialized = form.model_dump()
    restored = schema['ModelForm'].model_validate(serialized)

    assert restored.meta.reasoning_content_replay is reasoning_replay
    assert restored.meta.extra_body_compatibility is extra_body
    assert serialized['meta']['reasoning_content_replay'] is reasoning_replay
    assert serialized['meta']['extra_body_compatibility'] is extra_body
    assert 'reasoning_content_replay' not in serialized['params']
    assert 'extra_body_compatibility' not in serialized['params']


@pytest.mark.parametrize('field', ['reasoning_content_replay', 'extra_body_compatibility'])
@pytest.mark.parametrize('value', ['false', 0, None, [], {}])
def test_model_metadata_compatibility_rejects_non_booleans(field, value):
    with pytest.raises(ValidationError):
        model_controls_schema()['ModelMeta'](**{field: value})
