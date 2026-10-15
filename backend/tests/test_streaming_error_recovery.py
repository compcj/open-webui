"""Run provider streams through the real middleware without importing the app/database."""

import asyncio
import copy
import hashlib
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.images.context import build_tool_result_parts
from test_web_tool_citations import load_functions


ERROR = {'message': 'Upstream request failed', 'code': 'upstream_error'}


def chat_chunk(content='', finish_reason=None):
    return {'choices': [{'delta': {'content': content}, 'finish_reason': finish_reason}]}


def response_output(text='Recovered answer'):
    return [
        {
            'type': 'message',
            'id': 'msg-recovered',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': text}],
        }
    ]


def tool_call_chunk():
    return {
        'choices': [
            {
                'delta': {
                    'tool_calls': [{'index': 0, 'id': 'call-1', 'function': {'name': 'test_tool', 'arguments': '{}'}}]
                },
                'finish_reason': 'tool_calls',
            }
        ]
    }


@pytest.fixture
def runtime():
    events = []
    saved = {}

    async def emit(event):
        events.append(copy.deepcopy(event))

    async def save(chat_id, message_id, data, **kwargs):
        saved.update(copy.deepcopy(data))

    ns = {
        'asyncio': asyncio,
        'copy': copy,
        'hashlib': hashlib,
        'time': time,
        'uuid4': uuid4,
        'JSONCodec': json,
        'json': json,
        'HTTPException': HTTPException,
        'StreamingResponse': StreamingResponse,
        'is_saved_chat_id': is_saved_chat_id,
        'UserModel': type('UserModel', (), {}),
        'FilterContext': lambda: None,
        'log': logging.getLogger(__name__),
        'ENABLE_FUNCTIONS': False,
        'ENABLE_RESPONSES_API_STATEFUL': False,
        'ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND': False,
        'ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION': False,
        'CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE': 1,
        'CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS': 1,
        'RESPONSE_COMPLETION_RESPONSE_FIELDS': ('error', 'id', 'output', 'usage'),
        'get_system_oauth_token': AsyncMock(return_value=None),
        'get_tool_citation_source_ids': lambda *args: {},
        'save_response_stream': AsyncMock(),
        'clear_response_stream': AsyncMock(),
        'publish_chat_finished_event': AsyncMock(),
        'outlet_filter_handler': AsyncMock(),
        'background_tasks_handler': AsyncMock(),
        'execute_tool_call': AsyncMock(return_value=({}, 'Tool result', {}, 'builtin', False)),
        'stage_ask_user_tool_calls': lambda *args: (False, None),
        'build_terminal_file_tool_result': lambda *args: None,
        'process_tool_result': AsyncMock(return_value=('Tool result', [], [])),
        'terminal_event_handler': AsyncMock(),
        'attach_tool_citations': AsyncMock(),
        'build_tool_result_parts': build_tool_result_parts,
        'get_reasoning_format': lambda model: None,
        'convert_url_images_to_base64': AsyncMock(side_effect=lambda data, **kwargs: data),
        'normalize_messages_for_model': lambda data: data,
        'merge_usage': lambda current, incoming: {**(current or {}), **incoming},
        'Chats': SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(return_value=None),
            get_chat_title_by_id=AsyncMock(return_value='Synthetic chat'),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
    }
    load_functions(
        ns,
        'utils/misc.py',
        {
            'get_output_text',
            'get_reasoning_details',
            'convert_output_to_messages',
            'reconcile_tool_pairs',
            'get_paired_tool_call_ids',
            'get_last_user_message_item',
        },
    )
    load_functions(
        ns,
        'utils/middleware.py',
        {
            'streaming_chat_response_handler',
            'output_id',
            'append_to_text_field',
            'get_response_completion_event_data',
            'handle_responses_streaming_event',
            '_deduplicate_tool_calls',
            '_split_tool_calls',
            'tool_result_content',
            '_is_tool_result_error',
        },
    )

    def response_for(chunks):
        async def body():
            for chunk in chunks:
                yield chunk if isinstance(chunk, str) else f'data: {json.dumps(chunk)}\n\n'

        return StreamingResponse(body())

    async def run(chunks, *, temporary=False, follow_up=None, previous=None):
        ns['generate_chat_completion'] = (
            AsyncMock(side_effect=follow_up)
            if isinstance(follow_up, Exception)
            else AsyncMock(return_value=response_for(follow_up or []))
        )
        if previous:
            saved.update(copy.deepcopy(previous))
            ns['Chats'].get_message_by_id_and_message_id.return_value = copy.deepcopy(previous)
        ctx = {
            'request': SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(redis=None))),
            'form_data': {'model': 'synthetic-provider', 'messages': [{'role': 'user', 'content': 'Test'}]},
            'user': SimpleNamespace(id='user', role='admin'),
            'model': {'id': 'synthetic-provider'},
            'metadata': {
                'chat_id': 'local:test' if temporary else 'saved-chat',
                'message_id': 'reply',
                'params': {'reasoning_tags': False},
            },
            'events': [],
            'event_emitter': emit,
            'event_caller': None,
        }
        if previous:
            ctx['metadata']['assistant_message_id'] = 'reply'
            ctx['metadata']['tool_approval_resume'] = 'model'
            ctx['assistant_message'] = copy.deepcopy(previous)
        await ns['streaming_chat_response_handler'](response_for(chunks), ctx)
        return ctx

    return SimpleNamespace(run=run, ns=ns, events=events, saved=saved)


