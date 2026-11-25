"""Exercise projections, source storage, replay and exact rereading together."""

import asyncio
import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from test_tool_result_storage import database, edit, descriptor


@pytest_asyncio.fixture
async def checkpointed_commands(database):
    from open_webui.utils import context_compaction_v2 as v2

    with patch.multiple(
        importlib.import_module('open_webui.env'),
        CHAT_STREAM_RESPONSE_CHUNK_MAX_BUFFER_SIZE=1024,
        ENABLE_ORJSON=False,
        create=True,
    ):
        from open_webui.utils.misc import convert_output_to_messages

    db = database
    settings = v2.CompactionSettings(mode='v2', default_context_window=4096, recent_tokens=0, reminder_enabled=False)
    state = dict(mode='v2', settings=settings, chat_id='chat', user_message_id='u', metadata={'message_id': 'a'})
    request = SimpleNamespace(state=SimpleNamespace(context_compaction=state))
    user = {'role': 'user', 'content': db.raw['u']['content'], '_context_message_id': 'u'}
    payload = {'model': 'model', 'messages': [user]}
    output = []
    handoffs = []

    async def append_command(call_id, text):
        call = {'call_id': call_id, 'status': 'completed'}
        batch = [
            {**call, 'type': 'function_call', 'id': 'fc-' + call_id, 'name': 'run_command', 'arguments': '{}'},
            {
                **call,
                'type': 'function_call_output',
                'id': 'out-' + call_id,
                'output': [{'type': 'input_text', 'text': text}],
            },
        ]
        output.extend(batch)
        await edit(db, 'a', output=output)
        messages = convert_output_to_messages(batch, raw=True, flatten_tool_images=True, include_tool_result_refs=True)
        for message in messages:
            message['_context_message_id'] = 'a'
            for ref in message.get('_tool_result_refs', []):
                ref['message_id'] = 'a'
        payload['messages'].extend(messages)
        return messages[-1]['_tool_result_refs'][0]

    async def summarize(req, actor, model, models, messages, previous, settings, *, purpose='history', **kwargs):
        if purpose == 'tool':
            return 'Command completed with exit code 0.'
        handoffs.append(previous)
        return f'Handoff {len(handoffs)}'

    async def prepare(**options):
        return await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}}, **options)

    with (
        patch.object(v2, 'load_stored_models', AsyncMock(return_value={})),
        patch.object(v2, 'generate_summary', side_effect=summarize),
        patch.object(v2, 'emit_event', AsyncMock()) as emit,
    ):
        for call_id in ('one', 'two'):
            await append_command(call_id, f'{call_id} completed. ' * 80)
            await prepare(force=True, admit_tools=False)
        checkpoints = v2.get_checkpoint_store()
        assert await checkpoints.latest_version('chat', 'model') == 2

        await append_command('recent', 'Previous command output. ' * 250)
        ref = await append_command('current', 'Current command output. ' * 500)
        assert await db.selections.register_pending('chat', 'model', [ref], branch_id='a')
        call, result = payload['messages'][-2:]
        prefix = [v2.history_message('Handoff 3'), user, call]
        fixed = v2.estimate_payload_tokens({**payload, 'messages': [*prefix, {**result, 'content': ''}]})
        effective = v2.resolve_budget(settings, 'model', {}, payload).input_budget

        async def send(room):
            payload['max_tokens'] = effective - fixed - room
            original = copy.deepcopy(payload)
            try:
                return await prepare()
            finally:
                assert payload == original

        emit.reset_mock()
        yield SimpleNamespace(
            send=send,
            checkpoints=checkpoints,
            prefix=prefix,
            fixed=fixed,
            text=ref['text'],
            handoffs=handoffs,
            emit=emit,
            v2=v2,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['summary', 'excerpt'])
async def test_run_command_after_two_history_checkpoints(database, checkpointed_commands, method, monkeypatch):
    from open_webui.utils.tool_result_budget import ToolBudgetPlan

    case = checkpointed_commands
    if method == 'excerpt':
        monkeypatch.setattr(ToolBudgetPlan, 'summarize', AsyncMock(side_effect=TimeoutError))
    sent = await case.send(room=120)
    assert sent['messages'][:-1] == case.prefix
    assert case.v2.estimate_payload_tokens(sent) <= case.fixed + 120
    (selected,) = await database.selections.list_selections('chat', 'model', 'a')
    assert selected['state'] == 'prepared' and selected['result_id'] in sent['messages'][-1]['content']
    assert selected['method'] == method
    assert (await database.store.get_result(selected['result_id'], branch_id='a'))['text'] == case.text
    assert await case.send(room=120) == sent
    assert await case.checkpoints.latest_version('chat', 'model') == 3
    assert case.handoffs == ['', 'Handoff 1', 'Handoff 2']


