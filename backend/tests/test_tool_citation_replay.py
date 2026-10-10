"""Synthetic provider-boundary checks; no application import or live providers."""

import ast
import copy
import itertools
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from test_web_tool_citations import BACKEND, load_functions, tool_elements, hit
from test_web_tool_citations import runtime as citation_runtime
from test_image_tool_context import runtime as output_runtime
from test_tool_result_storage import database, edit


@pytest.fixture
def pipeline(citation_runtime, output_runtime):
    ns = {**output_runtime.ns, **citation_runtime.ns}
    ids = itertools.count()
    ns['output_id'] = lambda prefix: f'{prefix}-{next(ids)}'
    tree = load_functions(
        ns,
        'utils/middleware.py',
        {'process_messages_with_output', 'drain_approved_tool_calls', 'tool_result_content', 'strip_reasoning_details'},
    )
    prepare = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    tail_start = next(
        i
        for i, node in enumerate(prepare.body)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'normalize_messages_for_model'
    )
    wrapper = ast.parse('def finalize_payload(form_data, metadata):\n    pass\n    return form_data, metadata')
    wrapper.body[0].body = prepare.body[tail_start:-1] + wrapper.body[0].body[-1:]
    exec(compile(ast.fix_missing_locations(wrapper), 'payload_tail', 'exec'), ns)
    # Run production result attachment, output assembly, initial rules and dispatch.
    for node in ast.walk(tree):
        body = getattr(node, 'body', [])
        if not isinstance(body, list):
            continue
        for index, child in enumerate(body):
            if (
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Await)
                and isinstance(child.value.value, ast.Call)
                and isinstance(child.value.value.func, ast.Name)
                and child.value.value.func.id == 'attach_tool_citations'
                and any(isinstance(v, ast.Name) and v.id == 'tool_function_name' for v in ast.walk(child))
            ):
                wrapper = ast.parse('async def produce_result():\n    pass')
                wrapper.body[0].body = body[index - 1 : index + 2]
                exec(compile(ast.fix_missing_locations(wrapper), 'native_result', 'exec'), ns)
    wrapper = ast.parse('async def assemble_results():\n    pass')
    wrapper.body[0].body = [output_runtime.result_loop]
    exec(compile(ast.fix_missing_locations(wrapper), 'native_assembly', 'exec'), ns)
    # dispatch_followup closes over its original globals, so recompile in this namespace.
    dispatch = output_runtime.ns['dispatch_followup']
    ns['dispatch_followup'] = type(dispatch)(dispatch.__code__, ns)
    ns.update(
        output=[],
        results=[],
        result_status_by_call_id={},
        citation_source_ids={},
        citations_enabled=True,
        model_id='synthetic',
        model={'id': 'synthetic'},
        metadata={},
        tool={},
        tool_result_files=[],
        tool_result_embeds=[],
        get_reasoning_format=lambda model: None,
        filter_functions=[],
        generate_chat_completion=AsyncMock(),
        user=SimpleNamespace(id='owner'),
        request=object(),
        ENABLE_RESPONSES_API_STATEFUL=False,
        last_response_id=None,
    )
    ns['form_data'] = copy.deepcopy(citation_runtime.ns['form_data'])
    ns['form_data']['tools'] = [
        {'type': 'function', 'function': {'name': 'fetch_url', 'parameters': {'type': 'object'}}}
    ]
    return SimpleNamespace(ns=ns, events=citation_runtime.events, frontend_loop=output_runtime.frontend_loop)


