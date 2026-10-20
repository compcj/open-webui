"""Knowledge mutations execute through existing dispatch, approval and deduplication."""

import copy
import json
from functools import partial
from unittest.mock import AsyncMock

import pytest

from test_approval_execution_helper import runtime as execution_runtime
from test_knowledge_write_tools import runtime as knowledge_runtime
from test_streaming_tool_call_dedup import runtime as stream_runtime


def install(execution, knowledge):
    function = partial(knowledge.module.write_knowledge_file, **knowledge.context)
    execution.ns['get_updated_tool_function'] = AsyncMock(return_value=function)
    return {
        'call_tool': {'type': 'builtin', 'tool_id': 'builtin:call_tool'},
        'write_knowledge_file': {
            'type': 'builtin',
            'tool_id': 'builtin:write_knowledge_file',
            'callable': function,
            'spec': {
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'knowledge_id': {'type': 'string'},
                        'filename': {'type': 'string'},
                        'content': {'type': 'string'},
                    },
                    'required': ['knowledge_id', 'filename', 'content'],
                }
            },
        },
    }


def call(wrapped):
    arguments = {'knowledge_id': 'kb', 'filename': 'draft', 'content': 'created through dispatch'}
    return {
        'id': 'write-once',
        'function': {
            'name': 'call_tool' if wrapped else 'write_knowledge_file',
            'arguments': json.dumps(
                {'tool_name': 'write_knowledge_file', 'params': json.dumps(arguments)} if wrapped else arguments
            ),
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
async def test_direct_and_dispatched_creation_obey_approval_and_keep_provider_identity(
    execution_runtime, knowledge_runtime, wrapped
):
    tools = install(execution_runtime, knowledge_runtime)
    metadata = {'tools': tools, 'params': {'tool_approval_mode': 'ask'}}
    tool_call = call(wrapped)
    original = copy.deepcopy(tool_call['function'])
    denied = await execution_runtime.ns['execute_tool_call']({}, metadata, AsyncMock(return_value=False), tool_call)
    assert denied[1].startswith('Error: tool call was not approved')
    knowledge_runtime.upload.assert_not_awaited()
    approved = AsyncMock(return_value=True)
    result = await execution_runtime.ns['execute_tool_call']({}, metadata, approved, tool_call)
    assert json.loads(result[1])['status'] == 'success'
    assert approved.call_args.args[0]['data']['tool_call']['name'] == 'write_knowledge_file'
    knowledge_runtime.upload.assert_awaited_once()
    if wrapped:
        assert tool_call['provider_function'] == original


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
async def test_approved_resume_rechecks_current_kb_permission(execution_runtime, knowledge_runtime, wrapped):
    tools = install(execution_runtime, knowledge_runtime)
    knowledge_runtime.base.user_id = 'other'
    knowledge_runtime.grant.return_value = True
    tool_call = call(wrapped)
    # Saved approvals execute through the same helper after reconstructing tools.
    # The live grant is revoked after staging and before resuming that call.
    knowledge_runtime.grant.return_value = False
    metadata = {'chat_id': 'saved', 'tools': tools, 'params': {'tool_approval_mode': 'ask'}}
    result = await execution_runtime.ns['execute_tool_call']({}, metadata, None, tool_call)
    assert 'error' in json.loads(result[1])
    knowledge_runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
async def test_completed_knowledge_write_replay_does_not_execute_again(
    execution_runtime, knowledge_runtime, stream_runtime, wrapped
):
    tools = install(execution_runtime, knowledge_runtime)
    stream = stream_runtime.ns
    stream['metadata'] = {'tools': tools}
    stream['execute_tool_call'] = AsyncMock(side_effect=execution_runtime.ns['execute_tool_call'])
    tool_call = call(wrapped)
    original = copy.deepcopy(tool_call)
    stream['tool_calls'] = [[tool_call]]
    await stream['execute_queued_calls']()
    knowledge_runtime.upload.assert_awaited_once()
    stream['output'].append(
        {
            'type': 'function_call_output',
            'call_id': tool_call['id'],
            'status': 'completed',
            'output': [{'type': 'input_text', 'text': '{"status":"success","file_id":"new-file"}'}],
        }
    )
    stream['prior_output'] = copy.deepcopy(stream['output'])
    stream['tool_calls'] = [[original]]
    await stream['execute_queued_calls']()
    knowledge_runtime.upload.assert_awaited_once()
    stream['execute_tool_call'].assert_awaited_once()
