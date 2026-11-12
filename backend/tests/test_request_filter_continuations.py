"""Request-filter baselines across code, Responses, compaction and approval boundaries."""

import copy
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from open_webui.utils.request_filter_context import snapshot_request_filter_input
from test_image_tool_context import install_approval_store
from test_streaming_error_recovery import chat_chunk, runtime, tool_call_chunk
from test_tool_citation_replay import citation_runtime, output_runtime, pipeline
from test_web_tool_citations import load_functions


def baseline_payload():
    return {
        'model': 'synthetic-provider',
        'messages': [
            {'role': 'system', 'content': 'Stable system'},
            {'role': 'user', 'content': 'Raw question'},
        ],
        'tools': [{'type': 'function', 'function': {'name': 'test_tool', 'description': 'Stable schema'}}],
    }


def install_filter_baseline(ctx, ns, baseline, seen, order=None):
    ctx['request'].state.request_filter_input = copy.deepcopy(baseline)
    ctx['request'].state.max_tool_call_iterations = 3
    ctx['form_data'] = copy.deepcopy(baseline)
    ctx['form_data']['messages'][0]['content'] = 'Frozen model prompt\nStable system\nFilter once'
    ctx['form_data']['tools'][0]['function']['description'] += ' filtered'

    async def request_filter(**kwargs):
        body = kwargs['form_data']
        if kwargs['filter_type'] != 'request':
            return body, {}
        if order is not None:
            order.append('filter')
        seen.append(copy.deepcopy(body))
        body['messages'][0]['content'] += '\nFilter once'
        body['tools'][0]['function']['description'] += ' filtered'
        return body, {}

    async def frozen_prompt(request, body, user):
        body['messages'][0]['content'] = 'Frozen model prompt\n' + body['messages'][0]['content']
        return body

    ns.update(
        ENABLE_FUNCTIONS=True,
        get_filter_functions=AsyncMock(return_value=['synthetic-filter']),
        process_filter_functions=request_filter,
        prepare_chat_system_prompt=frozen_prompt,
    )


def assert_clean_filter_rounds(seen, sent):
    assert seen
    for body in seen:
        assert body['messages'][0]['content'] == 'Stable system'
        assert body['tools'][0]['function']['description'] == 'Stable schema'
    for body in sent:
        assert body['messages'][0]['content'] == 'Frozen model prompt\nStable system\nFilter once'
        assert body['tools'][0]['function']['description'] == 'Stable schema filtered'


@pytest.mark.asyncio
async def test_code_interpreter_rounds_keep_raw_baseline_and_previous_code(runtime):
    baseline, seen, sent = baseline_payload(), [], []
    execute_code = AsyncMock(return_value={'stdout': 'Synthetic code result', 'stderr': '', 'result': ''})
    code_one = 'print("first code")'
    code_two = 'print("second code")'

    def setup(ctx, ns, response_for):
        install_filter_baseline(ctx, ns, baseline, seen)
        ctx['metadata']['params']['function_calling'] = 'legacy'
        ctx['metadata']['features'] = {'code_interpreter': True}
        ctx['event_caller'] = execute_code
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

        async def generate(request, body, user, **kwargs):
            sent.append(copy.deepcopy(body))
            return response_for(
                [chat_chunk(f'<code_interpreter type="code">{code_two}</code_interpreter>'), 'data: [DONE]\n\n']
                if len(sent) == 1
                else [chat_chunk('Finished', 'stop')]
            )

        ns['generate_chat_completion'] = AsyncMock(side_effect=generate)

    ctx = await runtime.run(
        [chat_chunk(f'<code_interpreter type="code">{code_one}</code_interpreter>'), 'data: [DONE]\n\n'],
        setup=setup,
    )

    assert runtime.saved.get('error') is None, runtime.saved.get('error')
    assert len(sent) == len(seen) == execute_code.await_count == 2
    assert_clean_filter_rounds(seen, sent)
    last_text = '\n'.join(message.get('content', '') for message in sent[-1]['messages'])
    assert last_text.count(code_one) == last_text.count(code_two) == 1
    assert ctx['request'].state.request_filter_input == baseline


