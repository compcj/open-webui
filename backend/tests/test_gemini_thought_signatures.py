"""Gemini OpenAI-compatible sidecars survive the actual stream and replay paths."""

import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_streaming_error_recovery import chat_chunk, runtime as stream_runtime
from test_streaming_tool_call_dedup import runtime as calls_runtime
from test_web_tool_citations import load_functions
from test_tool_citation_replay import pipeline, citation_runtime, output_runtime, install_approval_store


SIGNATURE = {'google': {'thought_signature': 'synthetic-opaque-signature'}}


def tool_delta(*, signature=False, arguments='{}', name='test_tool', call_id='call-1'):
    return {
        'choices': [
            {
                'delta': {
                    'tool_calls': [
                        {
                            'index': 0,
                            'id': call_id,
                            'type': 'function',
                            'function': {'name': name, 'arguments': arguments},
                            **({'extra_content': copy.deepcopy(SIGNATURE)} if signature else {}),
                        }
                    ]
                },
                'finish_reason': None,
            }
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize('indices', [({}, {'index': None}), ({'index': 0}, {'index': 0})], ids=['absent', 'repeated'])
async def test_parallel_tool_calls_execute_and_finish_after_stop(stream_runtime, grouped, indices):
    calls = [
        {
            **indices[0],
            'id': 'call-a',
            'type': 'function',
            'function': {'name': 'search_web', 'arguments': '{"query":"first query"}'},
            'extra_content': SIGNATURE,
        },
        {
            **indices[1],
            'id': 'call-b',
            'type': 'function',
            'function': {'name': 'search_web', 'arguments': '{"query":"second query"}'},
        },
    ]
    batches = [calls] if grouped else [[call] for call in calls]
    chunks = [{'choices': [{'index': 0, 'delta': {'role': 'assistant', 'tool_calls': batch}}]} for batch in batches]
    queries = []

    async def search_web(query):
        queries.append(query)
        return query + ' result'

    def setup(ctx, ns, response_for):
        load_functions(ns, 'utils/middleware.py', {'execute_tool_call'})
        load_functions(ns, 'utils/tools.py', {'get_updated_tool_function'})
        ctx['metadata']['tools'] = {
            'search_web': {
                'type': 'builtin',
                'tool_id': 'builtin:search_web',
                'callable': search_web,
                'spec': {'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}}}},
            }
        }
        ns['process_tool_result'].side_effect = lambda request, name, result, *args: (result, [], [])

    await stream_runtime.run(
        [*chunks, chat_chunk(finish_reason='stop'), 'data: [DONE]\n\n'],
        follow_up=[chat_chunk('Finished', 'stop'), 'data: [DONE]\n\n'],
        setup=setup,
    )

    assert queries == ['first query', 'second query']
    stream_runtime.ns['generate_chat_completion'].assert_awaited_once()
    messages = stream_runtime.ns['generate_chat_completion'].await_args.args[1]['messages']
    assert [call for msg in messages for call in msg.get('tool_calls', [])] == [
        {key: value for key, value in call.items() if key != 'index'} for call in calls
    ]
    assert [(msg['tool_call_id'], msg['content']) for msg in messages if msg['role'] == 'tool'] == [
        ('call-a', 'first query result'),
        ('call-b', 'second query result'),
    ]
    assert stream_runtime.saved['done'] is True
    assert stream_runtime.ns['get_output_text'](stream_runtime.saved['output']) == 'Finished'


@pytest.mark.asyncio
async def test_repeated_indexless_declaration_keeps_one_signed_execution(calls_runtime):
    ns = calls_runtime.ns
    ns['new_tool_round'] = True
    ns['prior_output'] = [{'type': 'function_call_output', 'call_id': 'reused', 'status': 'completed'}]
    call = {'id': 'reused', 'function': {'name': 'write_record', 'arguments': '{"x":1}'}}
    await ns['ingest_delta']([call, {**call, 'extra_content': SIGNATURE}])
    await ns['ingest_delta']([{'id': 'reused', 'function': {'name': 'write_record', 'arguments': '{"x":2}'}}])
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    executed = ns['execute_tool_call'].await_args.args[-1]
    assert executed['id'] != 'reused'
    assert json.loads(executed['function']['arguments']) == {'x': 1}
    assert ns['output'][0]['extra_content'] == SIGNATURE


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 2])
async def test_anonymous_fragment_needs_an_unambiguous_pending_call(calls_runtime, count):
    ns = calls_runtime.ns
    await ns['ingest_delta']([{'id': f'call-{idx}', 'function': {'name': 'write_record'}} for idx in range(count)])
    await ns['ingest_delta']([{'function': {'arguments': '{"x":1}'}}])
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    if count == 1:
        ns['execute_tool_call'].assert_awaited_once()
        assert json.loads(ns['execute_tool_call'].await_args.args[-1]['function']['arguments']) == {'x': 1}
    else:
        ns['execute_tool_call'].assert_not_awaited()
        assert {item['call_id']: item['status'] for item in ns['output'] if item['type'] == 'function_call'} == {
            'call-0': 'failed',
            'call-1': 'failed',
        }


@pytest.mark.asyncio
@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
async def test_tool_signature_survives_stream_save_reload_and_followup(stream_runtime, late, temporary):
    chunks = [tool_delta(signature=not late, arguments='{')]
    chunks.append(tool_delta(signature=late, arguments='}', name=''))
    chunks.extend([chat_chunk(finish_reason='tool_calls'), 'data: [DONE]\n\n'])
    original = copy.deepcopy(chunks)
    await stream_runtime.run(chunks, temporary=temporary, follow_up=[chat_chunk('Done', 'stop'), 'data: [DONE]\n\n'])
    assert stream_runtime.saved.get('error') is None
    sent = stream_runtime.ns['generate_chat_completion'].await_args.args[1]
    call = next(call for message in sent['messages'] for call in message.get('tool_calls', []))
    assert call['extra_content'] == SIGNATURE
    output = next(e['data']['output'] for e in reversed(stream_runtime.events) if e.get('data', {}).get('output'))
    stored = json.loads(json.dumps(stream_runtime.saved.get('output', output)))
    replay = stream_runtime.ns['convert_output_to_messages'](stored, raw=True)
    assert next(call for message in replay for call in message.get('tool_calls', []))['extra_content'] == SIGNATURE
    assert next(item for item in stored if item.get('type') == 'function_call')['extra_content'] == SIGNATURE
    assert original[0]['choices'][0]['delta']['tool_calls'][0].get('extra_content') == (None if late else SIGNATURE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'content',
    ['A final answer', '  Exact signed text\n', '', '<tool_call>{"name":"test_tool","arguments":{}}</tool_call>'],
)
async def test_final_message_signature_can_arrive_in_an_empty_delta(stream_runtime, content):
    def setup(ctx, ns, response_for):
        ctx['form_data']['tools'] = [{'type': 'function', 'function': {'name': 'test_tool'}}]

    await stream_runtime.run(
        [
            chat_chunk(content),
            {
                'choices': [
                    {'delta': {'content': '', 'extra_content': copy.deepcopy(SIGNATURE)}, 'finish_reason': 'stop'}
                ]
            },
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    assert stream_runtime.saved.get('error') is None
    stored = json.loads(json.dumps(stream_runtime.saved['output']))
    replay = stream_runtime.ns['convert_output_to_messages'](stored, raw=True)
    assert replay == [{'role': 'assistant', 'content': content, 'extra_content': SIGNATURE}]
    stream_runtime.ns['execute_tool_call'].assert_not_awaited()


@pytest.mark.asyncio
async def test_signature_and_existing_reasoning_details_coexist(stream_runtime):
    details = [{'type': 'reasoning.encrypted', 'data': 'synthetic-encrypted-details', 'format': 'test'}]

    def setup(ctx, ns, response_for):
        load_functions(
            ns, 'utils/middleware.py', {'merge_streamed_reasoning_details', 'append_native_streamed_reasoning'}
        )

    delta = tool_delta(signature=True)
    delta['choices'][0]['delta']['reasoning_details'] = copy.deepcopy(details)
    await stream_runtime.run(
        [delta, chat_chunk(finish_reason='tool_calls'), 'data: [DONE]\n\n'],
        follow_up=[chat_chunk('Done', 'stop'), 'data: [DONE]\n\n'],
        setup=setup,
    )
    assert stream_runtime.saved.get('error') is None
    sent = stream_runtime.ns['generate_chat_completion'].await_args.args[1]
    assistant = next(message for message in sent['messages'] if message.get('tool_calls'))
    assert assistant['reasoning_details'] == details
    assert assistant['tool_calls'][0]['extra_content'] == SIGNATURE


@pytest.mark.asyncio
async def test_batch_dispatch_replays_signature_on_the_original_wrapper(calls_runtime):
    ns = calls_runtime.ns
    ns['metadata']['tools']['call_tool'] = {'type': 'builtin', 'tool_id': 'builtin:call_tool'}
    arguments = json.dumps(
        {
            'calls': [
                {'tool_name': 'write_record', 'params': '{"record":1}'},
                {'tool_name': 'write_record', 'params': '{"record":2}'},
            ]
        }
    )
    await ns['ingest_delta'](
        [
            {
                'index': 0,
                'id': 'batch',
                'type': 'function',
                'function': {'name': 'call_tool', 'arguments': arguments},
                'extra_content': copy.deepcopy(SIGNATURE),
            }
        ]
    )
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    for call in ns['execute_tool_call'].call_args_list:
        ns['output'].append({'type': 'function_call_output', 'call_id': call.args[-1]['id'], 'output': 'saved result'})
    load_functions(
        ns, 'utils/misc.py', {'convert_output_to_messages', 'reconcile_tool_pairs', 'get_paired_tool_call_ids'}
    )
    replay = ns['convert_output_to_messages'](json.loads(json.dumps(ns['output'])), raw=True)
    calls = [call for message in replay for call in message.get('tool_calls', [])]
    assert len(calls) == 1
    assert calls[0]['function'] == {'name': 'call_tool', 'arguments': arguments}
    assert calls[0]['extra_content'] == SIGNATURE


def test_model_switch_strips_both_provider_replay_formats(stream_runtime):
    load_functions(stream_runtime.ns, 'utils/middleware.py', {'strip_reasoning_details'})
    output = [
        {
            'type': 'message',
            'content': [{'type': 'output_text', 'text': 'Answer'}],
            'extra_content': copy.deepcopy(SIGNATURE),
            'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}],
        }
    ]
    original = copy.deepcopy(output)
    stripped = stream_runtime.ns['strip_reasoning_details'](output)
    assert 'extra_content' not in stripped[0]
    assert 'reasoning_details' not in stripped[0]
    assert output == original


@pytest.mark.asyncio
@pytest.mark.parametrize('indexed', [True, False])
async def test_interleaved_tool_signatures_arrive_without_function_deltas(calls_runtime, indexed):
    ns = calls_runtime.ns
    identities = [{'index': idx} if indexed else {'id': f'call-{idx}'} for idx in range(2)]
    calls = [
        {**identity, 'id': f'call-{idx}', 'function': {'name': 'write_record', 'arguments': '{"x":'}}
        for idx, identity in enumerate(identities)
    ]
    await ns['ingest_delta'](calls)
    for index in [1, 0]:
        identity = identities[index]
        await ns['ingest_delta']([{**identity, 'function': {'arguments': f'{index}}}'}}])
        extra = {'google': {'thought_signature': f' opaque-{index}=\n'}}
        await ns['ingest_delta']([{**identity, 'extra_content': extra}])
        await ns['ingest_delta']([{**identity, 'extra_content': copy.deepcopy(extra)}])
        await ns['ingest_delta']([{**identity, 'extra_content': {'google': {'thought_signature': ''}}}])
    events = [entry.args[0] for entry in ns['emit_response_completion_event'].await_args_list]
    assert [(e['item_id'], e['delta']) for e in events if e['type'] == 'response.function_call_arguments.delta'] == [
        ('call-0', '{"x":'),
        ('call-1', '{"x":'),
        ('call-1', '1}'),
        ('call-0', '0}'),
    ]
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    for index, item in enumerate(ns['output']):
        assert item['extra_content'] == {'google': {'thought_signature': f' opaque-{index}=\n'}}
        assert json.loads(item['arguments']) == {'x': index}
    assert ns['execute_tool_call'].await_count == 2
    ns['output'].extend(
        [
            {
                'type': 'function_call_output',
                'call_id': f'call-{index}',
                'output': [{'type': 'input_text', 'text': 'Result'}],
            }
            for index in range(2)
        ]
    )
    load_functions(
        ns, 'utils/misc.py', {'convert_output_to_messages', 'reconcile_tool_pairs', 'get_paired_tool_call_ids'}
    )
    replay = ns['convert_output_to_messages'](json.loads(json.dumps(ns['output'])), raw=True)
    assert [call['function']['arguments'] for call in replay[0]['tool_calls']] == ['{"x":0}', '{"x":1}']


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('previous_text', ['First ', ''])
async def test_signed_continue_keeps_independent_messages(stream_runtime, stream, previous_text):
    previous = {
        'output': [
            {
                'type': 'message',
                'role': 'assistant',
                'status': 'in_progress',
                'content': [{'type': 'output_text', 'text': previous_text}],
                'extra_content': {'google': {'thought_signature': 'signature-1'}},
            }
        ]
    }
    if stream:
        await stream_runtime.run(
            [
                chat_chunk(' second '),
                {'choices': [{'delta': {'extra_content': copy.deepcopy(SIGNATURE)}, 'finish_reason': 'stop'}]},
                'data: [DONE]\n\n',
            ],
            previous=previous,
        )
    else:
        ns = stream_runtime.ns
        ns.update(
            get_response_data=lambda value: (value, value),
            merge_events_into_response=lambda value, events: value,
            merge_usage=lambda old, new: new,
            build_response_object=lambda response, data: data,
            publish_chat_failed_event=AsyncMock(),
        )
        load_functions(ns, 'utils/middleware.py', {'non_streaming_chat_response_handler'})
        ctx = {
            'request': SimpleNamespace(state=SimpleNamespace()),
            'form_data': {'model': 'test'},
            'user': SimpleNamespace(),
            'events': [],
            'metadata': {'chat_id': 'saved-chat', 'message_id': 'reply', 'assistant_message_id': 'reply'},
            'assistant_message': previous,
            'event_emitter': AsyncMock(),
        }
        await ns['non_streaming_chat_response_handler'](
            {
                'choices': [
                    {
                        'message': {'role': 'assistant', 'content': ' second ', 'extra_content': SIGNATURE},
                        'finish_reason': 'stop',
                    }
                ],
            },
            ctx,
        )
    stored = stream_runtime.saved['output']
    replay = stream_runtime.ns['convert_output_to_messages'](stored, raw=True)
    assert replay == [
        {
            'role': 'assistant',
            'content': previous_text,
            'extra_content': {'google': {'thought_signature': 'signature-1'}},
        },
        {'role': 'assistant', 'content': ' second ', 'extra_content': SIGNATURE},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['synthetic', 'other-model'])
async def test_approval_persists_signature_and_replays_only_to_same_model(pipeline, target):
    ns = pipeline.ns
    message = {
        'role': 'assistant',
        'model': 'synthetic',
        'output': [
            {
                'type': 'function_call',
                'call_id': 'approved',
                'name': 'f',
                'arguments': '{}',
                'status': 'queued',
                'approved': True,
                'extra_content': copy.deepcopy(SIGNATURE),
            }
        ],
    }
    ns.update(
        Chats=SimpleNamespace(),
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(return_value={'tool_call_id': 'approved', 'content': 'Result'}),
    )
    install_approval_store(ns, message)
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'assistant'}
    await ns['resume_tool_calls'](None, ns['form_data'], ns['user'], {'id': target}, metadata, message)
    ns['execute_tool_call_for_output'].assert_awaited_once()
    stored = json.loads(json.dumps(message))
    assert stored['output'][0]['extra_content'] == SIGNATURE
    form = copy.deepcopy(ns['form_data'])
    await ns['project_approved_response'](None, form, ns['user'], {'id': target}, metadata, stored)
    call = next(call for msg in form['messages'] for call in msg.get('tool_calls', []))
    assert call.get('extra_content') == (SIGNATURE if target == 'synthetic' else None)
    assert stored['output'][0]['extra_content'] == SIGNATURE


def test_signature_changes_v2_budget_and_wire_fingerprint(monkeypatch):
    from open_webui.utils import context_compaction_v2 as v2

    monkeypatch.setattr(v2, 'token_encoder', lambda: None)
    plain = {'role': 'assistant', 'content': 'Answer'}
    signed = {**plain, 'extra_content': {'google': {'thought_signature': 'opaque' * 1000}}}
    assert v2.estimate_message(signed) > v2.estimate_message(plain) + 1000
    assert v2.digest(v2.wire_message(signed)) != v2.digest(v2.wire_message(plain))


@pytest.mark.asyncio
async def test_message_signature_covers_text_on_both_sides_of_a_tool_call(stream_runtime):
    await stream_runtime.run(
        [
            chat_chunk('Before '),
            tool_delta(signature=True),
            chat_chunk(' after'),
            {'choices': [{'delta': {'extra_content': SIGNATURE}, 'finish_reason': 'tool_calls'}]},
            'data: [DONE]\n\n',
        ],
        follow_up=[chat_chunk('Done', 'stop'), 'data: [DONE]\n\n'],
    )
    sent = stream_runtime.ns['generate_chat_completion'].await_args.args[1]
    assistant = next(message for message in sent['messages'] if message.get('tool_calls'))
    assert assistant['content'] == 'Before  after'
    assert assistant['extra_content'] == SIGNATURE
    assert assistant['tool_calls'][0]['extra_content'] == SIGNATURE
    assert any(message.get('role') == 'tool' for message in sent['messages'])


@pytest.mark.asyncio
async def test_late_signature_preserves_text_before_tag_parsing(stream_runtime):
    text = '  <think>display thought</think>\nAnswer  '

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['reasoning_tags'] = ['<think>', '</think>']
        ns['DEFAULT_SOLUTION_TAGS'] = []
        ns['re'] = re
        load_functions(ns, 'utils/middleware.py', {'_start_tag_pattern'})

    await stream_runtime.run(
        [
            chat_chunk(text),
            {'choices': [{'delta': {'extra_content': SIGNATURE}, 'finish_reason': 'stop'}]},
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    replay = stream_runtime.ns['convert_output_to_messages'](stream_runtime.saved['output'], raw=True)
    assert replay == [{'role': 'assistant', 'content': text, 'extra_content': SIGNATURE}]


@pytest.mark.asyncio
async def test_request_filter_switch_strips_old_signatures_but_keeps_new_model_rounds(stream_runtime):
    second = tool_delta(signature=True, arguments='{"round":2}', call_id='call-2')
    second['choices'][0]['delta']['tool_calls'][0]['extra_content']['google']['thought_signature'] = 'signature-b'

    async def switch(**kwargs):
        return {**kwargs['form_data'], 'model': 'model-b'}, {}

    def setup(ctx, ns, response_for):
        ns.update(
            ENABLE_FUNCTIONS=True,
            CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS=5,
            get_filter_functions=AsyncMock(return_value=['filter']),
            process_filter_functions=switch,
            generate_chat_completion=AsyncMock(
                side_effect=[
                    response_for([second, chat_chunk(finish_reason='tool_calls'), 'data: [DONE]\n\n']),
                    response_for([chat_chunk('Done', 'stop'), 'data: [DONE]\n\n']),
                ]
            ),
        )

    await stream_runtime.run(
        [
            tool_delta(signature=True),
            chat_chunk(finish_reason='tool_calls'),
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    sends = stream_runtime.ns['generate_chat_completion'].await_args_list
    assert len(sends) == 2
    first_calls = [call for msg in sends[0].args[1]['messages'] for call in msg.get('tool_calls', [])]
    assert all('extra_content' not in call for call in first_calls)
    later_calls = [call for msg in sends[1].args[1]['messages'] for call in msg.get('tool_calls', [])]
    assert 'extra_content' not in later_calls[0]
    assert later_calls[1]['extra_content']['google']['thought_signature'] == 'signature-b'
    assert (
        next(item for item in stream_runtime.saved['output'] if item.get('call_id') == 'call-1')['extra_content']
        == SIGNATURE
    )


@pytest.mark.asyncio
async def test_signed_code_followup_preserves_prior_output_without_reexecution(stream_runtime):
    execute_code = AsyncMock(return_value={'stdout': 'Result', 'stderr': '', 'result': ''})

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['function_calling'] = 'legacy'
        ctx['metadata']['features'] = {'code_interpreter': True}
        ctx['event_caller'] = execute_code
        ns.update(
            Config=SimpleNamespace(
                get=AsyncMock(
                    side_effect=lambda key: {
                        'code_interpreter.enable': True,
                        'code_interpreter.engine': 'pyodide',
                    }[key]
                )
            ),
            sanitize_code=lambda code: code,
            CODE_INTERPRETER_BLOCKED_MODULES=[],
            DEFAULT_CODE_INTERPRETER_TAGS=[('<code_interpreter>', '</code_interpreter>')],
            re=re,
        )
        load_functions(ns, 'utils/middleware.py', {'_start_tag_pattern'})

    await stream_runtime.run(
        [
            chat_chunk('Earlier text <code_interpreter type="code">print("fixture")</code_interpreter>'),
            'data: [DONE]\n\n',
        ],
        follow_up=[
            chat_chunk('Final answer'),
            {'choices': [{'delta': {'extra_content': SIGNATURE}, 'finish_reason': 'stop'}]},
            'data: [DONE]\n\n',
        ],
        setup=setup,
    )
    execute_code.assert_awaited_once()
    stream_runtime.ns['generate_chat_completion'].assert_awaited_once()
    stored = stream_runtime.saved['output']
    assert stored[0]['content'][0]['text'] == 'Earlier text '
    assert stored[-1]['content'][0]['text'] == 'Final answer'
    assert stored[-1]['extra_content'] == SIGNATURE
