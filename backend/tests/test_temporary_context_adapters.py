"""Temporary ports execute the same V2 source and selection rules without SQL."""

import copy
from types import SimpleNamespace

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def context():
    from open_webui.utils.context_scope import ContextScope
    from open_webui.utils.context_store_temporary import TemporaryContextAdapter
    from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository

    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')
    messages = {
        'u': {'id': 'u', 'role': 'user', 'parentId': None, 'content': 'question'},
        'a': {'id': 'a', 'role': 'assistant', 'parentId': 'u', 'content': '', 'model': 'model'},
        'leaf': {'id': 'leaf', 'role': 'user', 'parentId': 'a', 'content': 'next'},
    }

    def seed(current, now):
        current['data'] = {
            'snapshot': {
                'messages_map': messages,
                'current_id': 'leaf',
                'owner_id': 'owner',
                'shared_history': False,
                'chat': {},
            }
        }

    doc, _ = await repo.transact(doc['id'], 'owner', doc['epoch'], seed, source_changed=True)
    doc, _ = await repo.register_tasks(
        doc['id'],
        'owner',
        doc['epoch'],
        [
            dict(
                task_id='task',
                reply_id='a',
                model_id='model',
                branch_id='u',
                worker_nonce='worker',
            )
        ],
        expected_revision=doc['revision'],
    )
    scope = ContextScope(
        kind='temporary',
        context_id=doc['id'],
        actor_id='owner',
        epoch=doc['epoch'],
        revision=doc['revision'],
        branch_id='a',
        reply_id='a',
        model_id='model',
        task_id='task',
        execution_fence=doc['fence'],
    )
    store = TemporaryContextAdapter(repo, scope)
    yield SimpleNamespace(repo=repo, doc=doc, scope=scope, store=store)


async def resolve(ctx, *, text='原始 evidence ' * 100):
    execution, started = await ctx.repo.begin_execution(
        ctx.doc['id'],
        'owner',
        ctx.doc['epoch'],
        task_id='task',
        identity={'round_id': 'round', 'ordinal': 0, 'call_id': 'call', 'reply_id': 'a', 'model_id': 'model'},
        target_hash='target',
    )
    assert started
    await ctx.store.save_execution_result(execution['id'], {'content': text})
    output = [
        {'type': 'function_call', 'id': 'call', 'call_id': 'call', 'name': 'search', 'arguments': '{}'},
        {
            'type': 'function_call_output',
            'id': 'out',
            'call_id': 'call',
            'output': [{'type': 'input_text', 'text': text}],
            'tool_result': {'batch_id': 'batch', 'name': 'search', 'kind': 'native'},
        },
    ]
    await ctx.store.commit_raw_results('a', {'out': execution['id']}, output, [])
    return dict(
        message_id='a', output_id='out', call_id='call', batch_id='batch', name='search', kind='native', text=text
    )


@pytest.mark.asyncio
async def test_client_history_cannot_admit_new_results_and_resolved_execution_can(context):
    ctx = context
    forged = dict(
        message_id='a', output_id='out', call_id='call', batch_id='batch', name='search', kind='native', text='forged'
    )
    assert not await ctx.store.register_pending(ctx.scope.store_key, 'model', [forged], branch_id='a')
    desc = await resolve(ctx)
    assert await ctx.store.register_pending(ctx.scope.store_key, 'model', [desc], branch_id='a')
    pending = await ctx.store.list_selections(ctx.scope.store_key, 'model', 'leaf')
    assert len(pending) == 1 and pending[0]['state'] == 'pending'
    assert pending[0]['text'] == desc['text']
    assert await ctx.store.list_selections(ctx.scope.store_key, 'other-model', 'leaf') == []
    doc = await ctx.repo.read(ctx.doc['id'], 'owner', ctx.doc['epoch'])
    assert doc['data'].get('resolved_outputs', {}) == {}


