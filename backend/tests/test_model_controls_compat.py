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
    assert apply(deepcopy(base), controls, {})['custom_params']['reasoning_effort'] == 'high'
    assert apply(deepcopy(base), {}, {}) == base
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


def model_controls_schema():
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
    return scope


def test_pydantic_model_and_form_migrate_orm_and_preserve_version():
    from types import SimpleNamespace

    scope = model_controls_schema()
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


def legacy_pipe_form():
    return {
        'id': 'pipe-preset',
        'base_model_id': 'pipe-base',
        'name': 'Legacy Pipe',
        'params': {'custom_params': {'reasoning_effort': 'very high/自定义'}},
        'meta': {'available_reasoning_effort': ['low', 'very high/自定义'], 'context_window': 100},
    }


def check_model_controls(form, previous=None, base=None):
    import asyncio
    from types import SimpleNamespace

    scope = helper(
        'routers/models.py',
        ['_check_model_controls', '_is_legacy_pipe_reasoning_controls'],
        model_controls_schema(),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={'pipe-base': base or {'pipe': {}}})))
    asyncio.run(scope['_check_model_controls'](form, previous, SimpleNamespace(id='owner'), request))


@pytest.mark.parametrize('path', ['read-save', 'import', 'export-import', 'history-restore'])
def test_legacy_pipe_configuration_round_trips_without_mutating_snapshots(path):
    from types import SimpleNamespace

    schema = model_controls_schema()
    legacy = legacy_pipe_form()
    immutable = deepcopy(legacy)
    stored = schema['ModelModel'].model_validate(
        SimpleNamespace(**legacy, user_id='owner', version_id='old-version', is_active=True, created_at=1, updated_at=2)
    )
    if path == 'import':
        form = schema['ModelForm'].model_validate(legacy)
    elif path == 'history-restore':
        snapshot = {key: deepcopy(legacy[key]) for key in ('name', 'base_model_id', 'params', 'meta')}
        form = schema['ModelForm'](id=legacy['id'], **deepcopy(snapshot))
        form.params.model_fields_set.add('model_controls')
        assert snapshot == {key: immutable[key] for key in snapshot}
    else:
        exported = stored.model_dump()
        if path == 'export-import':
            import json

            exported = json.loads(json.dumps(exported))
            # JSON object order does not change the meaning of portable exports.
            options = exported['params']['model_controls']['reasoning']['options']
            exported['params']['model_controls']['reasoning']['options'] = dict(reversed(list(options.items())))
        exported['name'] = 'Renamed Pipe'
        form = schema['ModelForm'].model_validate(exported)
    check_model_controls(form, stored)
    result = form.model_dump()
    control = result['params']['model_controls']['reasoning']
    assert control['options']['option2']['params'] == {'reasoning_effort': 'very high/自定义'}
    assert control['default'] == 'option2'
    assert result['params']['custom_params'] == {'reasoning_effort': 'very high/自定义'}
    assert result['meta'].get('available_reasoning_effort') is None
    assert 'default_reasoning_effort' not in result['meta']
    assert stored.version_id == 'old-version'
    assert legacy == immutable


@pytest.mark.parametrize('base', [{'direct': True}, {'owned_by': 'arena'}, {'pipe': {}, 'direct': True}])
def test_legacy_reasoning_does_not_enable_controls_for_direct_or_arena(base):
    schema = model_controls_schema()
    form = schema['ModelForm'].model_validate(legacy_pipe_form())
    with pytest.raises(HTTPException) as error:
        check_model_controls(form, base=base)
    assert error.value.status_code == 400


@pytest.mark.parametrize('change', ['extra-param', 'extra-control', 'arbitrary-key', 'custom-label', 'slider'])
def test_pipe_rejects_arbitrary_model_controls(change):
    schema = model_controls_schema()
    data = schema['ModelForm'].model_validate(legacy_pipe_form()).model_dump()
    controls = data['params']['model_controls']
    if change == 'extra-param':
        controls['reasoning']['options']['option1']['params']['temperature'] = 0.2
    elif change == 'extra-control':
        controls['output'] = {
            'label': 'Output',
            'options': {'brief': {'label': 'Brief', 'params': {'temperature': 0.2}}},
        }
    elif change == 'arbitrary-key':
        controls['arbitrary'] = controls.pop('reasoning')
    elif change == 'custom-label':
        controls['reasoning']['options']['option1']['label'] = 'Unrelated preset'
    else:
        controls['reasoning']['display'] = 'slider'
    form = schema['ModelForm'].model_validate(data)
    with pytest.raises(HTTPException) as error:
        check_model_controls(form)
    assert error.value.status_code == 400


