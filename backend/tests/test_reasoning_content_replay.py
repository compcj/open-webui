"""Exact upstream reasoning survives saving and tool replay without fabricating fields."""

import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_streaming_error_recovery import runtime, chat_chunk, tool_call_chunk
from test_tool_citation_replay import pipeline, citation_runtime, output_runtime, install_approval_store
from test_web_tool_citations import load_functions


@pytest.fixture
def replay(runtime):
    load_functions(
        runtime.ns,
        'utils/middleware.py',
        {
            'get_reasoning_format',
            'strip_reasoning_details',
            'process_messages_with_output',
            'update_assistant_message_from_stream',
            'merge_streamed_reasoning_details',
        },
    )
    return runtime


@pytest.mark.parametrize('enabled', [None, True, False])
@pytest.mark.parametrize('reason', ['  exact\nreasoning  ', ''])
@pytest.mark.asyncio
async def test_stream_preserves_actual_field_and_respects_model_switch(replay, enabled, reason):
    def setup(ctx, ns, response_for):
        if enabled is not None:
            ctx['model']['info'] = {'meta': {'reasoning_content_replay': enabled}}
        ctx['form_data']['tools'] = [{'type': 'function', 'function': {'name': 'test_tool'}}]

    ctx = await replay.run(
        [{'choices': [{'delta': {'reasoning_content': reason}}]}, tool_call_chunk()],
        follow_up=[chat_chunk('Answer', 'stop'), 'data: [DONE]\n\n'],
        setup=setup,
    )
    stored = json.loads(json.dumps(replay.saved['output']))
    reasoning = next(item for item in stored if item['type'] == 'reasoning')
    assert reasoning['reasoning_content'] == reason
    sent = replay.ns['generate_chat_completion'].await_args.args[1]['messages']
    call = next(message for message in sent if message.get('tool_calls'))
    if enabled is False:
        assert 'reasoning_content' not in call
    else:
        assert call['reasoning_content'] == reason
    history = replay.ns['process_messages_with_output'](
        [{'role': 'assistant', 'output': stored}],
        reasoning_format=replay.ns['get_reasoning_format'](ctx['model']),
    )
    assert next(message for message in history if message.get('tool_calls')) == call


@pytest.mark.asyncio
async def test_reasoning_alias_and_display_tags_do_not_invent_provider_field(replay):
    await replay.run(
        [{'choices': [{'delta': {'reasoning': 'Displayed alias'}}]}, tool_call_chunk()],
        follow_up=[chat_chunk('Answer', 'stop')],
    )
    sent = replay.ns['generate_chat_completion'].await_args.args[1]['messages']
    assert all('reasoning_content' not in message for message in sent)
    output = [
        {'type': 'reasoning', 'content': [{'type': 'output_text', 'text': 'Parsed tags'}]},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Answer'}]},
    ]
    assert replay.ns['convert_output_to_messages'](
        output, raw=True, reasoning_format=replay.ns['get_reasoning_format']({})
    ) == [{'role': 'assistant', 'content': 'Answer'}]


@pytest.mark.parametrize('enabled', [True, False])
def test_raw_field_replay_keeps_details_independent_and_drops_foreign_model_data(replay, enabled):
    details = [{'type': 'reasoning.encrypted', 'format': 'synthetic', 'data': 'opaque'}]
    output = [
        {
            'type': 'reasoning',
            'reasoning_content': ' exact ',
            'reasoning_details': details,
            'content': [{'type': 'output_text', 'text': 'display may differ'}],
        },
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Answer'}]},
    ]
    fmt = replay.ns['get_reasoning_format']({'info': {'meta': {'reasoning_content_replay': enabled}}})
    message = replay.ns['convert_output_to_messages'](output, raw=True, reasoning_format=fmt)[0]
    assert message['reasoning_details'] == details
    assert message.get('reasoning_content') == (' exact ' if enabled else None)
    stripped = replay.ns['strip_reasoning_details'](output)
    assert 'reasoning_content' not in stripped[0]
    assert 'reasoning_details' not in stripped[0]
    assert output[0]['reasoning_content'] == ' exact '


