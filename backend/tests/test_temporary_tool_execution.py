"""Real temporary storage at the middleware effect boundary; no application imports."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def context():
    from open_webui.utils.context_scope import ContextScope
    from open_webui.utils.context_store_temporary import TemporaryContextAdapter
    from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository

    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')

    def seed(current, now):
        current['data']['snapshot'] = {
            'messages_map': {
                'u': {'id': 'u', 'role': 'user', 'parentId': None, 'content': 'question'},
                'a': {'id': 'a', 'role': 'assistant', 'parentId': 'u', 'content': '', 'model': 'model'},
            },
            'current_id': 'a',
            'owner_id': 'owner',
            'shared_history': False,
            'chat': {},
        }

    doc, _ = await repo.transact(doc['id'], 'owner', doc['epoch'], seed, source_changed=True)
    doc, _ = await repo.register_tasks(
        doc['id'],
        'owner',
        doc['epoch'],
        [dict(task_id='task', reply_id='a', model_id='model', branch_id='u', worker_nonce='worker')],
        expected_revision=doc['revision'],
    )
    scope = ContextScope(
        'temporary',
        doc['id'],
        'owner',
        epoch=doc['epoch'],
        branch_id='a',
        reply_id='a',
        model_id='model',
        task_id='task',
        execution_fence=doc['fence'],
    )
    adapter = TemporaryContextAdapter(repo, scope)
    return SimpleNamespace(repo=repo, doc=doc, scope=scope, adapter=adapter, store=adapter.bundle())


def execution(ctx, round_id='round', ordinal=0):
    from open_webui.utils.temporary_tool_execution import TemporaryToolExecution

    return TemporaryToolExecution(ctx.store, round_id=round_id, ordinal=ordinal, call_id='call')


@pytest.mark.asyncio
async def test_preeffect_storage_failure_never_executes(context, monkeypatch):
    from open_webui.utils.context_store_errors import ContextUnavailable

    monkeypatch.setattr(context.repo, 'begin_execution', AsyncMock(side_effect=ContextUnavailable()))
    effect = AsyncMock()
    with pytest.raises(ContextUnavailable):
        async with execution(context) as guard:
            await guard.start({'name': 'write', 'params': {}})
            await effect()
    effect.assert_not_called()


@pytest.mark.asyncio
async def test_posteffect_persistence_failure_is_uncertain_and_never_reexecutes(context, monkeypatch):
    from open_webui.utils.context_store_errors import ContextUnavailable, ContextConflict

    monkeypatch.setattr(context.adapter, 'save_execution_result', AsyncMock(side_effect=ContextUnavailable()))
    effect = AsyncMock(return_value={'content': 'raw'})
    with pytest.raises(ContextUnavailable):
        async with execution(context) as guard:
            await guard.start({'name': 'write'})
            await guard.finish(await effect())
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    assert next(iter(doc['executions'].values()))['status'] == 'uncertain'
    with pytest.raises(ContextConflict):
        async with execution(context) as guard:
            await guard.start({'name': 'write'})
            await effect()
    assert effect.await_count == 1


@pytest.mark.asyncio
async def test_resolved_duplicate_replays_exact_normalized_output_and_new_round_executes(context):
    value = {
        'content': '',
        'files': [{'type': 'image', 'url': '/safe/image', 'context_only': True}],
        'citation_context': '[1]',
        'citation_sources': [{'document': ['raw']}],
    }
    async with execution(context) as first:
        assert await first.start({'name': 'read'}) is None
        await first.finish(value)
    async with execution(context) as duplicate:
        assert await duplicate.start({'name': 'read'}) == value
        assert duplicate.replayed
    async with execution(context, round_id='next') as following:
        assert await following.start({'name': 'read'}) is None
        assert not following.replayed
        await following.finish({'content': 'fresh'})
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    assert len(doc['executions']) == 2


@pytest.mark.asyncio
async def test_cancellation_after_effect_is_uncertain(context):
    with pytest.raises(asyncio.CancelledError):
        async with execution(context) as guard:
            await guard.start({'name': 'code'})
            raise asyncio.CancelledError()
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    entry = next(iter(doc['executions'].values()))
    assert (entry['status'], entry['reason_code']) == ('uncertain', 'cancelled')


@pytest.mark.asyncio
async def test_concurrent_duplicate_started_cannot_execute(context):
    from open_webui.utils.context_store_errors import ContextConflict

    async with execution(context) as first:
        await first.start({'name': 'delegate'})
        with pytest.raises(ContextConflict):
            async with execution(context) as duplicate:
                await duplicate.start({'name': 'delegate'})
        await first.finish({'content': 'done'})
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    assert next(iter(doc['executions'].values()))['status'] == 'resolved'


class ForbiddenSQL:
    def __getattr__(self, name):
        raise AssertionError(f'Temporary middleware used SQL: {name}')


def middleware_namespace(context):
    import ast
    import copy
    import json
    import logging
    from uuid import uuid4
    from test_web_tool_citations import load_functions
    from test_tool_dispatch_v0122 import dispatch_namespace

    ns = dict(
        ast=ast,
        copy=copy,
        json=json,
        JSONCodec=json,
        uuid4=uuid4,
        log=logging.getLogger(__name__),
        Chats=ForbiddenSQL(),
        is_saved_chat_id=lambda value: bool(value) and not value.startswith('temporary:'),
        get_updated_tool_function=AsyncMock(side_effect=lambda **kw: kw['function']),
        build_terminal_file_tool_result=lambda *args: None,
        process_tool_result=AsyncMock(side_effect=lambda req, name, raw, *args: (raw, [], [])),
        terminal_event_handler=AsyncMock(),
        attach_tool_citations=AsyncMock(),
        store_tool_result_image=AsyncMock(return_value='/stored/image'),
    )
    ns.update(dispatch_namespace())
    load_functions(
        ns,
        'utils/middleware.py',
        {
            'execute_tool_call',
            'execute_temporary_tool_call_for_output',
            'tool_result_content',
            '_is_tool_result_error',
            'build_chat_response_context',
            'persist_injected_tool_results',
        },
    )
    ns['request'] = SimpleNamespace(state=SimpleNamespace(context_store=context.store))
    effect = AsyncMock(return_value='original')
    ns['metadata'] = {
        'chat_id': context.scope.store_key,
        'message_id': 'a',
        'user_message_id': 'u',
        'tools': {
            'read': {
                'tool_id': 'external:read',
                'type': 'external',
                'callable': effect,
                'spec': {'parameters': {'properties': {'x': {'type': 'integer'}}}},
            }
        },
    }
    ns['effect'] = effect
    return ns


async def run_native(ns, *, round_id='round', ordinal=0, call=None, event_caller=None, sidecar=None):
    return await ns['execute_temporary_tool_call_for_output'](
        ns['request'],
        {'model': 'model'},
        SimpleNamespace(id='owner'),
        ns['metadata'],
        event_caller,
        None,
        call or {'id': 'call', 'function': {'name': 'read', 'arguments': '{"x": 1}'}},
        round_id=round_id,
        ordinal=ordinal,
        sidecar=sidecar,
    )


@pytest.mark.asyncio
async def test_native_normalized_images_citations_and_provider_wrapper_replay_once(context):
    import copy
    from open_webui.utils.images.context import build_tool_result_parts

    ns = middleware_namespace(context)
    ns['metadata']['params'] = {'tool_approval_mode': 'ask'}
    ns['metadata']['tools']['call_tool'] = {'type': 'builtin', 'tool_id': 'builtin:call_tool'}
    ns['process_tool_result'] = AsyncMock(
        return_value=('', [{'type': 'image', 'url': 'data:image/png;base64,eA==', 'context_only': True}], ['embed'])
    )

    async def citations(result, *args, **kwargs):
        result.update(citation_context='frozen [1]', citation_sources=[{'document': ['raw evidence']}])

    ns['attach_tool_citations'] = citations
    approval = AsyncMock(return_value=True)
    call = {
        'id': 'call',
        'function': {
            'name': 'call_tool',
            'arguments': ns['JSONCodec'].dumps({'tool_name': 'read', 'params': ns['JSONCodec'].dumps({'x': 1})}),
        },
    }
    original = copy.deepcopy(call['function'])
    result, execution_id = await run_native(ns, call=call, event_caller=approval)
    output = [result['publication']['call_item'], result['output_item']]
    await context.adapter.commit_raw_results('a', {output[-1]['id']: execution_id}, output)
    replay, repeated_id = await run_native(ns, call=copy.deepcopy(call), event_caller=approval)
    assert replay == {key: value for key, value in result.items() if key != 'publication'}
    assert repeated_id == execution_id
    ns['effect'].assert_awaited_once()
    ns['process_tool_result'].assert_awaited_once()
    approval.assert_awaited_once()
    raw = (await context.adapter.read_snapshot())['messages_map']['a']['output']
    assert raw == output
    assert raw[0]['provider_function'] == original
    assert result['files'][0]['url'] == '/stored/image'


@pytest.mark.asyncio
@pytest.mark.parametrize('reply', [False, {'error': 'disconnected'}])
async def test_temporary_approval_denial_has_no_execution_proof(context, reply):
    ns = middleware_namespace(context)
    ns['metadata']['params'] = {'tool_approval_mode': 'ask'}
    result, execution_id = await run_native(ns, event_caller=AsyncMock(return_value=reply))
    assert 'not approved' in result['content'] and execution_id is None
    ns['effect'].assert_not_awaited()
    assert not (await context.repo.read(context.doc['id'], 'owner', context.doc['epoch']))['executions']


@pytest.mark.asyncio
async def test_native_posteffect_exception_and_direct_disconnect_do_not_fabricate_result(context):
    from open_webui.utils.context_store_errors import ContextConflict

    ns = middleware_namespace(context)
    ns['effect'].side_effect = RuntimeError('effect outcome is unknown')
    with pytest.raises(RuntimeError):
        await run_native(ns)
    with pytest.raises(ContextConflict):
        await run_native(ns)
    ns['effect'].assert_awaited_once()
    ns['metadata']['tools']['read']['direct'] = True
    with pytest.raises(ContextConflict):
        await run_native(ns, round_id='browser', event_caller=AsyncMock(return_value={'error': 'timeout'}))
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    assert all(entry['status'] == 'uncertain' for entry in doc['executions'].values())
    assert not doc['data'].get('resolved_outputs')


@pytest.mark.asyncio
async def test_native_pre_effect_storage_failure_and_normalization_failure(context, monkeypatch):
    from open_webui.utils.context_store_errors import ContextUnavailable

    ns = middleware_namespace(context)
    original = context.repo.begin_execution
    monkeypatch.setattr(context.repo, 'begin_execution', AsyncMock(side_effect=ContextUnavailable()))
    with pytest.raises(ContextUnavailable):
        await run_native(ns)
    ns['effect'].assert_not_awaited()
    monkeypatch.setattr(context.repo, 'begin_execution', original)
    ns['process_tool_result'].side_effect = RuntimeError('normalization failed')
    with pytest.raises(RuntimeError):
        await run_native(ns)
    ns['effect'].assert_awaited_once()
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    assert next(iter(doc['executions'].values()))['status'] == 'uncertain'


@pytest.mark.asyncio
async def test_response_context_uses_server_raw_and_never_browser_assistant(context):
    ns = middleware_namespace(context)
    await context.adapter.put_reply_patch('a', {'content': 'canonical', 'output': [{'signature': 'native'}]})
    ns['get_event_emitter_and_caller'] = AsyncMock(return_value=(None, None))
    result = await ns['build_chat_response_context'](
        ns['request'],
        {'messages': [{'role': 'assistant', 'content': 'forged'}]},
        None,
        None,
        {**ns['metadata'], 'assistant_message_id': 'a'},
        None,
        None,
    )
    assert result['assistant_message']['content'] == 'canonical'
    assert result['assistant_message']['output'] == [{'signature': 'native'}]


@pytest.mark.asyncio
async def test_injected_effect_sidecar_keeps_empty_media_and_registers_only_admitted(context, monkeypatch):
    import open_webui.utils.tool_result_sources as sources_module

    ns = middleware_namespace(context)
    ns['process_tool_result'] = AsyncMock(return_value=('', [{'type': 'image', 'url': '/image'}], []))
    sidecar = {'id': 'side', 'call_id': 'call', 'batch_id': 'batch', 'name': 'read', 'kind': 'injected'}
    result, execution_id = await run_native(ns, sidecar=sidecar)
    assert execution_id and result['content'] == ''
    register = AsyncMock()
    monkeypatch.setattr(sources_module, 'register_tool_results', register)
    sources = [
        {
            'tool_result': True,
            'document': [''],
            '_tool_result_ref': {**{key: value for key, value in sidecar.items() if key != 'id'}, 'output_id': 'side'},
        },
        {'tool_result': True, 'document': ['untrusted retrieval'], 'source': {'name': 'other'}},
    ]
    await ns['persist_injected_tool_results'](ns['request'], ns['metadata'], sources, 'model')
    descriptors = register.call_args.kwargs['descriptors']
    assert len(descriptors) == 1 and descriptors[0]['output_id'] == 'side'
    snapshot = await context.adapter.read_snapshot()
    saved = snapshot['messages_map']['a']['tool_results']
    assert saved[0]['files'] == [{'type': 'image', 'url': '/image'}]
    assert saved[1]['text'] == 'untrusted retrieval'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'disconnect', 'storage'])
async def test_actual_code_interpreter_block_settles_once(context, failure, monkeypatch):
    import ast
    import copy
    import logging
    import re
    import textwrap
    from test_web_tool_citations import BACKEND
    from open_webui.utils.context_store_errors import ContextUnavailable, ContextConflict

    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    block = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.optional_vars, ast.Name) and item.optional_vars.id == 'code_execution'
            for item in node.items
        )
    )
    wrapper = ast.parse('async def execute_code():\n    pass')
    wrapper.body[0].body = [copy.deepcopy(block)]
    output = [
        {'id': 'code-id', 'type': 'open_webui:code_interpreter', 'code': 'print(1)', 'attributes': {'type': 'code'}}
    ]
    callback = AsyncMock(return_value={'error': 'disconnected'} if failure == 'disconnect' else {'stdout': 'original'})
    from open_webui.utils.temporary_tool_execution import TemporaryToolExecution, commit_temporary_results
    from open_webui.utils.tool_result_sources import output_text

    ns = dict(
        request=SimpleNamespace(state=SimpleNamespace(context_store=context.store)),
        TemporaryToolExecution=TemporaryToolExecution,
        commit_temporary_results=commit_temporary_results,
        output_text=output_text,
        ci_item=output[0],
        full_output=lambda: output,
        copy=copy,
        log=logging.getLogger(__name__),
        Config=SimpleNamespace(get=AsyncMock(return_value='pyodide')),
        sanitize_code=lambda value: value,
        CODE_INTERPRETER_BLOCKED_MODULES=[],
        textwrap=textwrap,
        metadata={},
        event_caller=callback,
        uuid4=lambda: 'rpc',
        re=re,
    )
    exec(compile(ast.fix_missing_locations(wrapper), 'actual_code_execution', 'exec'), ns)
    if failure == 'storage':
        monkeypatch.setattr(context.repo, 'begin_execution', AsyncMock(side_effect=ContextUnavailable()))
    if failure:
        with pytest.raises(ContextUnavailable if failure == 'storage' else ContextConflict):
            await ns['execute_code']()
        assert callback.await_count == (0 if failure == 'storage' else 1)
    else:
        await ns['execute_code']()
        # The adapter drops staged duplicates after admission, but execution replay
        # must retain the exact code output item for another callback attempt.
        replay = await context.adapter.execution_result(
            next(iter((await context.repo.read(context.doc['id'], 'owner', context.doc['epoch']))['executions']))
        )
        assert replay['content'] == 'original'
        await ns['execute_code']()
        callback.assert_awaited_once()
        assert (await context.adapter.read_snapshot())['messages_map']['a']['output'] == output


from test_streaming_error_recovery import runtime as stream_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['off', 'v2'])
@pytest.mark.parametrize('failed_second', [False, True])
async def test_full_temporary_stream_commits_successful_results_without_sql(
    context, stream_runtime, mode, failed_second, monkeypatch
):
    import copy
    import sys
    from types import ModuleType
    from test_streaming_error_recovery import chat_chunk
    from open_webui.utils.context_compaction_config import CompactionSettings

    for suffix in (
        'chats',
        'chat_context_checkpoints',
        'chat_tool_result_selections',
        'chat_tool_results',
        'chat_tool_result_contexts',
    ):
        module = ModuleType(f'open_webui.models.{suffix}')
        module.__getattr__ = lambda name: (_ for _ in ()).throw(AssertionError(f'SQL import: {name}'))
        monkeypatch.setitem(sys.modules, module.__name__, module)
    ns = middleware_namespace(context)

    async def effect(x):
        if x == 2 and failed_second:
            raise RuntimeError('second effect outcome unknown')
        return f'raw-{x}'

    ns['effect'].side_effect = effect
    ns['metadata']['params'] = {'reasoning_tags': False}

    def setup(ctx, stream_ns, response_for):
        ctx['metadata'] = ns['metadata']
        ctx['request'].state.context_store = context.store
        ctx['request'].state.context_compaction = dict(
            mode=mode,
            settings=CompactionSettings(mode=mode),
            chat_id=context.scope.store_key,
            user_message_id='u',
            model_id='model',
        )
        ctx['form_data']['model'] = ctx['model']['id'] = 'model'
        ctx['user'].id = 'owner'
        stream_ns['Chats'] = ForbiddenSQL()
        stream_ns['execute_temporary_tool_call_for_output'] = ns['execute_temporary_tool_call_for_output']
        stream_ns['CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS'] = 2

    chunk = {
        'choices': [
            {
                'delta': {
                    'tool_calls': [
                        {
                            'index': index,
                            'id': f'call-{index}',
                            'function': {'name': 'read', 'arguments': f'{{"x":{index + 1}}}'},
                        }
                        for index in range(2)
                    ]
                },
                'finish_reason': 'tool_calls',
            }
        ]
    }
    if failed_second:
        with pytest.raises(RuntimeError, match='second effect outcome unknown'):
            await stream_runtime.run([chunk, 'data: [DONE]'], setup=setup)
    else:
        await stream_runtime.run(
            [chunk, 'data: [DONE]'], setup=setup, follow_up=[chat_chunk('answer', 'stop'), 'data: [DONE]']
        )
    assert ns['effect'].await_count == 2
    doc = await context.repo.read(context.doc['id'], 'owner', context.doc['epoch'])
    raw = doc['data']['snapshot']['messages_map']['a']['output']
    result_items = [item for item in raw if item['type'] == 'function_call_output']
    assert len(result_items) == (1 if failed_second else 2)
    assert result_items[0]['output'][0]['text'] == 'raw-1'
    assert len(doc['data']['result_admissions']) == len(result_items)
    assert not doc['data'].get('resolved_outputs')
    stream_runtime.ns['save_response_stream'].assert_not_awaited()
    assert stream_runtime.ns['generate_chat_completion'].await_count == (0 if failed_second else 1)
    if failed_second:
        assert any(entry['status'] == 'uncertain' for entry in doc['executions'].values())
        assert next(item for item in raw if item.get('call_id') == 'call-1')['status'] == 'incomplete'
    elif mode == 'v2':
        assert len(doc['data']['selections']) == 2


@pytest.mark.asyncio
async def test_actual_fallback_handler_commits_before_source_publication(context):
    from test_web_tool_citations import load_functions

    ns = middleware_namespace(context)
    ns.update(
        Config=SimpleNamespace(
            get_many=AsyncMock(
                return_value={'task.model.default': None, 'task.model.external': None, 'task.tools.prompt_template': ''}
            )
        ),
        get_task_model_id=lambda *args: 'model',
        DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE='tools',
        tools_function_calling_generation_template=lambda *args: 'tools',
        get_last_user_message=lambda messages: messages[-1]['content'],
        get_content_from_message=lambda message: message['content'],
        TASKS=SimpleNamespace(FUNCTION_CALLING='function-calling'),
        generate_chat_completion=AsyncMock(
            return_value={
                'choices': [{'message': {'content': '{"tool_calls":[{"name":"read","parameters":{"x":1}}]}'}}]
            }
        ),
    )
    load_functions(ns, 'utils/middleware.py', {'chat_completion_tools_handler'})
    body, flags = await ns['chat_completion_tools_handler'](
        ns['request'],
        {'model': 'model', 'messages': [{'role': 'user', 'content': 'question'}]},
        {'__event_call__': None, '__event_emitter__': None, '__metadata__': ns['metadata']},
        SimpleNamespace(id='owner'),
        {},
        ns['metadata']['tools'],
    )
    ns['effect'].assert_awaited_once()
    sidecars = (await context.adapter.read_snapshot())['messages_map']['a']['tool_results']
    assert sidecars[0]['text'] == 'original'
    assert flags['sources'][0]['_tool_result_ref']['call_id'] == sidecars[0]['call_id']
    assert flags['sources'][0]['document'] == ['original']


@pytest.mark.asyncio
async def test_same_model_parallel_reply_identities_do_not_alias(context):
    from dataclasses import replace
    from open_webui.utils.context_store_temporary import TemporaryContextAdapter
    from open_webui.utils.temporary_tool_execution import TemporaryToolExecution

    def add_reply(doc, now):
        doc['data']['snapshot']['messages_map']['b'] = {
            'id': 'b',
            'role': 'assistant',
            'parentId': 'u',
            'model': 'model',
            'content': '',
        }

    doc, _ = await context.repo.transact(
        context.doc['id'], 'owner', context.doc['epoch'], add_reply, source_changed=True
    )
    await context.repo.register_tasks(
        doc['id'],
        'owner',
        doc['epoch'],
        [dict(task_id='other-task', reply_id='b', model_id='model', branch_id='u', worker_nonce='other-worker')],
        expected_revision=doc['revision'],
    )
    other = TemporaryContextAdapter(
        context.repo, replace(context.scope, task_id='other-task', reply_id='b', branch_id='b')
    )

    async def run(store, content):
        async with TemporaryToolExecution(store, round_id='same', ordinal=0, call_id='same') as guard:
            await guard.start({'name': 'read'})
            await guard.finish({'content': content})
            return guard.execution_id

    ids = await asyncio.gather(run(context.store, 'first'), run(other.bundle(), 'second'))
    assert ids[0] != ids[1]
    assert (await context.adapter.execution_result(ids[0]))['content'] == 'first'
    assert (await other.execution_result(ids[1]))['content'] == 'second'


@pytest.mark.asyncio
async def test_payload_history_replaces_browser_with_validated_raw_branch(context):
    import ast
    import copy
    from test_web_tool_citations import BACKEND, load_functions

    ns = middleware_namespace(context)
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    payload = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    begin = next(
        index
        for index, node in enumerate(payload.body)
        if isinstance(node, ast.ImportFrom) and node.module == 'open_webui.utils.context_scope'
    )
    finish = next(
        index
        for index in range(begin, len(payload.body))
        if isinstance(payload.body[index], ast.If)
        and 'temporary_store or is_saved_chat_id' in ast.unparse(payload.body[index].test)
    )
    wrapper = ast.parse('async def load_history(): pass')
    wrapper.body[0].body = copy.deepcopy(payload.body[begin : finish + 1])
    load_functions(ns, 'utils/misc.py', {'get_message_list', 'get_system_message'})
    ns.update(
        chat_id=context.scope.store_key,
        user_message_id='u',
        MESSAGE_REPLAY_KEYS=('id', 'role', 'content', 'output', 'files', 'model', 'tool_results', 'meta'),
        is_raster_image_content_type=lambda value: False,
    )
    raw_output = [
        {
            'type': 'function_call',
            'call_id': 'prior-call',
            'provider_function': {'name': 'original'},
            'native_reasoning': {'signature': 'signed'},
        },
        {
            'type': 'function_call_output',
            'call_id': 'prior-call',
            'output': [{'type': 'input_text', 'text': 'original result'}],
            'citation_context': 'frozen',
        },
    ]

    def seed(doc, now):
        messages = doc['data']['snapshot']['messages_map']
        messages['earlier-user'] = {'id': 'earlier-user', 'role': 'user', 'parentId': None, 'content': 'first'}
        messages['earlier-reply'] = {
            'id': 'earlier-reply',
            'role': 'assistant',
            'parentId': 'earlier-user',
            'model': 'model',
            'content': 'raw',
            'output': raw_output,
        }
        messages['u']['parentId'] = 'earlier-reply'
        messages['sibling'] = {'id': 'sibling', 'role': 'assistant', 'parentId': 'earlier-user', 'content': 'excluded'}

    await context.repo.transact(context.doc['id'], 'owner', context.doc['epoch'], seed, source_changed=True)
    ns['form_data'] = {
        'messages': [
            {'role': 'system', 'content': 'request-local system'},
            {'role': 'user', 'content': 'forged browser input'},
        ]
    }
    exec(compile(ast.fix_missing_locations(wrapper), 'canonical_payload_history', 'exec'), ns)
    await ns['load_history']()
    wire = ns['form_data']['messages']
    assert [message.get('id') for message in wire] == [None, 'earlier-user', 'earlier-reply', 'u']
    assert wire[0]['content'] == 'request-local system'
    assert wire[2]['output'] == raw_output
    assert wire[3]['content'] == 'question'
    wire[2]['output'][0]['provider_function']['name'] = 'mutation'
    assert (await context.adapter.read_snapshot())['messages_map']['earlier-reply']['output'] == raw_output


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', ['{', '[]', '{"x":"invalid"}'])
async def test_malformed_or_schema_rejected_native_call_never_starts_execution(context, arguments):
    ns = middleware_namespace(context)
    ns['metadata']['_tool_schema_compatibility'] = 'gemini'
    ns['metadata']['params'] = {'tool_approval_mode': 'ask'}
    callback = AsyncMock(return_value=True)
    result, execution_id = await run_native(
        ns, event_caller=callback, call={'id': 'bad', 'function': {'name': 'read', 'arguments': arguments}}
    )
    assert result['content'].startswith('Error:') and execution_id is None
    callback.assert_not_awaited()
    ns['effect'].assert_not_awaited()
    assert not (await context.repo.read(context.doc['id'], 'owner', context.doc['epoch']))['executions']


@pytest.mark.asyncio
async def test_temporary_cannot_enter_saved_approval_resume(context):
    from test_web_tool_citations import load_functions
    from open_webui.utils.context_store_errors import ContextConflict

    ns = middleware_namespace(context)
    load_functions(ns, 'utils/middleware.py', {'resume_tool_calls'})
    with pytest.raises(ContextConflict):
        await ns['resume_tool_calls'](ns['request'], {}, None, {}, ns['metadata'], {'output': []})