def response_tool_call(response_id, call_id):
    return {
        'type': 'response.completed',
        'response': {
            'id': response_id,
            'status': 'completed',
            'output': [
                {
                    'type': 'function_call',
                    'id': 'item-' + call_id,
                    'call_id': call_id,
                    'name': 'test_tool',
                    'arguments': '{}',
                    'status': 'completed',
                }
            ],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['off', 'v2'])
async def test_responses_rounds_preserve_stateful_ids_or_full_v2_input(runtime, monkeypatch, mode):
    from open_webui.utils import tool_result_sources

    baseline, seen, sent, order = baseline_payload(), [], [], []
    baseline['previous_response_id'] = 'stale-input-response'
    registered = []

    async def register(request, metadata, model_id, **kwargs):
        order.append('register')
        assert runtime.saved['output'] == kwargs['output']
        registered.append(copy.deepcopy(kwargs))

    monkeypatch.setattr(tool_result_sources, 'register_tool_results', register)

    def setup(ctx, ns, response_for):
        install_filter_baseline(ctx, ns, baseline, seen, order)
        ctx['request'].state.context_compaction = {'mode': mode, 'model_id': baseline['model']}
        ns['ENABLE_RESPONSES_API_STATEFUL'] = True

        async def generate(request, body, user, **kwargs):
            order.append('send')
            sent.append(copy.deepcopy(body))
            return response_for(
                [response_tool_call('response-2', 'call-2')] if len(sent) == 1 else [chat_chunk('Finished', 'stop')]
            )

        ns['generate_chat_completion'] = AsyncMock(side_effect=generate)

    ctx = await runtime.run([response_tool_call('response-1', 'call-1')], setup=setup)

    assert runtime.saved.get('error') is None, runtime.saved.get('error')
    assert len(sent) == len(seen) == 2
    assert_clean_filter_rounds(seen, sent)
    if mode == 'off':
        assert [body['previous_response_id'] for body in sent] == ['response-1', 'response-2']
        assert [[message['role'] for message in body['messages']] for body in sent] == [
            ['system', 'tool'],
            ['system', 'tool'],
        ]
        assert [body['messages'][1]['tool_call_id'] for body in sent] == ['call-1', 'call-2']
        assert registered == []
        assert order == ['filter', 'send', 'filter', 'send']
    else:
        assert all('previous_response_id' not in body for body in sent)
        assert sent[1]['messages'][: len(sent[0]['messages'])] == sent[0]['messages']
        assert any(message.get('content') == 'Raw question' for message in sent[1]['messages'])
        assert [message['tool_call_id'] for message in sent[1]['messages'] if message['role'] == 'tool'] == [
            'call-1',
            'call-2',
        ]
        assert len(registered) == 2
        assert all(entry['output_ids'] for entry in registered)
        assert order == ['register', 'filter', 'send', 'register', 'filter', 'send']
    assert ctx['request'].state.request_filter_input == baseline


@pytest.mark.asyncio
async def test_legacy_compaction_counts_frozen_prompt_but_keeps_private_context_out_of_summary(runtime):
    baseline, seen, sent, budget_inputs = baseline_payload(), [], [], []
    private = [
        {
            'role': 'assistant',
            'tool_calls': [
                {'id': 'hidden-call', 'type': 'function', 'function': {'name': 'memory', 'arguments': '{}'}}
            ],
        },
        {'role': 'tool', 'tool_call_id': 'hidden-call', 'content': 'Frozen private context'},
    ]
    baseline['messages'][1:1] = [
        {'role': 'user', 'content': 'Old question'},
        {'role': 'assistant', 'content': 'Old answer'},
    ]
    baseline['messages'].extend(copy.deepcopy(private))

    async def compact(request, user, messages, *args, **kwargs):
        budget_inputs.append((copy.deepcopy(messages), kwargs))
        if len(budget_inputs) == 1:
            return [messages[0], *messages[-3:]], 'Frozen summary', True
        return messages, None, False

    def setup(ctx, ns, response_for):
        install_filter_baseline(ctx, ns, baseline, seen)
        ctx['request'].state.context_compaction = {'mode': 'legacy'}
        ctx['request'].state.private_tool_context = copy.deepcopy(private)
        ctx['request'].app.state.MODELS = {}
        ns['compact_messages_for_request'] = compact
        load_functions(ns, 'utils/middleware.py', {'process_messages_with_output'})
        second = tool_call_chunk()
        second['choices'][0]['delta']['tool_calls'][0]['id'] = 'call-2'

        async def generate(request, body, user, **kwargs):
            sent.append(copy.deepcopy(body))
            return response_for([second] if len(sent) == 1 else [chat_chunk('Finished', 'stop')])

        ns['generate_chat_completion'] = AsyncMock(side_effect=generate)

    ctx = await runtime.run([tool_call_chunk()], setup=setup)

    assert runtime.saved.get('error') is None, runtime.saved.get('error')
    assert len(budget_inputs) == len(sent) == len(seen) == 2
    assert_clean_filter_rounds(seen, sent)
    for messages, kwargs in budget_inputs:
        assert 'Frozen private context' not in json.dumps(messages)
        assert messages[0]['content'] == 'Stable system'
        assert kwargs['system_prompt'] == 'Frozen model prompt\nStable system\nFilter once'
        assert kwargs['bypass_system_prompt'] is True
    for body in sent:
        text = json.dumps(body['messages'])
        assert text.count('Frozen summary') == 1
        assert text.count('Frozen private context') == 1
        assert 'Old question' not in text
        assert 'Old answer' not in text
    assert ctx['request'].state.request_filter_input == baseline


@pytest.mark.asyncio
@pytest.mark.parametrize('deny', [False, True])
async def test_approval_filter_preflight_cannot_contaminate_the_next_baseline(pipeline, deny):
    ns = pipeline.ns
    form = baseline_payload()
    form['model'] = 'synthetic'
    form['provider_options'] = {'trace': ['raw']}
    original = copy.deepcopy(form)
    request = SimpleNamespace(state=SimpleNamespace())
    message = {
        'model': 'synthetic',
        'output': [
            {
                'type': 'function_call',
                'call_id': 'approved',
                'name': 'test_tool',
                'arguments': '{}',
                'status': 'queued',
                'approved': True,
            }
        ],
    }
    install_approval_store(ns, message)
    ns['user'].model_dump = lambda: {'id': 'owner'}

    async def request_filter(**kwargs):
        body = kwargs['form_data']
        body['messages'][0]['content'] += '\nApproval filter'
        body['tools'][0]['function']['description'] += ' approval-filtered'
        body['provider_options']['trace'].append('approval-filtered')
        if deny:
            raise ValueError('Approval access revoked')
        return body, {}

    ns.update(
        ENABLE_FUNCTIONS=True,
        get_filter_functions=AsyncMock(return_value=['synthetic-filter']),
        get_filter_context=lambda request: None,
        process_filter_functions=request_filter,
        get_system_oauth_token=AsyncMock(return_value=None),
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(
            return_value={'tool_call_id': 'approved', 'content': 'Error: synthetic tool refusal'}
        ),
    )
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'reply'}

    if deny:
        with pytest.raises(ValueError, match='Approval access revoked'):
            await ns['resume_tool_calls'](request, form, ns['user'], ns['model'], metadata, message)
        ns['execute_tool_call_for_output'].assert_not_awaited()
    else:
        assert await ns['resume_tool_calls'](request, form, ns['user'], ns['model'], metadata, message) is False
        ns['execute_tool_call_for_output'].assert_awaited_once()
        tool_body = ns['execute_tool_call_for_output'].await_args.args[1]
        assert tool_body['messages'][0]['content'].endswith('Approval filter')

    assert snapshot_request_filter_input(form) == original
    assert form == original