def test_pipe_reset_keeps_explicit_new_configuration_authoritative():
    schema = model_controls_schema()
    data = legacy_pipe_form()
    data['params']['model_controls'] = {}
    form = schema['ModelForm'].model_validate(data)
    previous = schema['ModelForm'].model_validate(legacy_pipe_form())
    check_model_controls(form, previous)
    assert form.params.model_controls == {}
    assert form.meta.available_reasoning_effort is None


def test_pipe_omitted_controls_preserve_legacy_compatibility_only():
    schema = model_controls_schema()
    previous = schema['ModelForm'].model_validate(legacy_pipe_form())
    form = schema['ModelForm'].model_validate({**legacy_pipe_form(), 'params': {}, 'meta': {}})
    check_model_controls(form, previous)
    assert form.params.model_controls == previous.params.model_controls
    data = previous.model_dump()
    data['params']['model_controls']['reasoning']['options']['option1']['params']['temperature'] = 0.2
    arbitrary = schema['ModelForm'].model_validate(data)
    omitted = schema['ModelForm'].model_validate({**legacy_pipe_form(), 'params': {}, 'meta': {}})
    with pytest.raises(HTTPException) as error:
        check_model_controls(omitted, arbitrary)
    assert error.value.status_code == 400


def test_provider_models_keep_arbitrary_approved_controls():
    schema = model_controls_schema()
    data = legacy_pipe_form()
    data['params']['model_controls'] = {
        'output': {'label': 'Output', 'options': {'brief': {'label': 'Brief', 'params': {'temperature': 0.2}}}}
    }
    form = schema['ModelForm'].model_validate(data)
    check_model_controls(form, base={'owned_by': 'openai'})
    assert form.params.model_controls['output'].options['brief'].params == {'temperature': 0.2}


def pipe_model_routes():
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    schema = model_controls_schema()
    legacy = legacy_pipe_form()
    stored = schema['ModelModel'].model_validate(
        {**legacy, 'user_id': 'owner', 'version_id': 'production', 'is_active': True, 'created_at': 1, 'updated_at': 2}
    )
    snapshot = {key: deepcopy(legacy[key]) for key in ('name', 'base_model_id', 'params', 'meta')}
    models = SimpleNamespace(
        get_models=AsyncMock(return_value=[stored]),
        get_models_by_ids=AsyncMock(return_value=[]),
        insert_new_model=AsyncMock(return_value=stored),
        update_model_by_id=AsyncMock(return_value=stored),
    )
    scope = {
        **schema,
        'deepcopy': deepcopy,
        'HTTPException': HTTPException,
        'BYPASS_ADMIN_ACCESS_CONTROL': True,
        'log': logging.getLogger(__name__),
        'Models': models,
        'ModelHistories': SimpleNamespace(
            get_history_by_id=AsyncMock(return_value=SimpleNamespace(id='old-version', snapshot=snapshot))
        ),
        'authorized_model_history': AsyncMock(return_value=stored),
        '_verify_knowledge_file_access': AsyncMock(),
        '_verify_background_image': AsyncMock(),
        '_verify_voice_avatar': AsyncMock(),
        '_verify_version_dependencies': AsyncMock(),
        'Config': SimpleNamespace(get=AsyncMock(return_value={})),
        'filter_allowed_access_grants': AsyncMock(return_value=[]),
        'publish_event': AsyncMock(),
        'EVENTS': SimpleNamespace(MODEL_IMPORTED='imported', MODEL_UPDATED='updated'),
    }
    tree = ast.parse((ROOT / 'routers/models.py').read_text(encoding='utf8'))
    names = {
        'model_response',
        'is_valid_model_id',
        '_is_legacy_pipe_reasoning_controls',
        '_check_model_controls',
        'export_models',
        'import_models',
        'set_model_version',
    }
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
            nodes.append(node)
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'pipe_routes', 'exec'), scope)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(MODELS={'pipe-base': {'id': 'pipe-base', 'pipe': {}}}))
    )
    return scope, request, SimpleNamespace(id='owner', role='admin'), stored, snapshot


@pytest.mark.parametrize('existing', [False, True])
def test_actual_model_export_import_keeps_legacy_pipe_configuration(existing):
    import asyncio
    from types import SimpleNamespace

    routes, request, user, stored, snapshot = pipe_model_routes()
    immutable = deepcopy(snapshot)
    exported = asyncio.run(routes['export_models'](request, [stored.id], user, None))
    options = exported[0]['params']['model_controls']['reasoning']['options']
    exported[0]['params']['model_controls']['reasoning']['options'] = dict(reversed(list(options.items())))
    if existing:
        routes['Models'].get_models_by_ids.return_value = [stored]
    assert asyncio.run(routes['import_models'](request, user, SimpleNamespace(models=exported), None)) is True
    write = routes['Models'].update_model_by_id if existing else routes['Models'].insert_new_model
    write.assert_awaited_once()
    form = write.await_args.args[1] if existing else write.await_args.kwargs['form_data']
    assert form.params.model_controls['reasoning'].options['option2'].params == {'reasoning_effort': 'very high/自定义'}
    assert form.meta.available_reasoning_effort is None
    assert snapshot == immutable


