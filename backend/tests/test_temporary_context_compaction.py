"""The real V2 algorithm uses temporary ports and preserves event scope."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from test_temporary_context_adapters import context, resolve
from open_webui.utils import context_compaction_v2 as v2, tool_result_budget as budget
from open_webui.utils.temporary_contexts import scope_metadata
from open_webui.utils.tool_result_store import read_tool_result_page
from open_webui.utils.context_compaction_config import CompactionSettings


@pytest.mark.asyncio
async def test_exact_temporary_raw_read_uses_cache(context):
    ctx = context
    desc = await resolve(ctx, text='exact evidence ' * 100)
    row = await ctx.store.save_result(ctx.scope.store_key, desc, branch_id='a')
    page = await read_tool_result_page(
        SimpleNamespace(id='owner', role='user'),
        ctx.scope.store_key,
        row['id'],
        branch_id='a',
        max_tokens=512,
        context_store=ctx.store.bundle(),
    )
    assert page['text'] and page['text'] in desc['text']


@pytest.mark.asyncio
async def test_real_manual_compaction_preserves_scope_raw_and_sql_exclusion(context):
    ctx = context
    raw = [
        {'id': 'old', 'parentId': None, 'role': 'user', 'content': 'old fact ' * 1000},
        {'id': 'old-a', 'parentId': 'old', 'role': 'assistant', 'model': 'model', 'content': 'answer ' * 100},
        {'id': 'u', 'parentId': 'old-a', 'role': 'user', 'content': 'current task'},
        {'id': 'a', 'parentId': 'u', 'role': 'assistant', 'model': 'model', 'content': ''},
    ]

    def seed(doc, now):
        doc['data']['snapshot']['messages_map'] = {m['id']: m for m in raw}

    args = (ctx.doc['id'], 'owner', ctx.doc['epoch'])
    await ctx.repo.transact(*args, seed, source_changed=True)
    before = copy.deepcopy((await ctx.repo.read(*args))['data']['snapshot'])
    meta = dict(
        user_id='owner',
        session_id='socket',
        chat_id=ctx.scope.store_key,
        message_id='a',
        task_id='task',
        temporary_context=scope_metadata(ctx.scope),
    )
    req = SimpleNamespace(state=SimpleNamespace(context_store=ctx.store.bundle(), metadata=meta))
    cfg = CompactionSettings(mode='v2', default_context_window=1800, recent_tokens=10, reminder_enabled=False)

    async def history(snapshot, model_id, models):
        records = v2.branch_messages(snapshot['messages_map'], snapshot['current_id'])
        return {'model': model_id, 'messages': [{**m, '_context_message_id': m['id']} for m in records]}, records

    emitter = AsyncMock()
    with (
        patch.object(v2, 'emit_event', emitter),
        patch.object(v2, 'load_compaction_settings', AsyncMock(return_value=cfg)),
        patch.object(v2, 'load_stored_models', AsyncMock(return_value={})),
        patch.object(v2, '_history_payload', history),
        patch.object(v2, 'generate_summary', AsyncMock(return_value='Prior facts and decisions.')),
        patch.object(v2, 'get_checkpoint_store', side_effect=AssertionError('SQL checkpoint access')),
        patch.object(budget, 'get_tool_selection_store', side_effect=AssertionError('SQL selection access')),
        patch.object(budget, 'get_tool_result_store', side_effect=AssertionError('SQL result access')),
    ):
        usage = await v2.get_v2_chat_context_usage(
            SimpleNamespace(id=ctx.scope.store_key), 'model', {'model': {}}, context_store=ctx.store.bundle()
        )
        assert usage is not None
        result = await v2.compact_v2_chat_branch(
            req,
            SimpleNamespace(id='owner'),
            SimpleNamespace(id=ctx.scope.store_key),
            'model',
            {'model': {}},
            context_store=ctx.store.bundle(),
        )
    state = await ctx.repo.read(*args)
    assert result['compacted'] and state['data']['checkpoints']
    assert state['data']['snapshot'] == before
    assert emitter.await_count == 3
    for call in emitter.await_args_list:
        assert call.args[0] == meta
