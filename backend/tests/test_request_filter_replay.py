"""Request filters see fresh canonical input on every tool continuation."""

import ast
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_streaming_error_recovery import runtime, chat_chunk, tool_call_chunk
from test_web_tool_citations import BACKEND, load_functions


def install_initial_tail(ns):
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    process = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    # Include both existing preparation stages and the real filter/normalize tail.
    start = (
        next(
            i
            for i, node in enumerate(process.body)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == 'pending_assistant_message is not None'
            and any(isinstance(child, ast.Name) and child.id == 'resume_tool_calls' for child in ast.walk(node))
        )
        + 1
    )
    wrapper = ast.parse('async def initial_tail(form_data, metadata): pass')
    wrapper.body[0].body = process.body[start:]
    exec(compile(ast.fix_missing_locations(wrapper), 'initial_request_tail', 'exec'), ns)
    load_functions(ns, 'utils/misc.py', {'merge_system_messages', 'strip_empty_content_blocks'})
    load_functions(ns, 'utils/middleware.py', {'normalize_messages_for_model'})


@pytest.mark.asyncio
async def test_initial_filter_cannot_mutate_the_raw_baseline(runtime):
    ns = runtime.ns
    install_initial_tail(ns)
    request = SimpleNamespace(state=SimpleNamespace())
    private = [{'role': 'assistant', 'content': 'Frozen private context'}]
    seen = []

    async def filter_request(**kwargs):
        body = kwargs['form_data']
        seen.append(copy.deepcopy(body))
        body['messages'][0]['content'] += '\nFilter once'
        body['tools'][0]['function']['description'] += ' filter'
        return body, {}

    ns.update(
        request=request,
        ENABLE_FUNCTIONS=True,
        filter_functions=['synthetic'],
        filter_context=None,
        process_filter_functions=filter_request,
        extra_params={},
        private_context_output=[],
        private_prompt_context=False,
        sources=[],
        chat=None,
        model_system_prompt_template='',
        user=SimpleNamespace(id='u'),
        compaction_messages=None,
        context_summary=None,
        private_context=private,
        model={'id': 'model'},
        events=[],
    )
    body = {
        'model': 'model',
        'messages': [{'role': 'system', 'content': 'Stable'}, {'role': 'user', 'content': 'Question'}],
        'tools': [{'type': 'function', 'function': {'name': 'tool', 'description': 'Stable schema'}}],
    }
    result, _, _, _ = await ns['initial_tail'](body, {})
    baseline = request.state.request_filter_input
    assert baseline['messages'][0]['content'] == 'Stable'
    assert baseline['tools'][0]['function']['description'] == 'Stable schema'
    assert private[0] in seen[0]['messages']
    assert result['messages'][0]['content'] == 'Stable\nFilter once'


@pytest.mark.asyncio
@pytest.mark.parametrize('deny_round', [None, 2])
async def test_tool_rounds_rerun_filter_without_cumulative_changes(runtime, deny_round):
    seen, sent = [], []
    baseline = {
        'model': 'synthetic-provider',
        'messages': [{'role': 'system', 'content': 'Stable'}, {'role': 'user', 'content': 'Test'}],
        'tools': [{'type': 'function', 'function': {'name': 'test_tool', 'description': 'schema'}}],
    }

    async def apply_filter(**kwargs):
        body = kwargs['form_data']
        if kwargs['filter_type'] != 'request':
            return body, {}
        seen.append(copy.deepcopy(body))
        if len(seen) == deny_round:
            raise ValueError('Access revoked')
        body['messages'][0]['content'] += '\nFilter once'
        body['tools'][0]['function']['description'] += ' filter'
        body['route_hint'] = 'round-' + str(len(seen))
        return body, {}

    async def frozen_prompt(request, body, user):
        body['messages'][0]['content'] = 'Frozen model prompt\n' + body['messages'][0]['content']
        return body

    def setup(ctx, ns, response_for):
        ns.update(
            ENABLE_FUNCTIONS=True,
            get_filter_functions=AsyncMock(return_value=['synthetic']),
            process_filter_functions=apply_filter,
            prepare_chat_system_prompt=frozen_prompt,
        )
        ctx['request'].state.max_tool_call_iterations = 3
        ctx['request'].state.request_filter_input = copy.deepcopy(baseline)
        ctx['form_data'] = copy.deepcopy(baseline)
        ctx['form_data']['messages'][0]['content'] = 'Frozen model prompt\nStable\nFilter once'
        ctx['form_data']['tools'][0]['function']['description'] = 'schema filter'

        async def generate(request, body, user, **kwargs):
            sent.append(copy.deepcopy(body))
            return response_for([tool_call_chunk()] if len(sent) < 3 else [chat_chunk('Done', 'stop')])

        ns['generate_chat_completion'] = AsyncMock(side_effect=generate)

    ctx = await runtime.run([tool_call_chunk()], setup=setup)
    assert len(seen) == (2 if deny_round else 3)
    for body in seen:
        assert body['messages'][0]['content'] == 'Stable'
        assert body['tools'][0]['function']['description'] == 'schema'
    for body in sent:
        assert body['messages'][0]['content'] == 'Frozen model prompt\nStable\nFilter once'
        assert body['tools'][0]['function']['description'] == 'schema filter'
    assert ctx['request'].state.request_filter_input == baseline
    if deny_round:
        assert len(sent) == 1
        assert 'Access revoked' in runtime.saved['error']['content']
    else:
        assert sent[-1]['tool_choice'] == 'none'
        assert sent[-1]['messages'][-1]['role'] == 'user'
        assert sent[1]['messages'][: len(sent[0]['messages'])] == sent[0]['messages']


@pytest.mark.asyncio
async def test_provider_ignoring_final_tool_choice_does_not_execute_more_tools(runtime):
    def setup(ctx, ns, response_for):
        ctx['form_data']['tools'] = [{'type': 'function', 'function': {'name': 'test_tool'}}]

    await runtime.run([tool_call_chunk()], follow_up=[tool_call_chunk()], setup=setup)
    assert runtime.ns['execute_tool_call'].await_count == 1
    sent = runtime.ns['generate_chat_completion'].await_args.args[1]
    assert sent['tools']
    assert sent['tool_choice'] == 'none'
    assert sent['messages'][-1]['role'] == 'user'
    assert runtime.saved['error']
