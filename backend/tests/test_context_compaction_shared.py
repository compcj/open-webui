"""Shared V2 source provenance, exact saved inputs and immutable fallback selections."""

import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_tool_result_storage import database, descriptor
from test_tool_result_selections import candidate, choice, report
from test_context_compaction_summary_safety import summary_runtime
from open_webui.utils import context_compaction_v2 as v2


def test_shared_checkpoint_requires_provenance_and_voice_edits_invalidate_sources():
    assert v2.checkpoint_usable({}, {'shared_history': False})
    assert not v2.checkpoint_usable({}, {'shared_history': True})
    assert v2.checkpoint_usable({'source_scope': 'saved_history_v1'}, {'shared_history': True})
    raw = {'id': 'a', 'role': 'assistant', 'content': 'saved', 'meta': {'voice': {'speech': [{'transcript': 'said'}]}}}
    refs = v2.source_refs([raw])
    changed = copy.deepcopy(raw)
    changed['meta']['voice']['speech'][0]['transcript'] = 'edited'
    assert not v2.source_refs_match(refs, {'a': changed})


def test_shared_summary_input_uses_saved_prefix_and_never_request_context():
    raw = [
        {'id': 'u', 'role': 'user', 'content': 'shared question', 'params': {'system': 'PRIVATE'}},
        {
            'id': 'a',
            'parentId': 'u',
            'role': 'assistant',
            'content': 'growing aggregate',
            'output': [
                {'id': 'o1', 'type': 'message', 'status': 'completed', 'content': 'saved answer'},
                {'id': 'o2', 'type': 'message', 'status': 'in_progress', 'content': 'uncovered'},
            ],
        },
    ]
    refs = v2.source_refs(raw)
    result = v2.saved_summary_messages(raw, refs)
    serialized = str(v2.summary_units(result))
    assert 'shared question' in serialized and 'saved answer' in serialized
    assert 'PRIVATE' not in serialized and 'uncovered' not in serialized and 'growing aggregate' not in serialized
    assert raw[1]['output'][1]['content'] == 'uncovered'


@pytest.mark.asyncio
async def test_shared_summary_omits_actor_system_variables_and_metadata(summary_runtime):
    seen = []
    req = SimpleNamespace(
        state=SimpleNamespace(
            metadata={'variables': {'secret': 'PRIVATE'}, 'chat_id': 'chat', 'session_id': 'actor-session'},
            context_compaction={'shared_history': True},
        )
    )
    models = {'summary': {'info': {'meta': {'context_window': 10000}, 'params': {'system': 'PRIVATE {{secret}}'}}}}

    async def send(request, form_data, **kwargs):
        seen.append((request.state.metadata, form_data))
        return {'choices': [{'message': {'content': 'safe summary'}}]}

    summary_runtime.generate_chat_completion = send
    await v2.generate_summary(
        req,
        None,
        'summary',
        models,
        [{'role': 'user', 'content': 'saved question'}],
        '',
        v2.CompactionSettings(recent_tokens=10),
    )
    assert len(seen) == 1
    assert 'PRIVATE' not in str(seen) and 'variables' not in str(seen)
    assert seen[0][0].get('session_id') == 'actor-session'
    sys.modules['open_webui.utils.payload'].resolve_system_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_selected_projection_replays_raw_when_chat_is_shared_without_rewriting(database, monkeypatch):
    db = database
    context = await candidate(db, 'private old summary')
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    selected = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    original_snapshot = db.selection_model.read_snapshot_in_session

    async def shared_snapshot(*args, **kwargs):
        return {**await original_snapshot(*args, **kwargs), 'shared_history': True}

    monkeypatch.setattr(db.selection_model, 'read_snapshot_in_session', shared_snapshot)
    replay = await db.selections.list_selections('chat', 'model', 'leaf')
    assert replay[0]['text'] == db.text and replay[0]['method'] == 'original'
    assert replay[0]['id'] == selected[0]['id'] and replay[0]['state'] == 'prepared'
    assert replay[0]['context_id'] is None
    assert await db.selections.latest_accounting('chat', 'model', 'leaf') is None
    async with db.sessions() as session:
        stored = await session.get(db.selection_model.ChatToolResultSelection, selected[0]['id'])
        assert stored.context_id == context['id'] and stored.method == 'summary'


@pytest.mark.asyncio
async def test_marked_shared_selection_replays_for_all_participants(database, monkeypatch):
    db = database
    raw = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    context = await db.store.save_context(
        raw['id'],
        model_id='model',
        branch_id='leaf',
        task_hash='task',
        policy_hash='shared',
        budget=100,
        text='shared saved facts',
        method='summary',
        original_tokens=1000,
        processed_tokens=10,
        source_scope='saved_history_v1',
    )
    assert context['source_scope'] == 'saved_history_v1'
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    snapshot = db.selection_model.read_snapshot_in_session

    async def shared_snapshot(*args, **kwargs):
        return {**await snapshot(*args, **kwargs), 'shared_history': True}

    monkeypatch.setattr(db.selection_model, 'read_snapshot_in_session', shared_snapshot)
    selected = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    assert selected[0]['text'] == 'shared saved facts'
    assert (await db.selections.list_selections('chat', 'model', 'leaf'))[0]['id'] == selected[0]['id']


@pytest.mark.asyncio
async def test_shared_context_listing_excludes_legacy_private_summaries(database, monkeypatch):
    db = database
    await candidate(db, 'private old summary')
    assert len(await db.store.list_contexts('chat', 'model', 'leaf')) == 1
    original_snapshot = db.model.read_snapshot_in_session

    async def shared_snapshot(*args, **kwargs):
        return {**await original_snapshot(*args, **kwargs), 'shared_history': True}

    monkeypatch.setattr(db.model, 'read_snapshot_in_session', shared_snapshot)
    assert await db.store.list_contexts('chat', 'model', 'leaf') == []
