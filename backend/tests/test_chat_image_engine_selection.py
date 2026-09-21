"""Run chat image boundaries without importing the application or its database."""

import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


BACKEND = Path(__file__).parents[1] / 'open_webui'


def extract(path, name, namespace):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('engine_id', [None, '', 'studio'])
def test_native_image_tool_forwards_the_selected_engine(engine_id):
    generate = AsyncMock(return_value=[{'id': 'image-1', 'url': '/files/image-1'}])
    namespace = {
        'Request': object,
        'UserModel': SimpleNamespace,
        'CreateImageForm': SimpleNamespace,
        'image_generations': generate,
        'JSONCodec': json,
        'is_saved_chat_id': lambda value: False,
        'log': logging.getLogger(__name__),
    }
    handler = extract('tools/builtin.py', 'generate_image', namespace)
    result = asyncio.run(
        handler(
            'Draw a tree',
            __request__=object(),
            __user__={'id': 'account-1', 'role': 'user'},
            __metadata__={'image_generation_engine_id': engine_id},
        )
    )
    assert json.loads(result)['status'] == 'success'
    assert generate.await_args.kwargs['form_data'].engine_id == engine_id
    assert generate.await_args.kwargs['form_data'].prompt == 'Draw a tree'


@pytest.mark.parametrize('engine_id', [None, '', 'studio'])
def test_legacy_image_generation_forwards_the_selected_engine(engine_id):
    generate = AsyncMock(return_value=[])
    namespace = {
        'Request': object,
        'HTTPException': HTTPException,
        'CreateImageForm': SimpleNamespace,
        'image_generations': generate,
        'Config': SimpleNamespace(get=AsyncMock(side_effect=lambda key: key == 'image_generation.enable')),
        'is_saved_chat_id': lambda value: False,
        'get_last_user_message': lambda messages: messages[-1]['content'],
        'get_images_from_messages': lambda messages: [],
        'add_or_update_system_message': lambda content, messages: messages,
        'log': logging.getLogger(__name__),
    }
    handler = extract('utils/middleware.py', 'chat_image_generation_handler', namespace)
    form = {'messages': [{'role': 'user', 'content': 'Draw a tree'}]}
    result = asyncio.run(
        handler(
            object(),
            form,
            {
                '__metadata__': {'chat_id': 'temporary:chat-1', 'image_generation_engine_id': engine_id},
                '__event_emitter__': AsyncMock(),
            },
            SimpleNamespace(id='account-1'),
        )
    )
    assert result is form
    assert generate.await_args.kwargs['form_data'].engine_id == engine_id


def test_chat_metadata_consumes_engine_id_before_calling_text_provider():
    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'metadata' for target in node.targets)
        and isinstance(node.value, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == 'user_agent' for key in node.value.keys)
    )
    form = {'image_generation_engine_id': 'studio'}
    namespace = {
        'form_data': form,
        'user': SimpleNamespace(id='account-1'),
        'request': SimpleNamespace(headers={}, state=SimpleNamespace()),
        'chat_id': 'chat-1',
        'user_message': None,
        'automation_id': None,
        'tool_servers': [],
        'chat_variables': {},
        'model': {},
        'model_item': {},
        'model_info_params': {},
        'stream_delta_chunk_size': 1,
        'reasoning_tags': [],
        'compact_token_threshold': 0,
        'tool_approval_mode': 'full',
    }
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'main.py', 'exec'), namespace)
    assert namespace['metadata'].get('image_generation_engine_id') == 'studio'
    assert 'image_generation_engine_id' not in form


def test_tool_approval_round_trip_preserves_the_original_engine_selection():
    assistant = {'id': 'assistant-1', 'parentId': 'user-1', 'model': 'text-model'}
    user_message = {'id': 'user-1', 'content': 'Draw a tree'}

    async def save(chat_id, message_id, update, **kwargs):
        assistant.update(update)

    namespace = {
        'Chats': SimpleNamespace(
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(side_effect=save),
            get_message_by_id_and_message_id=AsyncMock(
                side_effect=lambda chat_id, message_id: assistant if message_id == 'assistant-1' else user_message
            ),
        )
    }
    pause = extract('utils/middleware.py', 'pause_for_tool_approval', namespace)
    resume = extract('utils/tool_approval.py', 'build_tool_approval_resume_payload', namespace)
    asyncio.run(
        pause(
            'chat-1',
            'assistant-1',
            [{'type': 'function_call', 'call_id': 'call-1', 'name': 'generate_image'}],
            {},
            {'image_generation_engine_id': 'studio', 'features': {'image_generation': True}},
        )
    )
    payload = asyncio.run(resume('chat-1', 'assistant-1', SimpleNamespace(chat={}, variables={})))
    assert payload.get('image_generation_engine_id') == 'studio'
    assert payload['features']['image_generation'] is True