@pytest.mark.asyncio
async def test_run_command_reference_still_must_fit(database, checkpointed_commands, caplog):
    from fastapi import HTTPException

    case = checkpointed_commands
    with pytest.raises(
        HTTPException, match=r'tool result reference \(allocated 0 tokens, reference requires \d+'
    ) as error:
        await case.send(room=0)
    assert error.value.status_code == 400
    (diagnostic,) = [
        record.getMessage() for record in caplog.records if record.name == 'open_webui.utils.tool_result_budget'
    ]
    failure = next(
        call.args[2]
        for call in case.emit.await_args_list
        if call.args[2].get('tool') == 'run_command' and call.args[2].get('error')
    )
    assert 'input_remaining_before_allocation=0' in diagnostic and 'input_space' in diagnostic
    assert f'operation_id={failure["operation_id"]}' in diagnostic
    assert await case.checkpoints.latest_version('chat', 'model') == 2
    (selected,) = await database.selections.list_selections('chat', 'model', 'a')
    assert selected['state'] == 'pending'


@pytest.mark.asyncio
async def test_cancelling_during_checkpoint_notification_preserves_success(database, checkpointed_commands):
    case = checkpointed_commands
    notifying, release = asyncio.Event(), asyncio.Event()
    delivered = []

    async def emit(metadata, kind, data):
        if kind == 'context_compaction' and data['done']:
            notifying.set()
            await release.wait()
            delivered.append(data)

    case.emit.side_effect = emit
    sending = asyncio.create_task(case.send(room=120))
    try:
        await asyncio.wait_for(notifying.wait(), timeout=10)
        sending.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert len(delivered) == 1 and not delivered[0].get('error')
    assert await case.checkpoints.latest_version('chat', 'model') == 3
    (selected,) = await database.selections.list_selections('chat', 'model', 'a')
    assert selected['state'] == 'pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['stale-checkpoint', 'store-unavailable', 'cancelled', 'selection-conflict'])
async def test_run_command_fit_failure_preserves_durable_outcome(database, checkpointed_commands, failure):
    from fastapi import HTTPException
    from open_webui.utils.context_store_errors import ContextUnavailable

    case = checkpointed_commands
    if failure == 'selection-conflict':
        target = patch.object(database.selections, 'prepare_selections', AsyncMock(return_value=None))
        expected = HTTPException
    else:
        expected = {
            'stale-checkpoint': HTTPException,
            'store-unavailable': ContextUnavailable,
            'cancelled': asyncio.CancelledError,
        }[failure]
        target = patch.object(
            case.checkpoints,
            'save_checkpoint',
            AsyncMock(return_value=None, side_effect=None if expected is HTTPException else expected),
        )
    with target, pytest.raises(expected) as error:
        await case.send(room=120)
    if expected is HTTPException:
        assert error.value.status_code == 409
    stored = failure == 'selection-conflict'
    assert await case.checkpoints.latest_version('chat', 'model') == (3 if stored else 2)
    history_events = [call.args[2] for call in case.emit.await_args_list if call.args[1] == 'context_compaction']
    assert [event['done'] for event in history_events] == [False, True]
    assert bool(history_events[-1].get('error')) == (not stored)
    tool_events = [call.args[2] for call in case.emit.await_args_list if call.args[1] == 'tool_result_compaction']
    assert [event['done'] for event in tool_events] == [False, True]
    assert tool_events[-1]['error'] and tool_events[-1]['operation_id'] == tool_events[0]['operation_id']
    (selected,) = await database.selections.list_selections('chat', 'model', 'a')
    assert selected['state'] == 'pending'