def test_actual_model_history_restore_keeps_old_snapshot_immutable():
    import asyncio
    from types import SimpleNamespace

    routes, request, user, stored, snapshot = pipe_model_routes()
    immutable = deepcopy(snapshot)
    result = asyncio.run(
        routes['set_model_version'](request, stored.id, SimpleNamespace(version_id='old-version'), user, None)
    )
    assert result['id'] == stored.id
    write = routes['Models'].update_model_by_id
    write.assert_awaited_once()
    form = write.await_args.args[1]
    assert form.params.model_controls['reasoning'].default == 'option2'
    assert write.await_args.kwargs['production_version_id'] == 'old-version'
    assert snapshot == immutable


@pytest.mark.parametrize('effort', ['low', 'true', 'false', '0', 'null', '1.5', '"quoted"'])
@pytest.mark.parametrize('choice', ['default', 'advanced'])
@pytest.mark.parametrize('conversion', ['form', 'model'])
def test_actual_pipe_handler_keeps_legacy_scalar_chat_override(effort, choice, conversion):
    import asyncio
    import inspect
    import json
    import logging
    from typing import AsyncGenerator, Callable, Generator, Iterator
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi.responses import StreamingResponse

    schema = model_controls_schema()
    legacy = legacy_pipe_form()
    legacy['params'] = {'reasoning_effort': effort if choice == 'default' else 'high'}
    legacy['meta']['available_reasoning_effort'] = ['low', 'high', effort]
    stored = schema['ModelModel'].model_validate(
        {**legacy, 'user_id': 'owner', 'is_active': True, 'created_at': 1, 'updated_at': 2}
    )
    check_model_controls(stored)
    scope = helper(
        'utils/payload.py',
        [
            'apply_model_controls',
            'apply_params_to_form_data',
            'remove_open_webui_params',
            'apply_model_params_to_body_openai',
            'apply_model_params_to_body',
        ],
        {'JSONCodec': json, 'Callable': Callable, 'log': logging.getLogger(__name__)},
    )
    # main.process_chat applies defaults before middleware converts parameters for the pipe.
    params = scope['apply_model_controls'](
        stored.params.model_dump(),
        stored.params.model_dump()['model_controls'],
        {},
        advanced_params={'reasoning_effort': effort} if choice == 'advanced' else {},
    )
    if conversion == 'form':
        body = scope['apply_params_to_form_data']({'model': stored.id, 'params': params}, {'pipe': {}})
    else:
        body = scope['apply_model_params_to_body_openai'](params, {'model': stored.id})
    pipe = AsyncMock(return_value={'result': 'synthetic'})
    scope.update(
        inspect=inspect,
        Generator=Generator,
        AsyncGenerator=AsyncGenerator,
        Iterator=Iterator,
        BaseModel=schema['BaseModel'],
        UserModel=schema['BaseModel'],
        StreamingResponse=StreamingResponse,
        BYPASS_MODEL_ACCESS_CONTROL=True,
        Models=SimpleNamespace(get_model_by_id=AsyncMock(return_value=stored)),
        get_system_oauth_token=AsyncMock(return_value=None),
        get_function_module_by_id=AsyncMock(return_value=SimpleNamespace(pipe=pipe)),
    )
    handler = helper('functions.py', ['generate_function_chat_completion'], scope)['generate_function_chat_completion']
    request = SimpleNamespace(state=SimpleNamespace(bypass_system_prompt=True))
    result = asyncio.run(handler(request, body, SimpleNamespace(id='owner')))
    assert result == {'result': 'synthetic'}
    pipe.assert_awaited_once()
    sent = pipe.await_args.kwargs['body']
    assert sent['reasoning_effort'] == effort
    assert isinstance(sent['reasoning_effort'], str)
    assert sent['model'] == 'pipe-base'
    assert 'model_controls' not in sent
    assert body['model'] == stored.id


def model_default_helpers():
    apply = helper('utils/payload.py', ['apply_model_controls'])['apply_model_controls']
    merge = helper('utils/misc.py', ['merge_model_params'])['merge_model_params']
    return helper(
        'utils/models.py',
        ['effective_reasoning_effort', 'stamp_default_reasoning_effort', 'public_model_params', 'get_all_models'],
        {'merge_model_params': merge, 'apply_model_controls': apply, 'UserModel': object},
    )


