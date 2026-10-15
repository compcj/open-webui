"""Approval execution uses the actual callable and result assembly boundary."""

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.fixture
def runtime():
    path = Path(__file__).parents[1] / 'open_webui/utils/middleware.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'execute_tool_call', 'execute_tool_call_for_output', 'tool_result_content'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {node.name for node in nodes} == names, 'Approval execution helper must exist in production'
    ns = {
        'Any': object,
        'JSONCodec': json,
        'ast': ast,
        'log': logging.getLogger(__name__),
        'uuid4': uuid4,
        'is_saved_chat_id': lambda value: value == 'saved',
    }
    # Load the production dispatcher without importing application/database state.
    dispatch_path = path.with_name('tool_search.py')
    dispatch_tree = ast.parse(dispatch_path.read_text(encoding='utf-8'))
    dispatch_nodes = [
        node for node in dispatch_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'resolve_tool_call'
    ]
    assert len(dispatch_nodes) == 1
    ns.update(CALL_TOOL_NAME='call_tool', SEARCH_TOOL_NAME='search_tools')
    exec(compile(ast.Module(body=dispatch_nodes, type_ignores=[]), str(dispatch_path), 'exec'), ns)
    effect = AsyncMock(return_value={'data': 'result'})
    ns['get_updated_tool_function'] = AsyncMock(return_value=effect)
    ns['build_terminal_file_tool_result'] = lambda *args: None
    ns['process_tool_result'] = AsyncMock(
        return_value=('canonical result', [{'type': 'image', 'url': 'synthetic.png'}], ['embed'])
    )
    ns['terminal_event_handler'] = AsyncMock()
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return SimpleNamespace(ns=ns, effect=effect)


@pytest.mark.asyncio
async def test_approved_callable_result_is_assembled_once_with_files_and_embeds(runtime):
    ns = runtime.ns
    tool = {'callable': runtime.effect, 'type': 'builtin', 'spec': {'parameters': {'properties': {'query': {}}}}}
    call = {'id': 'call', 'function': {'name': 'lookup', 'arguments': '{"query":"test","ignored":"no"}'}}
    result = await ns['execute_tool_call_for_output'](
        object(), {'messages': []}, object(), {'chat_id': 'saved', 'tools': {'lookup': tool}}, None, None, call
    )
    runtime.effect.assert_awaited_once_with(query='test')
    assert result == {
        'tool_call_id': 'call',
        'content': 'canonical result',
        'files': [{'type': 'image', 'url': 'synthetic.png'}],
        'embeds': ['embed'],
    }
    ns['process_tool_result'].assert_awaited_once()
    ns['terminal_event_handler'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', ['invalid {', '[]', '{}'])
async def test_invalid_or_unavailable_tool_returns_error_without_side_effects(runtime, arguments):
    ns = runtime.ns
    call = {'id': 'call', 'function': {'name': 'missing', 'arguments': arguments}}
    result = await ns['execute_tool_call_for_output'](
        object(), {'messages': []}, object(), {'chat_id': 'saved', 'tools': {}}, None, None, call
    )
    assert result['tool_call_id'] == 'call' and result['content'].startswith('Error:')
    runtime.effect.assert_not_awaited()
    ns['process_tool_result'].assert_not_awaited()