@pytest.mark.asyncio
async def test_engine_persists_reuses_and_reads_exact_original(database):
    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    budget_module = importlib.import_module('open_webui.utils.tool_result_budget')
    output = copy.deepcopy(db.raw['a']['output'])
    output[0]['status'] = 'completed'
    output.insert(
        0,
        {
            'id': 'fc',
            'type': 'function_call',
            'call_id': 'call',
            'name': 'search',
            'arguments': '{}',
            'status': 'completed',
        },
    )
    await edit(db, 'a', output=output)
    settings = v2.CompactionSettings(mode='v2', default_context_window=6000, recent_tokens=200, reminder_enabled=False)
    state = dict(
        mode='v2',
        settings=settings,
        chat_id='chat',
        user_message_id='u',
        metadata={'chat_id': 'chat', 'message_id': 'a'},
    )
    request = SimpleNamespace(state=SimpleNamespace(context_compaction=state))
    payload = {
        'model': 'model',
        'messages': [
            {'role': 'user', 'content': 'question', '_context_message_id': 'u'},
            {
                'role': 'assistant',
                '_context_message_id': 'a',
                'tool_calls': [{'id': 'call', 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}}],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call',
                'content': db.text,
                '_context_message_id': 'a',
                '_tool_result_refs': [descriptor(db)],
            },
        ],
    }
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    events = []

    async def emit(meta, kind, data):
        events.append((kind, data))

    with (
        patch.object(v2, 'load_stored_models', AsyncMock(return_value={})),
        patch.object(v2, 'emit_event', emit),
        patch.object(
            v2, 'generate_summary', AsyncMock(return_value='Observed value is 42. Evidence has a NEEDLE passage.')
        ) as generate,
    ):
        first = await v2.prepare_v2_request(
            request, SimpleNamespace(id='owner', role='user'), payload, {}, 'model', {'model': {}}
        )
        assert generate.await_count == 1
        second = await v2.prepare_v2_request(
            request, SimpleNamespace(id='owner', role='user'), payload, {}, 'model', {'model': {}}
        )
        assert generate.await_count == 1
    notices = [data for kind, data in events if kind == 'tool_result_compaction']
    assert len(notices) == 2 and [item['done'] for item in notices] == [False, True]
    assert all(item['source_message_id'] == 'a' for item in notices)
    assert notices[0]['operation_id'] == notices[1]['operation_id']
    assert first == second and payload['messages'][-1]['content'] == db.text
    cached = await db.store.list_contexts('chat', 'model', 'a')
    assert len(cached) == 1
    result_id = cached[0]['result_id']
    assert (await db.store.get_result(result_id, branch_id='a'))['text'] == db.text
    page = await db.module.read_tool_result_page(
        SimpleNamespace(id='owner', role='user'), 'chat', result_id, branch_id='a', query='NEEDLE', max_tokens=300
    )
    assert page['text'].startswith('NEEDLE evidence')
    assert db.text[page['start'] : page['end']] == page['text']
    plan = budget_module.ToolBudgetPlan(
        request,
        None,
        payload,
        db.raw['u'],
        state['metadata'],
        'model',
        {'model': {}},
        settings,
        v2.resolve_budget(settings, 'model', {'model': {}}, payload),
        state,
    )
    replay = await plan.load_cached_only(payload)
    assert replay['messages'][-1]['content'] == first['messages'][-1]['content']
    # Policy changes affect new results only; adopted history remains exact.
    changed = settings.model_copy(update={'tool_summary_prompt': 'Changed strategy'})
    plan = budget_module.ToolBudgetPlan(
        request,
        None,
        payload,
        db.raw['u'],
        state['metadata'],
        'model',
        {'model': {}},
        changed,
        v2.resolve_budget(changed, 'model', {'model': {}}, payload),
        state,
    )
    assert (await plan.load_cached_only(payload))['messages'][-1]['content'] == first['messages'][-1]['content']
    await edit(db, 'u', content='edited request')
    assert await db.store.list_contexts('chat', 'model', 'a') == []


@pytest.mark.asyncio
async def test_read_tool_is_resized_exactly_and_never_summarized(database):
    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    tool = importlib.import_module('open_webui.utils.tool_result_budget')
    record = await db.store.save_result('chat', descriptor(db), branch_id='a')
    owner = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(owner, 'chat', record['id'], branch_id='a', max_tokens=700)
    raw_page = json.dumps(page, ensure_ascii=False, separators=(',', ':'))
    settings = v2.CompactionSettings(mode='v2')
    plan = tool.ToolBudgetPlan(
        None,
        owner,
        {'messages': []},
        db.raw['u'],
        {'message_id': 'a'},
        'model',
        {'model': {}},
        settings,
        v2.resolve_budget(settings, 'model', {'model': {}}, {}),
        {'chat_id': 'chat'},
    )
    plan.store = SimpleNamespace(get_context=AsyncMock(return_value=None))
    entry = tool.ToolEntry(
        ('a', 'read'),
        {'kind': 'read', 'name': 'read_tool_result'},
        raw_page,
        v2.estimate_text(raw_page),
        record={'id': 'read-snapshot'},
    )
    with patch.object(
        v2, 'generate_summary', AsyncMock(side_effect=AssertionError('Exact reading must not summarize'))
    ):
        resized, method = await plan.render(entry, 300)
    result = json.loads(resized)
    assert method == 'read' and v2.estimate_text(resized) <= 300
    assert db.text[result['start'] : result['end']] == result['text']
    following = await db.module.read_tool_result_page(
        owner, 'chat', record['id'], branch_id='a', cursor=result['next_cursor'], max_tokens=300
    )
    assert following['start'] == result['end']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['injected', 'code'])