@pytest.mark.parametrize('default_source', ['meta', 'custom', 'top'])
@pytest.mark.parametrize('entry_kind', ['preset', 'override'])
def test_actual_pipe_model_list_reports_the_effective_canonical_default(default_source, entry_kind):
    import asyncio
    import copy
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    schema = model_controls_schema()
    legacy = legacy_pipe_form()
    legacy['params'] = {}
    legacy['meta']['available_reasoning_effort'] = ['low', 'high']
    if default_source == 'meta':
        legacy['meta']['default_reasoning_effort'] = 'high'
    elif default_source == 'custom':
        legacy['params']['custom_params'] = {'reasoning_effort': 'high'}
    else:
        legacy['params']['reasoning_effort'] = 'high'
    if entry_kind == 'override':
        legacy.update(id='pipe-base', base_model_id=None)
    immutable = deepcopy(legacy)
    stored = schema['ModelModel'].model_validate(
        {**legacy, 'user_id': 'owner', 'is_active': True, 'created_at': 1, 'updated_at': 2}
    )
    defaults = {'reasoning_effort': 'low'}
    scope = model_default_helpers()
    scope.update(
        copy=copy,
        log=logging.getLogger(__name__),
        Config=SimpleNamespace(get_many=AsyncMock(return_value={'models.default_params': defaults})),
        Models=SimpleNamespace(get_all_models=AsyncMock(return_value=[stored])),
        Functions=SimpleNamespace(
            get_functions_by_ids=AsyncMock(return_value=[]),
            get_function_valves_by_ids=AsyncMock(return_value={}),
        ),
        get_all_base_models=AsyncMock(return_value=[{'id': 'pipe-base', 'pipe': {}, 'owned_by': 'openai'}]),
        get_chat_variables_schema=lambda value: None,
        get_functions_cache=lambda request: {},
        ENABLE_FUNCTIONS=False,
        RedisDict=type('RedisDict', (dict,), {}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS={}, BASE_MODELS=[])))
    listed = asyncio.run(scope['get_all_models'](request))
    descriptor = next(model for model in listed if model['id'] == stored.id)
    control = descriptor['info']['params']['model_controls']['reasoning']
    assert control['options'][control['default']]['params']['reasoning_effort'] == 'high'
    assert descriptor['info']['meta']['default_reasoning_effort'] == 'high'
    assert request.app.state.MODELS[stored.id]['info']['meta']['default_reasoning_effort'] == 'high'
    assert legacy == immutable


@pytest.mark.parametrize('conversion', ['form', 'model_openai', 'model_ollama'])
def test_reasoning_string_preservation_keeps_other_custom_json_parameters(conversion):
    import json
    import logging
    from typing import Callable

    scope = helper(
        'utils/payload.py',
        [
            'apply_params_to_form_data',
            'apply_model_params_to_body_openai',
            'apply_model_params_to_body_ollama',
            'apply_model_params_to_body',
            'remove_open_webui_params',
        ],
        {'JSONCodec': json, 'Callable': Callable, 'log': logging.getLogger(__name__)},
    )
    params = {
        'custom_params': {
            'reasoning_effort': 'true',
            'temperature': '0.2',
            'enabled': 'true',
            'structured': '{"nested": [1, true]}',
        }
    }
    if conversion == 'form':
        body = scope['apply_params_to_form_data']({'model': 'test', 'params': params}, {'pipe': {}})
    else:
        body = scope['apply_model_params_to_body_' + conversion.removeprefix('model_')](params, {'model': 'test'})
        if conversion == 'model_ollama':
            body = body['options']
    assert body['reasoning_effort'] == 'true'
    assert body['temperature'] == 0.2
    assert body['enabled'] is True
    assert body['structured'] == {'nested': [1, True]}


@pytest.mark.parametrize(
    'global_params, model_params, expected',
    [
        ({'reasoning_effort': 'low'}, {}, 'low'),
        ({'reasoning_effort': 'low'}, {'reasoning_effort': 'high'}, 'high'),
        ({'reasoning_effort': 'low'}, {'custom_params': {'reasoning_effort': 'high'}}, 'high'),
        ({'custom_params': {'reasoning_effort': 'low'}}, {'custom_params': {'reasoning_effort': 'high'}}, 'high'),
    ],
)
def test_reported_ordinary_model_default_matches_custom_parameter_precedence(global_params, model_params, expected):
    scope = model_default_helpers()
    original_global, original_model = deepcopy(global_params), deepcopy(model_params)
    assert scope['effective_reasoning_effort'](global_params, model_params) == expected
    assert global_params == original_global
    assert model_params == original_model