@pytest.mark.asyncio
async def test_selected_version_replay_and_accounting_are_fixed(context):
    ctx = context
    desc = await resolve(ctx)
    assert await ctx.store.register_pending(ctx.scope.store_key, 'model', [desc], branch_id='a')
    raw = await ctx.store.save_result(ctx.scope.store_key, desc, branch_id='a')
    cached = await ctx.store.save_context(
        raw['id'],
        model_id='model',
        branch_id='a',
        task_hash='task',
        policy_hash='policy',
        budget=100,
        text='summary',
        method='summary',
        original_tokens=1000,
        processed_tokens=10,
    )
    chosen = dict(
        descriptor=desc,
        context_id=cached['id'],
        method='summary',
        allocation=100,
        original_tokens=1000,
        processed_tokens=10,
    )
    report = dict(
        batch_id='batch',
        source_message_id='a',
        complete=True,
        decision_id='decision',
        results=[dict(message_id='a', output_id='out')],
    )
    prepared = await ctx.store.prepare_selections(
        ctx.scope.store_key,
        'model',
        branch_id='a',
        choices=[chosen],
        batch_reports=[report],
        validate=lambda rows, fresh: len(fresh) == 1,
    )
    assert prepared[0]['text'] == 'summary'
    replay = await ctx.store.list_selections(ctx.scope.store_key, 'model', 'leaf')
    assert replay == prepared
    account = await ctx.store.latest_accounting(ctx.scope.store_key, 'model', 'leaf')
    assert account['last_tool_batch']['complete'] is True
    assert account['last_tool_batch']['decision_id'] == 'decision'
    assert account['last_tool_batch']['processed_tokens'] == 10
    before = await ctx.repo.read(ctx.doc['id'], 'owner', ctx.doc['epoch'])
    await ctx.store.list_selections(ctx.scope.store_key, 'model', 'leaf')
    assert (await ctx.repo.read(ctx.doc['id'], 'owner', ctx.doc['epoch'])) == before


@pytest.mark.asyncio
async def test_source_edit_invalidates_raw_and_selection_without_deleting_original(context):
    ctx = context
    desc = await resolve(ctx, text='')
    assert await ctx.store.register_pending(ctx.scope.store_key, 'model', [desc], branch_id='a')
    raw = await ctx.store.save_result(ctx.scope.store_key, desc, branch_id='a')
    assert (await ctx.store.get_result(raw['id'], branch_id='leaf'))['text'] == ''

    def edit(doc, now):
        doc['data']['snapshot']['messages_map']['u']['content'] = 'edited'

    await ctx.repo.transact(ctx.doc['id'], 'owner', ctx.doc['epoch'], edit, source_changed=True)
    assert await ctx.store.get_result(raw['id'], branch_id='leaf') is None
    assert await ctx.store.list_selections(ctx.scope.store_key, 'model', 'leaf') == []
    doc = await ctx.repo.read(ctx.doc['id'], 'owner', ctx.doc['epoch'])
    assert doc['data']['results'][raw['id']]['text'] == ''


@pytest.mark.asyncio
async def test_scope_binding_prevents_foreign_store_and_fenced_writes(context):
    from open_webui.utils.context_store_errors import ContextGone, ContextNotFound

    ctx = context
    with pytest.raises(ContextNotFound):
        await ctx.store.read_snapshot('another-chat')
    await ctx.repo.close(ctx.doc['id'], 'owner', ctx.doc['epoch'])
    with pytest.raises(ContextGone):
        await ctx.store.save_result(ctx.scope.store_key, {}, branch_id='a')


@pytest.mark.asyncio
async def test_v2_request_uses_explicit_temporary_ports_without_sql(context, monkeypatch):
    from unittest.mock import AsyncMock
    from open_webui.utils import context_compaction_v2 as v2
    from open_webui.utils.context_compaction_config import CompactionSettings

    ctx = context
    settings = CompactionSettings(mode='v2', default_context_window=16000, recent_tokens=1000)
    state = {
        'mode': 'v2',
        'settings': settings,
        'chat_id': ctx.scope.store_key,
        'user_message_id': 'u',
        'model_id': 'model',
    }
    request = SimpleNamespace(state=SimpleNamespace(context_compaction=state, context_store=ctx.store.bundle()))
    models = {'model': {'id': 'model'}}
    monkeypatch.setattr(v2, 'load_stored_models', AsyncMock(return_value={}))
    monkeypatch.setattr(v2, 'emit_event', AsyncMock())
    monkeypatch.setattr(v2, 'get_checkpoint_store', lambda: pytest.fail('temporary request opened SQL store'))
    payload = {'model': 'model', 'messages': [{'role': 'user', 'content': 'question', '_context_message_id': 'u'}]}
    result = await v2.prepare_v2_request(
        request, SimpleNamespace(id='owner'), payload, {'message_id': 'a'}, 'model', models
    )
    assert result['messages'][0]['content'] == 'question'
    assert state['last_usage']['mode'] == 'v2'
    assert state['last_usage']['model'] == 'model'