@pytest.mark.parametrize('reason', ['  exact\nvalue  ', ''])
@pytest.mark.asyncio
async def test_nonstream_persists_exact_field(replay, reason):
    ns = replay.ns
    ns.update(
        get_response_data=lambda value: (value, value),
        merge_events_into_response=lambda value, events: value,
        build_response_object=lambda response, data: data,
        publish_chat_failed_event=AsyncMock(),
    )
    load_functions(ns, 'utils/middleware.py', {'non_streaming_chat_response_handler'})
    ctx = {
        'request': SimpleNamespace(state=SimpleNamespace()),
        'form_data': {'model': 'test'},
        'user': SimpleNamespace(),
        'events': [],
        'metadata': {'chat_id': 'saved-chat', 'message_id': 'reply'},
        'event_emitter': AsyncMock(),
    }
    await ns['non_streaming_chat_response_handler'](
        {
            'choices': [
                {
                    'message': {'role': 'assistant', 'content': 'Answer', 'reasoning_content': reason},
                    'finish_reason': 'stop',
                }
            ]
        },
        ctx,
    )
    output = replay.saved['output']
    assert next(item for item in output if item['type'] == 'reasoning')['reasoning_content'] == reason
    message = ns['convert_output_to_messages'](output, raw=True, reasoning_format=ns['get_reasoning_format']({}))[0]
    assert message['reasoning_content'] == reason


def test_background_stream_projection_preserves_exact_chunks(replay):
    message = {}
    for part in [' ', 'exact', '\n']:
        replay.ns['update_assistant_message_from_stream'](
            message, 'data: ' + json.dumps({'choices': [{'delta': {'reasoning_content': part}}]})
        )
    assert message['output'][0]['reasoning_content'] == ' exact\n'


@pytest.mark.asyncio
async def test_routed_model_controls_its_own_tool_reasoning_replay(replay):
    def setup(ctx, ns, response_for):
        ctx['model']['info'] = {'meta': {'reasoning_content_replay': False}}
        ctx['form_data']['model'] = 'routed'
        ctx['request'].app.state.MODELS = {
            'routed': {'id': 'routed', 'info': {'meta': {'reasoning_content_replay': True}}}
        }

    await replay.run(
        [{'choices': [{'delta': {'reasoning_content': 'routed thought'}}]}, tool_call_chunk()],
        follow_up=[chat_chunk('Done', 'stop')],
        setup=setup,
    )
    messages = replay.ns['generate_chat_completion'].await_args.args[1]['messages']
    assert next(message for message in messages if message.get('tool_calls'))['reasoning_content'] == 'routed thought'


def test_ollama_and_legacy_llama_formats_remain_independent(replay):
    assert replay.ns['get_reasoning_format']({'owned_by': 'ollama'}) == 'thinking'
    assert replay.ns['get_reasoning_format']({'provider': 'llama.cpp'}) == 'reasoning_content'
    assert (
        replay.ns['get_reasoning_format'](
            {'provider': 'llama.cpp', 'info': {'meta': {'reasoning_content_replay': False}}}
        )
        is None
    )