async def test_partial_cached_spans_survive_finish_and_retry(database, kind):
    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    tool = importlib.import_module('open_webui.utils.tool_result_budget')
    sources = importlib.import_module('open_webui.utils.tool_result_sources')
    sidecars = [
        dict(id='long', call_id='first', batch_id='batch', name='search', kind=kind, text=db.text * 8),
        dict(id='short', call_id='second', batch_id='batch', name='search', kind=kind, text='short evidence'),
    ]
    await edit(db, 'a', output=[], meta={'tool_results': sidecars})
    message = sources.injected_message(sidecars, 'a')
    if kind == 'code':
        prefix = "print('source remains unchanged')\n"
        message['content'] = [{'type': 'text', 'text': prefix + message['content']}]
        for ref in message['_tool_result_refs']:
            ref['start'] += len(prefix)
            ref['end'] += len(prefix)
    payload = {'messages': [message]}
    original = copy.deepcopy(payload)
    settings = v2.CompactionSettings(mode='v2', default_context_window=6000, recent_tokens=200)
    state = {'chat_id': 'chat'}

    def make_plan():
        return tool.ToolBudgetPlan(
            None,
            None,
            payload,
            db.raw['u'],
            {'message_id': 'a'},
            'model',
            {'model': {}},
            settings,
            v2.resolve_budget(settings, 'model', {'model': {}}, payload),
            state,
        )

    plan = make_plan()
    entry = plan.entries[('a', 'long')]
    record = await db.store.save_result('chat', entry.descriptor, branch_id='a')
    cached = await db.store.save_context(
        record['id'],
        model_id='model',
        branch_id='a',
        task_hash=plan.task_hash,
        policy_hash=plan.policy_hash,
        budget=plan.single,
        text='cached long evidence',
        method='summary',
        original_tokens=entry.tokens,
        processed_tokens=v2.estimate_text('cached long evidence'),
    )
    await db.selections.register_pending('chat', 'model', [e.descriptor for e in plan.entries.values()], branch_id='a')
    await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='a',
        choices=[
            {
                'descriptor': entry.descriptor,
                'context_id': cached['id'],
                'method': 'summary',
                'allocation': plan.single,
                'original_tokens': entry.tokens,
                'processed_tokens': v2.estimate_text(cached['text']),
            }
        ],
        batch_reports=[
            {
                'batch_id': 'batch',
                'source_message_id': 'a',
                'complete': True,
                'results': [{'source_message_id': 'a', 'output_id': 'long', 'method': 'summary'}],
            }
        ],
    )
    with patch.object(v2, 'emit_event', AsyncMock()), patch.object(v2, 'generate_summary', AsyncMock()) as generate:
        loaded = await plan.load(payload)
        assert plan.entries[('a', 'long')].rendered == 'cached long evidence'
        assert plan.entries[('a', 'short')].rendered is None
        assert plan.reserve(loaded) <= plan.budget.input_budget
        first = await plan.finish(loaded)
        retry = await plan.finish(first)
        fresh = make_plan()
        replay = await fresh.finish(await fresh.load(payload))
    assert first == retry == replay
    assert payload == original
    generate.assert_not_awaited()
    content = v2.text_content(first['messages'][0])
    assert 'cached long evidence' in content and 'short evidence' in content
    assert v2.message_key(first['messages'][0]) == v2.message_key(message)
    for ref in first['messages'][0]['_tool_result_refs']:
        assert content[ref['start'] : ref['end']] == ref.get('_rendered', ref['text'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,name', [('read', 'reader_alias'), ('native', 'read_tool_result')])
async def test_under_budget_exact_read_finish_retains_page_without_notification(database, kind, name):
    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    tool = importlib.import_module('open_webui.utils.tool_result_budget')
    record = await db.store.save_result('chat', descriptor(db), branch_id='a')
    owner = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(owner, 'chat', record['id'], branch_id='a', max_tokens=300)
    text = json.dumps(page, ensure_ascii=False, separators=(',', ':'))
    ref = dict(
        message_id='a', output_id='read', call_id='read-call', batch_id='read-batch', name=name, kind=kind, text=text
    )
    payload = {'messages': [{'role': 'tool', 'tool_call_id': 'read-call', 'content': text, '_tool_result_refs': [ref]}]}
    output = copy.deepcopy(db.raw['a']['output'])
    output.append(
        {
            'id': 'read',
            'type': 'function_call_output',
            'call_id': 'read-call',
            'output': text,
            'tool_result': {key: ref[key] for key in ('name', 'kind', 'batch_id')},
        }
    )
    await edit(db, 'a', output=output)
    await db.selections.register_pending('chat', 'model', [ref], branch_id='a')
    settings = v2.CompactionSettings(mode='v2', default_context_window=100000)
    store = SimpleNamespace(list_contexts=AsyncMock(return_value=[]), save_result=AsyncMock(), save_context=AsyncMock())
    events = []

    async def emit(metadata, event_kind, event):
        events.append((event_kind, copy.deepcopy(event)))

    with (
        patch.object(tool, 'get_tool_result_store', return_value=store),
        patch.object(v2, 'emit_event', emit),
        patch.object(
            v2,
            'generate_summary',
            AsyncMock(side_effect=AssertionError('Exact reads must not summarize')),
        ),
        patch.object(db.module, 'resize_tool_result_page', AsyncMock(side_effect=AssertionError('No resize needed'))),
    ):
        plan = tool.ToolBudgetPlan(
            None,
            owner,
            payload,
            db.raw['u'],
            {'chat_id': 'chat', 'message_id': 'a'},
            'model',
            {'model': {}},
            settings,
            v2.resolve_budget(settings, 'model', {'model': {}}, payload),
            {'chat_id': 'chat'},
        )
        result = await plan.finish(await plan.load(payload))
        retry = await plan.finish(result)
    assert result['messages'][0]['content'] == text == retry['messages'][0]['content']
    assert events == []
    assert plan.last_batch['results'][0]['method'] == 'read'
    assert db.text[page['start'] : page['end']] == page['text']
    store.save_result.assert_not_awaited()
    store.save_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'provider,explicit_cap,setting_cap',
    [('openai', None, None), ('openai', 321, None), ('openai', 777, 321), ('ollama', None, None)],
)
async def test_smaller_summary_model_sends_bounded_progressive_chunks(database, provider, explicit_cap, setting_cap):
    import sys
    from types import ModuleType

    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    tool = importlib.import_module('open_webui.utils.tool_result_budget')
    text = db.text * 50
    ref = {**descriptor(db), 'text': text}
    payload = {'messages': [{'role': 'tool', 'tool_call_id': 'call', 'content': text, '_tool_result_refs': [ref]}]}
    settings = v2.CompactionSettings(mode='v2', summary_model='summary', summary_max_output_tokens=setting_cap)
    params = {'num_ctx': 6000} if provider == 'ollama' else {}
    if explicit_cap:
        params['max_completion_tokens'] = explicit_cap
    models = {
        'target': {'info': {'meta': {'context_window': 100000}}},
        'summary': {'owned_by': provider, 'info': {'meta': {'context_window': 8000}, 'params': params}},
    }
    request = SimpleNamespace(state=SimpleNamespace(metadata={}))
    chat_module, payload_module = ModuleType('open_webui.utils.chat'), ModuleType('open_webui.utils.payload')
    calls = []

    async def generate(req, form_data, user, **kwargs):
        calls.append(copy.deepcopy(form_data))
        resolved = v2.resolve_budget(settings, 'summary', models, form_data)
        assert 0 < resolved.output_reserve < resolved.effective_context_window
        assert v2.estimate_payload_tokens(form_data) <= resolved.input_budget
        expected = max(
            resolved.context_window * settings.summary_output_percent // 100, setting_cap or explicit_cap or 0
        )
        assert resolved.output_reserve == expected
        summary = tool._prefix('Observed value is 42. NEEDLE evidence. ' * 2000, resolved.output_reserve)
        return {'choices': [{'message': {'content': summary}}]}

    chat_module.generate_chat_completion = generate

    def apply_params(body, model, params):
        # Match the real helper: explicit body/options values win defaults.
        params = {key: value for key, value in params.items() if key != 'system'}
        if model.get('owned_by') == 'ollama':
            body['options'] = {**params, **body.get('options', {})}
        else:
            for key, value in params.items():
                body.setdefault(key, value)
        return body

    payload_module.apply_params_to_form_data = apply_params
    payload_module.resolve_system_prompt = AsyncMock(return_value='Summary model fixed instructions.')
    with (
        patch.dict(sys.modules, {'open_webui.utils.chat': chat_module, 'open_webui.utils.payload': payload_module}),
        patch.object(
            v2,
            'load_stored_models',
            AsyncMock(return_value={}),
        ),
        patch.object(
            tool, 'get_tool_result_store', return_value=SimpleNamespace(get_context=AsyncMock(return_value=None))
        ),
    ):
        plan = tool.ToolBudgetPlan(
            request,
            None,
            payload,
            db.raw['u'],
            {'message_id': 'a'},
            'target',
            models,
            settings,
            v2.resolve_budget(settings, 'target', models, payload),
            {'chat_id': 'chat'},
        )
        entry = plan.entries[('a', 'out')]
        entry.record = {'id': 'summary-result'}
        rendered, method = await plan.render(entry, 10000)
    assert method == 'summary'
    assert len(calls) > 1
    assert v2.estimate_text(rendered) <= 10000
    fragments = ''.join(
        body['messages'][-1]['content'].split('Conversation data fragment (may continue):\n', 1)[1] for body in calls
    )
    transcript = json.loads(fragments)
    assert transcript['content'].endswith(text)


@pytest.mark.asyncio
@pytest.mark.parametrize('with_new', [False, True])
async def test_source_edit_during_preparation_rejects_stale_selected_history(database, with_new):
    db = database
    from fastapi import HTTPException
    from test_tool_result_selections import candidate, choice, report
    from open_webui.utils import context_compaction_v2 as v2
    from open_webui.utils import tool_result_budget as tool
    from open_webui.utils.tool_result_sources import output_descriptors

    context = await candidate(db, branch='a')
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    assert await db.selections.prepare_selections(
        'chat', 'model', branch_id='a', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    if with_new:
        extra = {
            'id': 'next',
            'call_id': 'next-call',
            'type': 'function_call_output',
            'output': db.text * 8,
            'tool_result': {'batch_id': 'next-batch', 'name': 'search', 'kind': 'native'},
        }
        await edit(db, 'a', output=[*db.raw['a']['output'], extra])
        ref = {**output_descriptors(db.raw['a']['output'])['next'], 'message_id': 'a'}
        await db.selections.register_pending('chat', 'model', [ref], branch_id='a')
    refs = [{**ref, 'message_id': 'a'} for ref in output_descriptors(db.raw['a']['output']).values()]
    payload = {
        'messages': [
            dict(
                role='tool',
                tool_call_id=ref['call_id'],
                content=ref['text'],
                _context_message_id='a',
                _tool_result_refs=[ref],
            )
            for ref in refs
        ]
    }
    settings = v2.CompactionSettings(mode='v2', default_context_window=6000, recent_tokens=200)
    plan = tool.ToolBudgetPlan(
        None,
        None,
        payload,
        db.raw['u'],
        {'message_id': 'a'},
        'model',
        {'model': {}},
        settings,
        v2.resolve_budget(settings, 'model', {'model': {}}, payload),
        {'chat_id': 'chat'},
    )
    loaded = await plan.load(payload)
    changed_output = copy.deepcopy(db.raw['a']['output'])
    changed_output[0]['output'] = 'edited earlier tool output'
    if with_new:

        async def summarize(*args, **kwargs):
            await edit(db, 'a', output=changed_output)
            return 'new tool summary'

        generation = patch.object(plan, 'summarize', side_effect=summarize)
    else:
        await edit(db, 'a', output=changed_output)
        generation = patch.object(plan, 'summarize', AsyncMock())
    with generation, patch.object(v2, 'emit_event', AsyncMock()):
        with pytest.raises(HTTPException) as error:
            await plan.finish(loaded)
    assert error.value.status_code == 409
    if with_new:
        rows = await db.selections.list_selections('chat', 'model', 'a')
        assert next(row for row in rows if row['output_id'] == 'next')['state'] == 'pending'