@pytest.mark.asyncio
@pytest.mark.parametrize('error_frame', [{'error': ERROR}, json.dumps({'error': ERROR})])
@pytest.mark.parametrize('temporary', [False, True])
async def test_recovered_chat_stream_never_publishes_or_saves_a_terminal_error(runtime, error_frame, temporary):
    await runtime.run(
        [error_frame, chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')], temporary=temporary
    )
    assert not any(event['data'].get('error') for event in runtime.events)
    assert not runtime.saved.get('error')
    final = runtime.events[-1]['data']
    assert final['done'] is True
    assert runtime.ns['get_output_text'](final['output']) == 'Recovered answer'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_frame',
    [
        {'type': 'error', 'message': 'Upstream request failed', 'code': 'upstream_error'},
        {'type': 'error', 'error': ERROR},
        {'type': 'response.failed', 'response': {'error': ERROR}},
    ],
)
async def test_recovered_responses_stream_does_not_leave_a_message_error(runtime, error_frame):
    await runtime.run(
        [error_frame, {'type': 'response.completed', 'response': {'status': 'completed', 'output': response_output()}}]
    )
    # Shared socket projection also reads raw response events, not just chat:completion.
    assert not any(
        event['data'].get('error') or event['data'].get('response', {}).get('error') for event in runtime.events
    )
    assert not runtime.saved.get('error')
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Recovered answer'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'tail',
    [
        [],
        [chat_chunk('partial after error')],
        ['data: [DONE]\n\n'],
        [{'choices': [{'delta': {'role': 'assistant'}}]}],
        [{'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 1}}],
        [chat_chunk(finish_reason='error')],
        [{'type': 'response.incomplete', 'response': {'status': 'incomplete'}}],
    ],
)
@pytest.mark.parametrize(
    'error_frame',
    [
        {'error': ERROR},
        json.dumps({'error': ERROR}),
        {'type': 'response.failed', 'response': {'error': ERROR}},
    ],
)
async def test_unrecovered_stream_error_is_kept_even_with_partial_content_or_done(runtime, tail, error_frame):
    await runtime.run([chat_chunk('partial before error'), error_frame, *tail])
    assert runtime.saved['error'] == {'content': ERROR}
    assert any(event['data'].get('error') for event in runtime.events)
    assert runtime.saved['done'] is True
    assert 'partial before error' in runtime.ns['get_output_text'](runtime.saved['output'])


@pytest.mark.asyncio
async def test_error_after_successful_provider_finish_is_not_cleared_by_outer_completion(runtime):
    await runtime.run([chat_chunk('Partial answer', 'stop'), {'error': ERROR}, 'data: [DONE]\n\n'])
    assert runtime.saved['error'] == {'content': ERROR}
    assert runtime.events[-1]['data']['done'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('temporary', [False, True])
@pytest.mark.parametrize(
    'follow_up',
    [
        [chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')],
        [{'type': 'response.completed', 'response': {'status': 'completed', 'output': response_output()}}],
    ],
)
async def test_successful_tool_followup_clears_reported_error_without_repeating_tool(runtime, follow_up, temporary):
    await runtime.run([tool_call_chunk(), {'error': ERROR}], follow_up=follow_up, temporary=temporary)
    runtime.ns['execute_tool_call'].assert_awaited_once()
    runtime.ns['generate_chat_completion'].assert_awaited_once()
    assert runtime.saved.get('error') is None
    final = runtime.events[-1]['data']
    assert runtime.ns['get_output_text'](final['output']) == 'Recovered answer'
    assert [item['call_id'] for item in final['output'] if item['type'] == 'function_call_output'] == ['call-1']
    if not temporary:
        assert runtime.saved['output'] == final['output']
    error_updates = [event['data'] for event in runtime.events if 'error' in event['data']]
    assert error_updates[-1] == {'error': None, 'done': False}


@pytest.mark.asyncio
async def test_tool_followup_exception_is_not_cleared_by_final_output(runtime):
    await runtime.run([tool_call_chunk(), {'error': ERROR}], follow_up=HTTPException(502, 'Continuation failed'))
    assert runtime.saved['error'] == {'content': 'Continuation failed'}
    assert runtime.events[-1]['data']['done'] is True
    runtime.ns['execute_tool_call'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('temporary', [False, True])
async def test_successful_approval_resume_clears_error_saved_by_previous_stream(runtime, temporary):
    await runtime.run(
        [chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')],
        previous={'output': response_output('Earlier partial answer'), 'error': {'content': ERROR}},
        temporary=temporary,
    )
    if not temporary:
        assert runtime.saved.get('error') is None
    error_updates = [event['data'] for event in runtime.events if 'error' in event['data']]
    assert error_updates == [{'error': None, 'done': False}]
    assert 'Earlier partial answer' in runtime.ns['get_output_text'](runtime.events[-1]['data']['output'])
    runtime.ns['execute_tool_call'].assert_not_awaited()