async def run_tool(pipeline, name, params, text):
    ns = pipeline.ns
    call_id = str(len(ns['output']))
    ns['output'].append(
        {
            'type': 'function_call',
            'id': f'fc-{call_id}',
            'call_id': call_id,
            'name': name,
            'arguments': json.dumps(params),
            'status': 'completed',
        }
    )
    ns.update(tool_call_id=call_id, tool_function_name=name, tool_function_params=params, tool_result=text)
    await ns['produce_result']()
    return call_id


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['chat', 'responses', 'stateful'])
async def test_search_then_five_fetches_preserve_final_provider_prefix(pipeline, api):
    ns = pipeline.ns
    ns['ENABLE_RESPONSES_API_STATEFUL'] = api == 'stateful'
    ns['last_response_id'] = 'previous' if api == 'stateful' else None
    baseline = copy.deepcopy(ns['form_data'])
    previous_messages = baseline['messages']
    previous_input = ns['convert_to_responses_payload'](copy.deepcopy(baseline))['input']
    initial_system = baseline['messages'][0]['content']
    snapshots = []
    for index in range(6):
        ns['results'] = []
        name = 'search_web' if index == 0 else 'fetch_url'
        params = {} if index == 0 else {'url': f'https://example.test/{index}'}
        raw = json.dumps([hit(1), hit(2)]) if index == 0 else f'Fetched page {index}. ' * 60
        call_id = await run_tool(pipeline, name, params, raw)
        await ns['assemble_results']()
        frozen = copy.deepcopy(ns['output'])
        await ns['dispatch_followup']()
        payload = copy.deepcopy(ns['generate_chat_completion'].await_args.args[1])
        assert payload['messages'][0]['content'] == initial_system
        assert payload['tools'] == baseline['tools']
        assert ns['form_data'] == baseline
        assert ns['output'] == frozen
        assert ns['output'][: len(snapshots)] == snapshots
        snapshots = frozen
        assert ns['output'][-1]['output'][0]['text'] == raw
        assert '<tool_sources>' in ns['output'][-1]['citation_context']
        if api != 'stateful':
            assert payload['messages'][: len(previous_messages)] == previous_messages
            previous_messages = copy.deepcopy(payload['messages'])
        if api != 'chat':
            wire = ns['convert_to_responses_payload'](payload)
            assert wire['instructions'] == initial_system
            if api == 'stateful':
                assert [item['type'] for item in wire['input']] == ['function_call_output']
                assert wire['input'][0]['call_id'] == call_id
            else:
                assert wire['input'][: len(previous_input)] == previous_input
                previous_input = copy.deepcopy(wire['input'])
            assert 'citation_context' not in json.dumps(wire)
            assert 'citation_sources' not in json.dumps(wire)
    assert len(pipeline.events) == 6
    assert [
        tool_elements(item['citation_context'])[0].attrib['id']
        for item in ns['output']
        if item['type'] == 'function_call_output'
    ] == ['1', '1', '2', '3', '4', '5']


