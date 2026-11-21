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
from fastapi import HTTPException
from test_web_tool_citations import load_functions

BACKEND = Path(__file__).parents[1] / 'open_webui'


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('template', [False, True])
async def test_callable_enriches_display_result_with_authenticated_owner(wrapped, template):
    spec = importlib.util.spec_from_file_location('terminal_policy_tools', BACKEND / 'utils/terminal_files.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    data = {'path': '/work/report.svg', 'exists': True}
    execute = AsyncMock(return_value=(data, {}) if wrapped else data)
    server = {'url': 'https://terminal.test', 'specs': [{'name': 'display_file', 'description': 'Display a file.'}]}
    connection = {'id': 't', 'url': server['url']}
    if template:
        connection.update(template_id='sandbox', context_id='chat:c')
    headers = {'X-Terminal-Template-Id': 'sandbox'} if template else {'X-Terminal-Context-Id': 'chat:c'}

    async def prepare(fn, extras):
        return fn

    ns = {
        'asyncio': asyncio,
        'copy': copy,
        'ENABLE_TOOL_SERVERS': True,
        'get_terminal_server_url': lambda current: current['url'],
        'find_terminal_connection': AsyncMock(return_value=connection),
        'build_terminal_request_context': AsyncMock(return_value=(server, headers, {})),
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
    load_functions(ns, 'utils/terminals.py', {'resolved_terminal_context'})
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
    assert payload['endpoint_id'] == policy.terminal_file_endpoint_id(server['url'], 'synthetic-secret')
    assert server['url'] not in json.dumps(payload)
    assert 'download_url' in prompt and 'image_url' in prompt
    assert 'do not display the same file again or emit Markdown' not in tools['display_file']['spec']['description']
    assert execute.call_args.kwargs['params'] == {'path': '/work/report.svg'}


@pytest.mark.asyncio
@pytest.mark.parametrize('template', [False, True])
@pytest.mark.parametrize('change', ['revoke', 'endpoint', 'context', 'credentials'])
async def test_terminal_call_checks_live_acl_binding_and_credentials(template, change):
    original = {'id': 'terminal', 'url': 'https://origin.test', 'key': 'before', 'instance_user_id': 'actor'}
    if template:
        original.update(
            template_id='sandbox',
            scope='model',
            context_id='model:a',
            terminal_context={'user_id': 'actor', 'model_id': 'a'},
        )
    current = copy.deepcopy(original)
    server = {'id': 'terminal', 'url': original['url'], 'specs': [{'name': 'read_file', 'description': 'Read a file'}]}
    resolve = AsyncMock(side_effect=lambda *args, **kwargs: copy.deepcopy(current))
    execute = AsyncMock(return_value={'result': True})

    async def prepare(fn, extras):
        return fn

    ns = {
        'asyncio': asyncio,
        'copy': copy,
        'ENABLE_TOOL_SERVERS': True,
        'HTTPException': HTTPException,
        'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        'has_connection_access': AsyncMock(return_value=True),
        'has_connection_write_access': AsyncMock(return_value=False),
        'find_terminal_connection': resolve,
        'get_terminal_servers': AsyncMock(return_value=[server]),
        'get_terminal_server_url': lambda record: record['url'].rstrip('/'),
        'get_tool_server_data': AsyncMock(return_value={}),
        'convert_openapi_to_tool_payload': lambda _: server['specs'],
        'bearer_auth_header': lambda value: {'Authorization': 'Bearer ' + value},
        'terminal_context_available': lambda *args: True,
        'terminal_context_id': lambda record, *args: record.get('context_id'),
        'get_terminal_cwd': AsyncMock(return_value='/work'),
        'get_terminal_system_prompt': AsyncMock(return_value=None),
        'clean_openai_tool_schema': lambda schema: schema,
        'execute_tool_server': execute,
        'get_async_tool_function_and_apply_extra_params': prepare,
        'TERMINAL_CONTEXT_HEADER': 'X-Terminal-Context-Id',
        'TERMINAL_FILE_DELIVERY_PROMPT': 'Download file',
    }
    load_functions(ns, 'utils/terminals.py', {'terminal_headers', 'resolved_terminal_context'})
    load_functions(
        ns,
        'utils/tools.py',
        {'get_terminal_tools', 'build_terminal_request_context', 'add_terminal_display_file_inline_param'},
    )
    tools, _ = await ns['get_terminal_tools'](
        SimpleNamespace(cookies={'session': 'private'}),
        original['id'],
        SimpleNamespace(id='actor'),
        {'__metadata__': {'model_id': 'a', 'chat_id': 'c'}},
    )
    if change == 'credentials':
        current.update(key='after', forward_cookies=True)
        assert await tools['read_file']['callable'](path='/work/data') == {'result': True}
        assert execute.await_args.kwargs['headers']['Authorization'] == 'Bearer after'
        assert execute.await_args.kwargs['cookies'] == {'session': 'private'}
        if template:
            headers = execute.await_args.kwargs['headers']
            assert 'X-Terminal-Context-Id' not in headers
            assert headers['X-Terminal-Template-Id'] == 'sandbox' and headers['X-Terminal-Model-Id'] == 'a'
        return
    if change == 'revoke':
        current = None
    elif change == 'endpoint':
        current['url'] = 'https://replacement.test'
    else:
        current['instance_user_id'] = 'someone-else'
    with pytest.raises(HTTPException) as denied:
        await tools['read_file']['callable'](path='/work/data')
    assert denied.value.status_code == (403 if change == 'revoke' else 409)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_signed_results_render_bound_cards_without_unscoped_file_browser_events():
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {'build_terminal_file_tool_result', 'terminal_event_handler', 'parse_terminal_tool_result'}
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
