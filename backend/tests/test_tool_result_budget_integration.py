"""Exercise projections, source storage, replay and exact rereading together."""

import copy
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from test_tool_result_storage import database, edit, descriptor


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