@pytest.mark.asyncio
async def test_saved_and_temporary_replay_keep_frozen_context_and_reasoning(pipeline):
    ns = pipeline.ns
    ns['output'].append({'type': 'reasoning', 'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}]})
    await run_tool(pipeline, 'fetch_url', {'url': 'https://example.test/1'}, 'Original evidence')
    await ns['assemble_results']()
    original = copy.deepcopy(ns['output'])
    frontend = {'full_output': lambda: original, 'frontend_output': [], 'snapshot': False, 'output_start': 0}
    exec(compile(ast.Module(body=[pipeline.frontend_loop], type_ignores=[]), 'frontend_output', 'exec'), frontend)
    for stored in (original, frontend['frontend_output']):
        serialized = json.loads(json.dumps(stored))
        for _ in range(2):
            messages = ns['process_messages_with_output']([{'role': 'assistant', 'output': serialized}])
            assert messages[0]['reasoning_details'] == original[0]['reasoning_details']
            assert messages[-1]['content'] == 'Original evidence\n\n' + original[-1]['citation_context']
            assert messages[-1]['content'].count('<tool_sources>') == 1
            assert serialized == stored
    assert ns['output'] == original
    # Native stored Responses items must project the same frozen text and strip local fields.
    wire = ns['convert_to_responses_payload']({'messages': [{'role': 'assistant', 'output': original}]})
    result = wire['input'][-1]
    assert set(result) == {'type', 'call_id', 'output'}
    assert result['output'][-1]['text'] == '\n\n' + original[-1]['citation_context']
    assert ns['output'] == original


@pytest.mark.asyncio
async def test_approval_restores_ids_preserves_prepared_prefix_and_does_not_run_twice(pipeline):
    ns = pipeline.ns
    await run_tool(pipeline, 'search_web', {}, json.dumps([hit(1), hit(2)]))
    await ns['assemble_results']()
    old = copy.deepcopy(ns['output'])
    queued = {
        'type': 'function_call',
        'call_id': 'approved',
        'name': 'fetch_url',
        'arguments': json.dumps({'url': 'https://example.test/2'}),
        'status': 'queued',
        'approved': True,
    }
    message = {
        'role': 'assistant',
        'model': 'synthetic',
        'sources': [event['data'] for event in pipeline.events],
        'output': [*old, queued],
    }
    form = {'messages': copy.deepcopy(ns['form_data']['messages']) + ns['convert_output_to_messages'](old, raw=True)}
    prefix = copy.deepcopy(form['messages'])

    async def save(chat_id, message_id, update, **kwargs):
        message.update(copy.deepcopy(update))

    ns.update(
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(side_effect=lambda *args: message),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
        is_saved_chat_id=lambda value: value == 'chat',
        ENABLE_PLUGINS=False,
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(
            return_value={'tool_call_id': 'approved', 'content': 'Fetched evidence'}
        ),
    )
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'assistant', 'sources': []}
    for _ in range(2):
        await ns['drain_approved_tool_calls'](None, form, ns['user'], ns['model'], metadata)
    ns['execute_tool_call_for_output'].assert_awaited_once()
    assert form['messages'][: len(prefix)] == prefix
    assert message['output'][: len(old)] == old
    result = next(
        item
        for item in message['output']
        if item.get('call_id') == 'approved' and item['type'] == 'function_call_output'
    )
    assert tool_elements(result['citation_context'])[0].attrib['id'] == '2'
    assert form['messages'][-1]['content'] == 'Fetched evidence\n\n' + result['citation_context']
    assert len([event for event in pipeline.events if event['type'] == 'source']) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,raw', [(False, 'Evidence'), (True, ''), (True, '{"error":"failure"}')])
async def test_disabled_empty_failed_results_keep_raw_output_without_sources(pipeline, enabled, raw):
    ns = pipeline.ns
    ns['citations_enabled'] = enabled
    await run_tool(pipeline, 'fetch_url', {'url': 'https://example.test/1'}, raw)
    await ns['assemble_results']()
    assert ns['output'][-1]['output'][0]['text'] == raw
    assert 'citation_context' not in ns['output'][-1]
    assert pipeline.events == []


@pytest.mark.asyncio
async def test_multiple_tools_in_one_round_share_order_and_keep_distinct_urls(pipeline):
    ns = pipeline.ns
    await run_tool(pipeline, 'search_web', {}, json.dumps([hit(1, title='Same'), hit(2, title='Same')]))
    await run_tool(pipeline, 'fetch_url', {'url': 'https://example.test/2'}, 'Actual page')
    await ns['assemble_results']()
    messages = ns['convert_output_to_messages'](ns['output'], raw=True)
    assert len(messages[0]['tool_calls']) == 2
    assert [element.attrib['id'] for message in messages[1:] for element in tool_elements(message['content'])] == [
        '1',
        '2',
        '2',
    ]
    registry = ns['get_tool_citation_source_ids']([], json.loads(json.dumps(ns['output'])))
    assert registry == {'https://example.test/1': 1, 'https://example.test/2': 2}


def test_old_results_replay_without_citation_changes(pipeline):
    ns = pipeline.ns
    output = [
        {'type': 'function_call', 'call_id': 'old', 'name': 'f', 'status': 'completed', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'old', 'output': [{'type': 'input_text', 'text': 'Old result'}]},
    ]
    assert ns['convert_output_to_messages'](output, raw=True)[-1]['content'] == 'Old result'
    assert ns['get_tool_citation_source_ids']([], output) == {}


@pytest.mark.asyncio
async def test_frozen_context_is_not_regenerated(pipeline):
    ns = pipeline.ns
    result = {'content': 'Evidence'}
    await ns['attach_tool_citations'](
        result, 'fetch_url', {'url': 'https://example.test/1'}, {}, {}, ns['event_emitter']
    )
    original = copy.deepcopy(result)
    await ns['attach_tool_citations'](
        result, 'fetch_url', {'url': 'https://example.test/1'}, {}, {'other': 1}, ns['event_emitter']
    )
    assert result == original
    assert len(pipeline.events) == 1


def test_initial_request_installs_fixed_rules_once_before_sources(pipeline):
    ns = pipeline.ns
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    process = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    block = next(
        node
        for node in process.body
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == 'add_tool_citation_instructions'
            for child in ast.walk(node)
        )
    )
    for enabled in (True, False):
        ns['form_data']['messages'] = [
            {'role': 'system', 'content': 'Resolved model prompt'},
            {'role': 'user', 'content': 'Question'},
        ]
        ns['model'] = {'info': {'meta': {'capabilities': {'citations': enabled}}}}
        for _ in range(2):
            exec(compile(ast.Module(body=[block], type_ignores=[]), 'initial_citation_rules', 'exec'), ns)
        system = ns['form_data']['messages'][0]['content']
        assert system.startswith('Resolved model prompt')
        assert system.count('### Tool sources and citations') == int(enabled)
        assert '<tool_sources>' not in system


