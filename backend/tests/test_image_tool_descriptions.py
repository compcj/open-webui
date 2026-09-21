"""Exercise real native tool assembly and cached schemas without importing the app."""

import asyncio
import copy
import inspect
import json
import logging
import re
from functools import cache
from types import SimpleNamespace
from typing import Any, Callable, get_type_hints
from unittest.mock import AsyncMock

import pytest
from langchain_core.utils.function_calling import convert_to_openai_function
from pydantic import BaseModel, Field, create_model

import test_chat_tool_features as tool_features
from test_chat_tool_features import BACKEND, extract
from test_image_generation_engines import load_engines_module, profile


@pytest.fixture
def tool_context():
    context = tool_features.ChatToolFeaturesTests()
    context.setUp()
    engines = load_engines_module()
    context.namespace.update(
        SimpleNamespace=SimpleNamespace,
        resolve_image_generation_config=engines.resolve_image_generation_config,
        resolve_image_edit_config=engines.resolve_image_edit_config,
        JSONCodec=json,
        cache=cache,
        inspect=inspect,
        re=re,
        copy=copy,
        Any=Any,
        Callable=Callable,
        BaseModel=BaseModel,
        Field=Field,
        create_model=create_model,
        get_type_hints=get_type_hints,
        convert_pydantic_model_to_openai_function_spec=convert_to_openai_function,
        UserModel=SimpleNamespace,
        CreateImageForm=SimpleNamespace,
        EditImageForm=SimpleNamespace,
        image_generations=AsyncMock(return_value=[]),
        image_edits=AsyncMock(return_value=[]),
        log=logging.getLogger(__name__),
    )
    extract(BACKEND / 'tools/builtin.py', ['generate_image', 'edit_image'], context.namespace)
    extract(
        BACKEND / 'utils/tools.py',
        [
            'parse_description',
            'parse_docstring',
            'convert_function_to_pydantic_model',
            'clean_properties',
            'clean_openai_tool_schema',
            'get_builtin_function_introspection',
            'build_builtin_tool_spec_json',
            'get_builtin_tool_spec',
        ],
        context.namespace,
    )
    # Isolate the two image tools; their implementations and schema factory are real.
    context.model = {
        'info': {
            'meta': {
                'builtinTools': {
                    'time': False,
                    'user_input': False,
                    'knowledge': False,
                    'tasks': False,
                }
            }
        }
    }
    context.config_values.update(
        {
            'image_generation.enable': True,
            'images.edit.enable': True,
            'image_generation.tool_description_suffix': ' Default generation ',
            'images.edit.tool_description_suffix': ' Default edit ',
            'image_generation.engines': [
                profile(
                    id='a',
                    name='A',
                    tool_description_suffix=' Draw in English.\n保留细节。 ',
                    edit={'engine': 'grok', 'tool_description_suffix': ' Edit A '},
                ),
                profile(
                    id='b',
                    name='B',
                    tool_description_suffix='Draw B',
                    edit={'engine': 'grok', 'tool_description_suffix': 'Edit B'},
                ),
                profile(id='empty', name='Empty', tool_description_suffix=' \n ', edit={'engine': 'grok'}),
                profile(id='default-editor', name='Default Editor', tool_description_suffix='Custom generation'),
            ],
        }
    )
    try:
        yield context
    finally:
        context.doCleanups()


def base_spec(context, name):
    return context.namespace['get_builtin_tool_spec'](context.namespace[name])


@pytest.mark.parametrize(
    ('engine_id', 'generation', 'edit'),
    [
        (None, 'Default generation', 'Default edit'),
        ('deleted', 'Default generation', 'Default edit'),
        ('a', 'Draw in English.\n保留细节。', 'Edit A'),
        ('b', 'Draw B', 'Edit B'),
        ('empty', '', ''),
        ('default-editor', 'Custom generation', 'Default edit'),
    ],
)
def test_real_tool_schemas_follow_selected_engine(tool_context, engine_id, generation, edit):
    tool_context.metadata['image_generation_engine_id'] = engine_id
    actual = tool_context.tools({'image_generation': True})
    for name, suffix in [('generate_image', generation), ('edit_image', edit)]:
        expected = base_spec(tool_context, name)
        if suffix:
            expected['description'] += '\n\n' + suffix
        assert actual[name]['spec'] == expected


def test_concurrent_requests_and_config_updates_do_not_mutate_cached_schemas(tool_context):
    before = {name: base_spec(tool_context, name) for name in ('generate_image', 'edit_image')}

    async def request(engine_id):
        return await tool_context.namespace['get_builtin_tools'](
            SimpleNamespace(state=SimpleNamespace()),
            {'__user__': tool_context.user, '__metadata__': {'image_generation_engine_id': engine_id}},
            {'image_generation': True},
            tool_context.model,
        )

    async def concurrent():
        return await asyncio.gather(request('a'), request('b'), request('a'))

    first, second, again = asyncio.run(concurrent())
    assert (
        first['generate_image']['spec']['description']
        == before['generate_image']['description'] + '\n\nDraw in English.\n保留细节。'
    )
    assert second['generate_image']['spec']['description'] == before['generate_image']['description'] + '\n\nDraw B'
    assert again['generate_image']['spec'] == first['generate_image']['spec']
    first['generate_image']['spec']['description'] = 'mutated'
    tool_context.config_values['image_generation.engines'][0]['tool_description_suffix'] = 'Updated'
    refreshed = asyncio.run(request('a'))
    assert refreshed['generate_image']['spec']['description'] == before['generate_image']['description'] + '\n\nUpdated'
    for name in before:
        assert base_spec(tool_context, name) == before[name]


def test_suffixes_are_not_added_to_generated_image_prompts(tool_context):
    tool_context.metadata['image_generation_engine_id'] = 'a'
    tools = tool_context.tools({'image_generation': True})
    asyncio.run(tools['generate_image']['callable'](prompt='A forest'))
    asyncio.run(tools['edit_image']['callable'](prompt='A red coat', image_urls=['synthetic.png']))
    generation = tool_context.namespace['image_generations'].await_args.kwargs['form_data']
    edit = tool_context.namespace['image_edits'].await_args.kwargs['form_data']
    assert (generation.prompt, generation.engine_id) == ('A forest', 'a')
    assert (edit.prompt, edit.engine_id, edit.image) == ('A red coat', 'a', ['synthetic.png'])


@pytest.mark.parametrize('gate', ['feature', 'permission', 'capability', 'category', 'generation', 'editing'])
def test_tool_descriptions_preserve_existing_image_gates(tool_context, gate):
    features = {'image_generation': gate != 'feature'}
    if gate == 'permission':
        tool_context.permission.return_value = False
    if gate == 'capability':
        tool_context.model['info']['meta']['capabilities'] = {'image_generation': False}
    if gate == 'category':
        tool_context.model['info']['meta']['builtinTools']['image_generation'] = False
    if gate == 'generation':
        tool_context.config_values['image_generation.enable'] = False
    if gate == 'editing':
        tool_context.config_values['images.edit.enable'] = False
    tools = tool_context.tools(features)
    if gate != 'editing':
        assert 'generate_image' not in tools
    if gate != 'generation':
        assert 'edit_image' not in tools


def test_empty_defaults_keep_original_schema(tool_context):
    tool_context.config_values['image_generation.tool_description_suffix'] = ' \n '
    tool_context.config_values['images.edit.tool_description_suffix'] = None
    actual = tool_context.tools({'image_generation': True})
    for name in ('generate_image', 'edit_image'):
        assert actual[name]['spec'] == base_spec(tool_context, name)