@pytest.mark.asyncio
async def test_code_continuation_keeps_each_responses_original_reasoning(replay):
    execute = AsyncMock(return_value={'stdout': 'Result', 'stderr': '', 'result': ''})

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['function_calling'] = 'legacy'
        ctx['metadata']['features'] = {'code_interpreter': True}
        ctx['event_caller'] = execute
        ns.update(
            Config=SimpleNamespace(
                get=AsyncMock(
                    side_effect=lambda key: {'code_interpreter.enable': True, 'code_interpreter.engine': 'pyodide'}[key]
                )
            ),
            sanitize_code=lambda code: code,
            CODE_INTERPRETER_BLOCKED_MODULES=[],
            DEFAULT_CODE_INTERPRETER_TAGS=[('<code_interpreter>', '</code_interpreter>')],
            re=re,
        )
        load_functions(ns, 'utils/middleware.py', {'_start_tag_pattern'})

    await replay.run(
        [
            {'choices': [{'delta': {'reasoning_content': 'first'}}]},
            chat_chunk('<code_interpreter type="code">print(1)</code_interpreter>'),
            'data: [DONE]\n\n',
        ],
        follow_up=[
            {'choices': [{'delta': {'reasoning_content': 'second'}}]},
            chat_chunk('Answer', 'stop'),
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    output = replay.saved['output']
    assert [item['reasoning_content'] for item in output if 'reasoning_content' in item] == ['first', 'second']
    messages = replay.ns['convert_output_to_messages'](output, raw=True, reasoning_format='provider_reasoning_content')
    assert [message['reasoning_content'] for message in messages if 'reasoning_content' in message] == [
        'first',
        'second',
    ]
    assert execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_approval_resume_preserves_raw_reasoning_without_reexecuting(pipeline, enabled):
    ns = pipeline.ns
    load_functions(ns, 'utils/middleware.py', {'get_reasoning_format'})
    model = {'id': 'synthetic', 'info': {'meta': {'reasoning_content_replay': enabled}}}
    message = {
        'role': 'assistant',
        'model': 'synthetic',
        'output': [
            {
                'type': 'reasoning',
                'reasoning_content': ' exact approved thought ',
                'content': [{'type': 'output_text', 'text': 'display'}],
            },
            {
                'type': 'function_call',
                'call_id': 'approved',
                'name': 'f',
                'arguments': '{}',
                'status': 'queued',
                'approved': True,
            },
        ],
    }
    ns.update(
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(return_value={'tool_call_id': 'approved', 'content': 'Result'}),
    )
    install_approval_store(ns, message)
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'assistant'}
    await ns['resume_tool_calls'](None, ns['form_data'], ns['user'], model, metadata, message)
    assert message['output'][0]['reasoning_content'] == ' exact approved thought '
    projected = ns['process_messages_with_output'](
        [json.loads(json.dumps(message))], reasoning_format=ns['get_reasoning_format'](model)
    )
    call = next(item for item in projected if item.get('tool_calls'))
    assert call.get('reasoning_content') == (' exact approved thought ' if enabled else None)
    await ns['resume_tool_calls'](None, ns['form_data'], ns['user'], model, metadata, message)
    ns['execute_tool_call_for_output'].assert_awaited_once()


@pytest.mark.asyncio
async def test_code_replay_does_not_resurrect_reasoning_after_model_switch(replay):
    sent = []
    execute = AsyncMock(return_value={'stdout': 'Result', 'stderr': '', 'result': ''})

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['function_calling'] = 'legacy'
        ctx['metadata']['features'] = {'code_interpreter': True}
        ctx['event_caller'] = execute
        ctx['request'].app.state.MODELS = {'routed': {'id': 'routed'}}
        ns.update(
            Config=SimpleNamespace(
                get=AsyncMock(
                    side_effect=lambda key: {'code_interpreter.enable': True, 'code_interpreter.engine': 'pyodide'}[key]
                )
            ),
            sanitize_code=lambda code: code,
            CODE_INTERPRETER_BLOCKED_MODULES=[],
            DEFAULT_CODE_INTERPRETER_TAGS=[('<code_interpreter>', '</code_interpreter>')],
            re=re,
            ENABLE_FUNCTIONS=True,
            get_filter_functions=AsyncMock(return_value=['route']),
        )
        load_functions(ns, 'utils/middleware.py', {'_start_tag_pattern'})

        async def route(**kwargs):
            body = kwargs['form_data']
            if kwargs['filter_type'] == 'request':
                body['model'] = 'routed'
            return body, {}

        async def generate(request, body, user, **kwargs):
            sent.append(copy.deepcopy(body))
            return response_for(
                [
                    {'choices': [{'delta': {'reasoning_content': 'model-B'}}]},
                    chat_chunk('<code_interpreter type="code">print(2)</code_interpreter>'),
                    'data: [DONE]\n\n',
                ]
                if len(sent) == 1
                else [chat_chunk('Answer', 'stop')]
            )

        ns.update(process_filter_functions=route, generate_chat_completion=AsyncMock(side_effect=generate))

    await replay.run(
        [
            {'choices': [{'delta': {'reasoning_content': 'model-A'}}]},
            chat_chunk('<code_interpreter type="code">print(1)</code_interpreter>'),
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    assert len(sent) == execute.await_count == 2
    assert [item['reasoning_content'] for item in replay.saved['output'] if 'reasoning_content' in item] == [
        'model-A',
        'model-B',
    ]
    assert not [message for message in sent[0]['messages'] if 'reasoning_content' in message]
    assert [message['reasoning_content'] for message in sent[1]['messages'] if 'reasoning_content' in message] == [
        'model-B'
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('late_raw', [False, True])
async def test_raw_reasoning_stays_with_its_signed_message_and_tool_call(replay, late_raw):
    signature = {'google': {'thought_signature': 'synthetic-signature'}}
    details = [{'type': 'reasoning.encrypted', 'format': 'synthetic', 'data': 'opaque'}]
    chunks = (
        [
            {'choices': [{'delta': {'reasoning_details': details}}]},
            {'choices': [{'delta': {'content': 'Signed text', 'extra_content': signature}}]},
            {'choices': [{'delta': {'reasoning_content': 'raw thought'}}]},
            tool_call_chunk(),
            'data: [DONE]\n\n',
        ]
        if late_raw
        else [
            {'choices': [{'delta': {'reasoning_content': 'raw thought'}}]},
            chat_chunk('Signed text'),
            tool_call_chunk(),
            {'choices': [{'delta': {'extra_content': signature}}]},
            'data: [DONE]\n\n',
        ]
    )
    await replay.run(chunks, follow_up=[chat_chunk('Done', 'stop')])
    messages = replay.ns['generate_chat_completion'].await_args.args[1]['messages']
    assistants = [message for message in messages if message.get('role') == 'assistant']
    assert len(assistants) == 1
    assert assistants[0]['content'] == 'Signed text'
    assert assistants[0]['reasoning_content'] == 'raw thought'
    assert assistants[0]['extra_content'] == signature
    assert assistants[0]['tool_calls']
    if late_raw:
        assert assistants[0]['reasoning_details'] == details