@pytest.mark.asyncio
async def test_approval_argument_error_does_not_attempt_citation_parsing(pipeline):
    ns = pipeline.ns
    queued = {
        'type': 'function_call',
        'call_id': 'invalid',
        'name': 'fetch_url',
        'arguments': '{',
        'status': 'queued',
        'approved': True,
    }
    message = {'output': [queued]}

    async def save(*args, **kwargs):
        message.update(args[2])

    ns.update(
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(return_value=message),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
        is_saved_chat_id=lambda value: True,
        ENABLE_PLUGINS=False,
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=AsyncMock(
            return_value={'tool_call_id': 'invalid', 'content': 'Error: Invalid JSON'}
        ),
    )
    await ns['drain_approved_tool_calls'](
        None, ns['form_data'], ns['user'], ns['model'], {'chat_id': 'chat', 'assistant_message_id': 'assistant'}
    )
    result = message['output'][1]
    assert result['status'] == 'failed'
    assert 'citation_context' not in result


@pytest.mark.asyncio
async def test_v2_counts_citations_and_preserves_them_when_projecting_raw_text(pipeline, database):
    ns, db = pipeline.ns, database
    await run_tool(pipeline, 'fetch_url', {'url': 'https://example.test/1'}, db.text)
    await ns['assemble_results']()
    output = copy.deepcopy(ns['output'])
    await edit(db, 'a', output=output)
    context = output[-1]['citation_context']
    payload = {
        'model': 'model',
        'messages': ns['process_messages_with_output']([db.raw['u'], db.raw['a']], preserve_compaction_ids=True),
    }
    result = payload['messages'][-1]
    ref = result['_tool_result_refs'][0]
    assert ref['text'] == db.text
    assert result['content'][ref['start'] : ref['end']] == db.text
    assert result['content'].endswith(context)
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    plain = copy.deepcopy(payload)
    plain['messages'][-1]['content'] = db.text
    assert v2.estimate_payload_tokens(payload) > v2.estimate_payload_tokens(plain)
    settings = v2.CompactionSettings(mode='v2', default_context_window=6000, recent_tokens=200, reminder_enabled=False)
    state = dict(
        mode='v2',
        settings=settings,
        chat_id='chat',
        user_message_id='u',
        metadata={'chat_id': 'chat', 'message_id': 'a'},
    )
    request = SimpleNamespace(state=SimpleNamespace(context_compaction=state))
    with (
        patch.object(v2, 'load_stored_models', AsyncMock(return_value={})),
        patch.object(v2, 'emit_event', AsyncMock()),
        patch.object(v2, 'generate_summary', AsyncMock(return_value='The retrieved value is 42.')) as summarize,
    ):
        projected = await v2.prepare_v2_request(
            request, SimpleNamespace(id='owner', role='user'), payload, {}, 'model', {'model': {}}
        )
    assert summarize.await_count == 1
    assert projected['messages'][-1]['content'].endswith('\n\n' + context)
    assert projected['messages'][-1]['content'] != result['content']
    assert payload['messages'][-1]['content'] == db.text + '\n\n' + context
    contexts = await db.store.list_contexts('chat', 'model', 'a')
    assert (await db.store.get_result(contexts[0]['result_id'], branch_id='a'))['text'] == db.text
    assert db.raw['a']['output'] == output


