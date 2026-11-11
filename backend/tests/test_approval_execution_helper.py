"""Approval execution uses the actual callable and result assembly boundary."""

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_web_tool_citations import load_functions


@pytest.fixture
def runtime():
    ns = {
        'Any': object,
        'JSONCodec': json,
        'log': logging.getLogger(__name__),
        'uuid4': uuid4,
        'is_saved_chat_id': lambda value: value == 'saved',
    }
    load_functions(
        ns, 'utils/middleware.py', {'execute_tool_call', 'execute_tool_call_for_output', 'tool_result_content'}
    )
    effect = AsyncMock(return_value={'data': 'result'})
    ns['get_updated_tool_function'] = AsyncMock(return_value=effect)
    ns['build_terminal_file_tool_result'] = lambda *args: None
    ns['process_tool_result'] = AsyncMock(
        return_value=('canonical result', [{'type': 'image', 'url': 'synthetic.png'}], ['embed'])
    )
    ns['terminal_event_handler'] = AsyncMock()
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