@pytest.mark.asyncio
async def test_exact_temporary_reader_validates_scope_source_and_unicode_cursor(context, monkeypatch):
    import sys
    from types import ModuleType
    from open_webui.utils import tool_result_store as pages

    ctx = context
    desc = await resolve(ctx)
    raw = await ctx.store.save_result(ctx.scope.store_key, desc, branch_id='a')
    env = ModuleType('open_webui.env')
    env.WEBUI_SECRET_KEY = 'synthetic-context-cursor'
    monkeypatch.setitem(sys.modules, 'open_webui.env', env)
    monkeypatch.setattr(pages, 'get_tool_result_store', lambda: pytest.fail('temporary reader opened SQL store'))
    first = await pages.read_tool_result_page(
        SimpleNamespace(id='owner', role='user'),
        ctx.scope.store_key,
        raw['id'],
        branch_id='a',
        max_tokens=256,
        context_store=ctx.store.bundle(),
    )
    assert first['text'] == desc['text'][first['start'] : first['end']]
    second = await pages.read_tool_result_page(
        SimpleNamespace(id='owner', role='user'),
        ctx.scope.store_key,
        raw['id'],
        branch_id='leaf',
        cursor=first['next_cursor'],
        max_tokens=256,
        context_store=ctx.store.bundle(),
    )
    assert second['start'] == first['end']
    assert second['text'] == desc['text'][second['start'] : second['end']]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as denied:
        await pages.read_tool_result_page(
            SimpleNamespace(id='other', role='admin'),
            ctx.scope.store_key,
            raw['id'],
            branch_id='a',
            context_store=ctx.store.bundle(),
        )
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_retirement_preserves_canonical_raw_selections_and_rejects_old_scope(context):
    from dataclasses import replace
    from open_webui.utils.context_store_errors import ContextConflict
    from open_webui.utils.context_store_temporary import TemporaryContextAdapter
    from open_webui.utils.temporary_context_runtime import valid_temporary_rpc

    ctx = context
    desc = await resolve(ctx, text='unchanged original')
    assert await ctx.store.register_pending(ctx.scope.store_key, 'model', [desc], branch_id='a')
    raw = await ctx.store.save_result(ctx.scope.store_key, desc, branch_id='a')
    prepared = await ctx.store.prepare_selections(
        ctx.scope.store_key,
        'model',
        branch_id='a',
        choices=[
            dict(
                descriptor=desc,
                context_id=None,
                method='original',
                allocation=20,
                original_tokens=20,
                processed_tokens=20,
            )
        ],
        batch_reports=[
            dict(
                batch_id='batch',
                source_message_id='a',
                complete=True,
                decision_id='retirement-decision',
                results=[dict(message_id='a', output_id='out')],
            )
        ],
        validate=lambda rows, fresh: len(fresh) == 1,
    )
    assert prepared is not None and len(prepared) == 1
    assert prepared[0]['state'] == 'prepared'
    assert prepared[0]['text'] == 'unchanged original'
    assert prepared[0]['recorded_at'] > 0
    assert prepared[0]['decision_id'] == 'retirement-decision'
    accounting = await ctx.store.latest_accounting(ctx.scope.store_key, 'model', 'leaf')
    assert accounting['last_tool_batch']['complete'] is True
    assert accounting['last_tool_batch']['decision_id'] == prepared[0]['decision_id']
    assert accounting['recorded_at'] == accounting['last_tool_batch']['recorded_at'] == prepared[0]['recorded_at']
    assert accounting['last_tool_batch']['original_tokens'] == 20
    assert accounting['last_tool_batch']['processed_tokens'] == 20
    args = (ctx.doc['id'], 'owner', ctx.doc['epoch'])
    await ctx.repo.acknowledge_task_done(*args, task_id='task')
    before = await ctx.repo.read(*args)
    current, _ = await ctx.repo.register_tasks(
        *args,
        [dict(task_id='new', reply_id='new-reply', model_id='model', branch_id='leaf', worker_nonce='new-worker')],
        expected_revision=before['revision'],
    )
    assert 'task' not in current['tasks'] and not current['executions']
    assert {k: v for k, v in current['data'].items() if k != 'retired_tasks'} == before['data']
    reader = TemporaryContextAdapter(ctx.repo, replace(ctx.scope, task_id=None, execution_fence=None))
    assert (await reader.get_result(raw['id'], branch_id='leaf'))['text'] == 'unchanged original'
    assert await reader.list_selections(ctx.scope.store_key, 'model', 'leaf') == prepared
    assert await reader.latest_accounting(ctx.scope.store_key, 'model', 'leaf') == accounting
    assert (await reader.read_snapshot())['messages_map']['a']['output'][1]['output'][0]['text'] == 'unchanged original'
    with pytest.raises(ContextConflict):
        await ctx.store.ensure_active()
    assert not valid_temporary_rpc(current, {'task_id': 'task'}, 0)
    execution_id = next(iter(before['executions']))
    with pytest.raises(ContextConflict):
        await ctx.store.execution_result(execution_id)
    with pytest.raises(ContextConflict):
        await ctx.repo.publish_execution_results(
            *args,
            task_id='task',
            execution_ids=[execution_id],
            expected_fence=0,
            raw_transition=lambda d, now: d['data'].clear(),
        )
    with pytest.raises(ContextConflict):
        await ctx.repo.mark_uncertain(*args, task_id='task', execution_id=execution_id)
    with pytest.raises(ContextConflict):
        await ctx.repo.acknowledge_task_done(*args, task_id='unknown')
    assert await ctx.repo.read(*args) == current