@pytest.mark.asyncio
@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('mode', ['off', 'v2'])
async def test_approval_keeps_reasoning_and_parallel_calls_in_same_response_span(pipeline, partial, mode):
    ns = pipeline.ns
    reasoning = {
        'type': 'reasoning',
        'summary': [{'type': 'summary_text', 'text': 'Plan'}],
        'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}],
    }
    call_a = {
        'type': 'function_call',
        'call_id': 'A',
        'name': 'fetch_url',
        'arguments': '{"url":"https://example.test/1"}',
        'status': 'completed' if partial else 'queued',
        'approved': True,
    }
    call_b = {
        'type': 'function_call',
        'call_id': 'B',
        'name': 'fetch_url',
        'arguments': '{"url":"https://example.test/2"}',
        'status': 'queued',
        'approved': True,
    }
    output = [reasoning, call_a, call_b]
    if partial:
        output.append(
            {
                'type': 'function_call_output',
                'call_id': 'A',
                'output': [{'type': 'input_text', 'text': 'First evidence'}],
            }
        )
    from open_webui.utils.tool_result_sources import injected_message

    sidecar = {'id': 'injected', 'name': 'search_web', 'text': 'Prepared search evidence', 'kind': 'injected'}
    message = {
        'id': 'assistant',
        'role': 'assistant',
        'model': 'synthetic',
        'output': output,
        'tool_results': [sidecar],
    }
    prepared = ns['process_messages_with_output'](
        [copy.deepcopy(message)],
        reasoning_format='reasoning_content',
        current_response_id='assistant',
        preserve_compaction_ids=mode == 'v2',
    )
    prefix = copy.deepcopy(ns['form_data']['messages'])
    tail = injected_message(
        [dict(sidecar, id='attachment', text='Prepared attachment context')], 'assistant', role='user'
    )
    if mode != 'v2':
        tail = {key: value for key, value in tail.items() if key in ('role', 'content')}
    form = {'messages': [*prefix, *prepared, tail]}
    metadata = {'chat_id': 'chat', 'assistant_message_id': 'assistant'}
    form, metadata = ns['finalize_payload'](form, metadata)
    before_evidence = copy.deepcopy(
        [msg for msg in form['messages'] if 'Prepared search evidence' in str(msg.get('content'))]
    )
    if mode == 'off':
        assert all('_context_message_id' not in msg for msg in form['messages'])
    assert all('_current_response_output' not in msg for msg in form['messages'])

    async def save(*args, **kwargs):
        message.update(copy.deepcopy(args[2]))

    async def execute(*args):
        return {'tool_call_id': args[-1]['id'], 'content': 'Evidence'}

    ns.update(
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(return_value=message),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
        is_saved_chat_id=lambda value: True,
        ENABLE_PLUGINS=False,
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
        execute_tool_call_for_output=execute,
        get_reasoning_format=lambda model: 'reasoning_content',
    )
    await ns['drain_approved_tool_calls'](
        SimpleNamespace(state=SimpleNamespace(context_compaction={'mode': mode})),
        form,
        ns['user'],
        ns['model'],
        metadata,
    )
    assistant = next(msg for msg in form['messages'] if any(call['id'] == 'B' for call in msg.get('tool_calls', [])))
    assert assistant['reasoning_details'] == reasoning['reasoning_details']
    assert assistant['reasoning_content'] == 'Plan'
    assert [call['id'] for call in assistant['tool_calls']] == ['A', 'B']
    assert sum(bool(msg.get('reasoning_details')) for msg in form['messages']) == 1
    assert form['messages'][: len(prefix)] == prefix
    assert form['messages'][-1] == tail
    assert [msg for msg in form['messages'] if 'Prepared search evidence' in str(msg.get('content'))] == before_evidence
    assert all('_current_response_output' not in msg for msg in form['messages'])


@pytest.mark.asyncio
async def test_stream_resume_reads_sources_saved_after_approval(pipeline):
    ns = pipeline.ns
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'message' for target in node.targets)
        and any(isinstance(child, ast.Constant) and child.value == 'assistant_message' for child in ast.walk(node))
    )
    wrapper = ast.parse('async def load_stream_message():\n    pass\n    return message')
    wrapper.body[0].body[0] = assignment
    exec(compile(ast.fix_missing_locations(wrapper), 'stream_resume_message', 'exec'), ns)
    fresh_source = ns['get_citation_source_from_tool_result'](
        'fetch_url', {'url': 'https://example.test/1'}, 'Approved evidence'
    )
    ns.update(
        ctx={'assistant_message': {'sources': []}},
        continuing=True,
        save_to_chat=True,
        metadata={'chat_id': 'chat', 'message_id': 'assistant'},
        Chats=SimpleNamespace(get_message_by_id_and_message_id=AsyncMock(return_value={'sources': fresh_source})),
    )
    saved = await ns['load_stream_message']()
    source_ids = ns['get_tool_citation_source_ids'](saved['sources'], [])
    result = {'content': 'New evidence'}
    await ns['attach_tool_citations'](result, 'fetch_url', {'url': 'https://example.test/2'}, {}, source_ids, None)
    assert tool_elements(result['citation_context'])[0].attrib['id'] == '2'


@pytest.mark.asyncio
async def test_pre_rag_system_metadata_still_carries_model_prompt_for_background_tasks(pipeline):
    ns = pipeline.ns
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    process = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    index = next(
        i
        for i, node in enumerate(process.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == 'system_prompt'
            for target in node.targets
        )
    )
    start = max(
        i
        for i, node in enumerate(process.body[:index])
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'system_message' for target in node.targets)
    )
    wrapper = ast.parse('async def capture_prompt():\n    pass')
    wrapper.body[0].body = process.body[start : index + 1]
    exec(compile(ast.fix_missing_locations(wrapper), 'inherited_system_prompt', 'exec'), ns)
    initial = copy.deepcopy(ns['form_data'])
    ns.update(
        model_system_prompt='Model template', resolve_system_prompt=AsyncMock(return_value='Resolved model prompt')
    )
    await ns['capture_prompt']()
    assert ns['metadata']['system_prompt'] == 'Resolved model prompt\n' + initial['messages'][0]['content']
    assert ns['form_data'] == initial
