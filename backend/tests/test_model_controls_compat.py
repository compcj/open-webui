import ast
import collections.abc
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1] / 'open_webui'


def helper(path, names, namespace=None):
    tree = ast.parse((ROOT / path).read_text(encoding='utf8'))
    if path == 'utils/payload.py':
        names = {*names, '_overlay_params'}
    selected = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    scope = {'deepcopy': deepcopy, 'HTTPException': HTTPException, **(namespace or {})}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), scope)
    return scope


# Load the real dependency without importing the backend environment/database.
deep_update = helper('utils/misc.py', ['deep_update'], {'collections': collections})['deep_update']


def test_controls_precedence_and_reset():
    apply = helper('utils/payload.py', ['apply_model_controls'], {'deep_update': deep_update})['apply_model_controls']
    controls = {
        'reasoning': {
            'default': 'high',
            'options': {
                'high': {'params': {'reasoning_effort': 'high'}},
                'low': {'params': {'reasoning_effort': 'low'}},
            },
        }
    }
    base = {'reasoning_effort': 'global', 'custom_params': {'reasoning_effort': 'global'}}
    assert (
        apply(deepcopy(base), controls, {'reasoning': 'low'}, advanced_params={'reasoning_effort': 'personal'})[
            'custom_params'
        ]['reasoning_effort']
        == 'low'
    )
    assert (
        apply(deepcopy(base), controls, {}, advanced_params={'reasoning_effort': 'personal'})['custom_params'][
            'reasoning_effort'
        ]
        == 'personal'
    )
    assert (
        apply(deepcopy(base), controls, {}, model_params={'reasoning_effort': 'model'})['custom_params'][
            'reasoning_effort'
        ]
        == 'high'
    )
    assert apply(deepcopy(base), {}, {}, model_params={'reasoning_effort': 'model'}) == base
    with pytest.raises(HTTPException):
        apply({}, controls, {'reasoning': 'removed'})


def test_nested_default_does_not_overwrite_advanced_params():
    apply = helper('utils/payload.py', ['apply_model_controls'], {'deep_update': deep_update})['apply_model_controls']
    controls = {
        'output': {
            'default': 'brief',
            'options': {'brief': {'params': {'reasoning': {'effort': 'low', 'summary': 'auto'}, 'temperature': 0.2}}},
        }
    }
    result = apply({}, controls, {}, advanced_params={'custom_params': {'reasoning': {'effort': 'high'}}})
    assert result['custom_params'] == {'reasoning': {'effort': 'high', 'summary': 'auto'}, 'temperature': 0.2}


def test_model_migration_new_format_authority_and_custom_ids():
    migrate = helper('models/models.py', ['migrate_legacy_model_controls'])['migrate_legacy_model_controls']
    data = {
        'params': {'reasoning_effort': 'very high/自定义'},
        'meta': {'available_reasoning_effort': ['low', 'very high/自定义', 'low'], 'context_window': 100},
    }
    migrated = migrate(data)
    control = migrated['params']['model_controls']['reasoning']
    assert list(control['options']) == ['option1', 'option2']
    assert control['options']['option2']['params'] == {'reasoning_effort': 'very high/自定义'}
    assert control['default'] == 'option2'
    assert 'available_reasoning_effort' not in migrated['meta']
    assert data['meta']['available_reasoning_effort'] == ['low', 'very high/自定义', 'low']
    new = migrate({'params': {'model_controls': {}}, 'meta': {'available_reasoning_effort': ['low']}})
    assert new['params']['model_controls'] == {}


def test_public_control_params_only_expose_scalar_reasoning():
    public = helper('utils/models.py', ['public_model_params'])['public_model_params']
    controls = {
        'reasoning': {
            'label': 'Reasoning',
            'options': {
                'low': {
                    'label': 'Low',
                    'params': {'reasoning_effort': 'low', 'secret': 'hidden', 'nested': {'secret': 'hidden'}},
                },
                'bad': {'label': 'Bad', 'params': {'reasoning_effort': {'secret': 'hidden'}}},
            },
        }
    }
    result = public({'model_controls': controls, 'api_key': 'hidden'})
    assert result['model_controls']['reasoning']['options']['low'] == {
        'label': 'Low',
        'params': {'reasoning_effort': 'low'},
    }
    assert result['model_controls']['reasoning']['options']['bad'] == {'label': 'Bad'}
    assert 'hidden' not in repr(result)


def test_pydantic_model_and_form_migrate_orm_and_preserve_version():
    import re
    from typing import Annotated, Any, Literal
    from types import SimpleNamespace
    from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationInfo, field_validator, model_validator

    names = {
        'migrate_legacy_model_controls',
        'normalize_model_tags',
        'strip_extracted_content_from_model_knowledge',
        'ModelControlOption',
        'ModelControl',
        'ModelParams',
        'ModelVoice',
        'ModelAvatarAnimation',
        'ModelAvatarGesture',
        'ModelVoiceAvatar',
        'ModelMeta',
        'ModelModel',
        'ModelForm',
    }
    tree = ast.parse((ROOT / 'models/models.py').read_text(encoding='utf8'))
    selected = [
        node
        for node in tree.body
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names)
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'ModelControlKey' for target in node.targets)
        )
    ]
    scope = dict(
        locals(), deepcopy=deepcopy, AccessGrantModel=BaseModel, validate_image_url=lambda value, **kwargs: value
    )
    exec(compile(ast.Module(body=selected, type_ignores=[]), 'model_controls_schema', 'exec'), scope)
    form = {
        'id': 'test',
        'name': 'Test',
        'params': {'reasoning_effort': 'custom/value'},
        'meta': {'available_reasoning_effort': ['low', 'custom/value'], 'context_window': 100},
    }
    migrated = scope['ModelForm'].model_validate(form)
    assert migrated.params.model_controls['reasoning'].default == 'option2'
    assert migrated.meta.context_window == 100
    orm = SimpleNamespace(
        **form, user_id='owner', version_id='production', base_model_id=None, is_active=True, created_at=1, updated_at=2
    )
    stored = scope['ModelModel'].model_validate(orm)
    assert stored.version_id == 'production'
    assert stored.params.model_controls['reasoning'].options['option2'].params == {'reasoning_effort': 'custom/value'}
    reset = scope['ModelForm'].model_validate({**form, 'params': {'model_controls': {}}})
    assert reset.params.model_controls == {}
    assert reset.meta.available_reasoning_effort is None


