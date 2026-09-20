"""Exercise the managed-terminal callable used by every tool execution mode."""

import ast
import asyncio
import copy
import importlib.util
import json
import mimetypes
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

BACKEND = Path(__file__).parents[1] / 'open_webui'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
async def test_callable_enriches_display_result_with_authenticated_owner(wrapped):
    spec = importlib.util.spec_from_file_location('terminal_policy_tools', BACKEND / 'utils/terminal_files.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    data = {'path': '/work/report.svg', 'exists': True}
    execute = AsyncMock(return_value=(data, {}) if wrapped else data)
    server = {'url': 'https://terminal.test', 'specs': [{'name': 'display_file', 'description': 'Display a file.'}]}

    async def prepare(fn, extras):
        return fn

    ns = {
        'asyncio': asyncio,
        'copy': copy,
        'Config': SimpleNamespace(get=AsyncMock(return_value=[{'id': 't'}])),
        'build_terminal_request_context': AsyncMock(return_value=(server, {'X-Terminal-Context-Id': 'chat:c'}, {})),
        'get_terminal_cwd': AsyncMock(return_value='/work'),
        'get_terminal_system_prompt': AsyncMock(return_value=None),
        'clean_openai_tool_schema': lambda schema: schema,
        'execute_tool_server': execute,
        'get_async_tool_function_and_apply_extra_params': prepare,
        'TERMINAL_CONTEXT_HEADER': 'X-Terminal-Context-Id',
        'WEBUI_SECRET_KEY': 'synthetic-secret',
        'add_file_delivery_links': policy.add_file_delivery_links,
        'TERMINAL_FILE_DELIVERY_PROMPT': getattr(policy, 'TERMINAL_FILE_DELIVERY_PROMPT', ''),
    }
    tree = ast.parse((BACKEND / 'utils/tools.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {'get_terminal_tools', 'add_terminal_display_file_inline_param'}
    ]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), '<managed terminal tools>', 'exec'), ns)
    tools, prompt = await ns['get_terminal_tools'](
        object(), 't', SimpleNamespace(id='owner'), {'__metadata__': {'chat_id': 'c', 'user_id': 'forged'}}
    )
    result = await tools['display_file']['callable'](path='/work/report.svg', page=2)
    result_data = result[0] if wrapped else result
    assert result_data['owner_id'] == 'owner'
    reference = parse_qs(urlsplit(result_data['download_url']).query)['ref'][0]
    payload = policy.verify_file_reference(reference, 'synthetic-secret')
    assert payload['chat_id'] == 'c' and payload['context_id'] == 'chat:c'
    assert 'download_url' in prompt and 'image_url' in prompt
    assert 'do not display the same file again or emit Markdown' not in tools['display_file']['spec']['description']
    assert execute.call_args.kwargs['params'] == {'path': '/work/report.svg'}


@pytest.mark.asyncio
async def test_signed_results_render_bound_cards_without_unscoped_file_browser_events():
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {'build_terminal_file_tool_result', 'terminal_event_handler'}
    ]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes],
        type_ignores=[],
    )
    ns = {'JSONCodec': json, 'mimetypes': mimetypes, 'os': os}
    exec(compile(ast.fix_missing_locations(module), '<terminal delivery events>', 'exec'), ns)
    data = {
        'path': '/work/report.svg',
        'exists': True,
        'download_url': '/api/v1/terminals/t/files/download?ref=signed',
        'image_url': '/api/v1/terminals/t/files/image?ref=signed',
        'owner_id': 'owner',
    }
    params = {'path': data['path']}
    item = ns['build_terminal_file_tool_result'](
        'display_file', params, data, {'tool_id': 'terminal:t'}, {'chat_id': 'c'}
    )
    assert item['displayed'] is True
    assert item['download_url'] == data['download_url'] and item['terminal_selector'] == 't'
    emit = AsyncMock()
    await ns['terminal_event_handler']('display_file', params, json.dumps(data), emit)
    emit.assert_not_awaited()
    # Old messages/direct terminals keep their existing file-browser event.
    await ns['terminal_event_handler']('display_file', params, json.dumps({'path': data['path'], 'exists': True}), emit)
    emit.assert_awaited_once_with({'type': 'terminal:display_file', 'data': {'path': data['path']}})