@pytest.mark.parametrize('default_value', [None, 'disabled', 0, [], {'summary': 'auto'}])
def test_explicit_control_replaces_incompatible_default_shape(default_value):
    apply = helper('utils/payload.py', ['apply_model_controls'], {'deep_update': deep_update})['apply_model_controls']
    controls = {
        'reasoning': {
            'default': 'off',
            'options': {
                'off': {'params': {'reasoning': default_value}},
                'high': {'params': {'reasoning': {'effort': 'high'}}},
            },
        }
    }
    original = deepcopy(controls)
    expected = {'effort': 'high'}
    result = apply({'custom_params': {'reasoning': None, 'unrelated': True}}, controls, {'reasoning': 'high'})
    assert result['custom_params'] == {'reasoning': expected, 'unrelated': True}
    result['custom_params']['reasoning']['effort'] = 'changed'
    assert controls == original


def test_control_shape_changes_preserve_advanced_priority_and_default_reset():
    apply = helper('utils/payload.py', ['apply_model_controls'], {'deep_update': deep_update})['apply_model_controls']
    controls = {
        'reasoning': {
            'default': 'off',
            'options': {
                'off': {'params': {'reasoning': None}},
                'high': {'params': {'reasoning': {'effort': 'high'}}},
            },
        },
        'summary': {
            'default': 'auto',
            'options': {'auto': {'params': {'reasoning': {'summary': 'auto'}}}},
        },
    }
    advanced = {'reasoning': 'old', 'custom_params': {'reasoning': {'effort': 'personal'}}}
    original = deepcopy(advanced)
    assert apply({}, controls, {}, advanced_params=advanced)['custom_params'] == {'reasoning': {'summary': 'auto'}}
    # A default can defer on its own covered fields; an explicit choice still wins.
    controls['reasoning']['default'] = 'high'
    assert apply({}, controls, {}, advanced_params=advanced)['custom_params'] == {
        'reasoning': {'effort': 'personal', 'summary': 'auto'}
    }
    assert apply({}, controls, {'reasoning': 'off'}, advanced_params=advanced)['custom_params'] == {'reasoning': None}
    assert advanced == original


def test_explicit_empty_option_replaces_its_default_and_keeps_other_controls():
    apply = helper('utils/payload.py', ['apply_model_controls'], {'deep_update': deep_update})['apply_model_controls']
    controls = {
        'reasoning': {
            'default': 'high',
            'options': {
                'high': {'params': {'reasoning_effort': 'high'}},
                'none': {'params': {}},
            },
        },
        'output': {'default': 'brief', 'options': {'brief': {'params': {'temperature': 0.2}}}},
    }
    assert apply({}, controls, {'reasoning': 'none'})['custom_params'] == {'temperature': 0.2}
    assert apply({}, controls, {})['custom_params'] == {'reasoning_effort': 'high', 'temperature': 0.2}


@pytest.mark.parametrize('ordinary_reasoning', [None, 'off', [], {'summary': 'auto'}])
@pytest.mark.parametrize('transport', ['form_openai', 'form_ollama', 'model_openai', 'model_ollama'])
def test_controls_survive_actual_provider_parameter_conversion(ordinary_reasoning, transport):
    import json
    import logging
    from typing import Callable

    names = [
        'apply_model_controls',
        'apply_params_to_form_data',
        'apply_model_params_to_body',
        'remove_open_webui_params',
        'apply_model_params_to_body_openai',
        'apply_model_params_to_body_ollama',
    ]
    scope = helper(
        'utils/payload.py',
        names,
        {'deep_update': deep_update, 'JSONCodec': json, 'Callable': Callable, 'log': logging.getLogger(__name__)},
    )
    controls = {
        'reasoning': {
            'default': 'off',
            'options': {'off': {'params': {'reasoning': None}}, 'high': {'params': {'reasoning': {'effort': 'high'}}}},
        }
    }
    params = scope['apply_model_controls']({'reasoning': deepcopy(ordinary_reasoning)}, controls, {'reasoning': 'high'})
    if transport.startswith('form_'):
        result = scope['apply_params_to_form_data'](
            {'model': 'example'}, {'owned_by': 'ollama' if transport.endswith('ollama') else 'openai'}, params
        )
    else:
        result = scope['apply_model_params_to_body_' + transport.removeprefix('model_')](params, {'model': 'example'})
    output = result['options'] if transport.endswith('ollama') else result
    assert output['reasoning']['effort'] == 'high'
    if isinstance(ordinary_reasoning, dict):
        assert output['reasoning']['summary'] == 'auto'
