"""Immutable admitted tool selections, with isolated source and transaction validation."""

import asyncio
import copy

import pytest
from sqlalchemy import select
from test_tool_result_storage import database, descriptor, edit


async def candidate(db, text='summary', *, branch='leaf', model='model', policy='policy'):
    raw = await db.store.save_result('chat', descriptor(db), branch_id=branch)
    return await db.store.save_context(
        raw['id'],
        model_id=model,
        branch_id=branch,
        task_hash='task',
        policy_hash=policy,
        budget=100,
        text=text,
        method='summary',
        original_tokens=1000,
        processed_tokens=10,
    )


def choice(db, context=None):
    return dict(
        descriptor=descriptor(db),
        context_id=context['id'] if context else None,
        method='summary' if context else 'original',
        allocation=100 if context else 1000,
        original_tokens=1000,
        processed_tokens=10 if context else 1000,
    )


def report(db, decision='decision', *, method='summary', processed=10):
    return dict(
        batch_id='batch',
        source_message_id='a',
        decision_id=decision,
        recorded_at=123,
        complete=True,
        limits_at_decision={'individual': 100},
        original_tokens=1000,
        processed_tokens=processed,
        results=[
            dict(message_id='a', output_id='out', original_tokens=1000, processed_tokens=processed, method=method)
        ],
    )


@pytest.mark.asyncio
async def test_pending_requires_persisted_sources_and_registration_is_atomic(database):
    db = database
    invalid = {**descriptor(db), 'text': 'forged'}
    assert not await db.selections.register_pending('chat', 'model', [descriptor(db), invalid], branch_id='a')
    assert await db.selections.list_selections('chat', 'model', 'leaf') == []
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    pending = await db.selections.list_selections('chat', 'model', 'leaf')
    assert len(pending) == 1 and pending[0]['state'] == 'pending' and pending[0]['text'] == db.text
    assert await db.selections.latest_accounting('chat', 'model', 'leaf') is None


@pytest.mark.asyncio
async def test_prepared_selection_replays_on_descendants_independent_of_policy(database):
    db = database
    context = await candidate(db)
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[]
        )
        is None
    )
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    frozen = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    assert frozen[0]['text'] == 'summary' and frozen[0]['context_id'] == context['id']
    await edit(db, 'later', role='user', content='changed task', parentId='leaf')
    replay = await db.selections.list_selections('chat', 'model', 'later')
    assert replay[0]['text'] == 'summary'
    alternative = await candidate(db, 'different summary', branch='later', policy='different')
    again = await db.selections.prepare_selections(
        'chat', 'model', branch_id='later', choices=[choice(db, alternative)], batch_reports=[report(db, 'loser')]
    )
    assert again[0]['id'] == frozen[0]['id'] and again[0]['text'] == 'summary'
    assert await db.selections.list_selections('chat', 'other', 'later') == []
    await edit(db, 'sister', role='user', content='other task', parentId='a')
    assert (await db.selections.list_selections('chat', 'model', 'sister'))[0]['state'] == 'pending'
    assert await db.selections.latest_accounting('chat', 'model', 'sister') is None
    accounting = await db.selections.latest_accounting('chat', 'model', 'later')
    assert accounting['last_tool_batch']['decision_id'] == 'decision'
    assert accounting['recorded_at'] == frozen[0]['recorded_at']
    await edit(
        db,
        'a',
        content='finished',
        output=[*db.raw['a']['output'], {'id': 'answer', 'type': 'message', 'content': 'done'}],
    )
    assert (await db.selections.list_selections('chat', 'model', 'later'))[0]['text'] == 'summary'
    await edit(db, 'u', content='edited ancestor')
    assert await db.selections.list_selections('chat', 'model', 'later') == []
    assert await db.selections.latest_accounting('chat', 'model', 'later') is None


@pytest.mark.asyncio
async def test_legacy_original_baseline_has_no_duplicated_raw_text(database):
    db = database
    selected = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='leaf',
        choices=[choice(db)],
        batch_reports=[report(db, method='original', processed=1000)],
    )
    assert selected[0]['text'] == db.text and selected[0]['context_id'] is None
    columns = db.selection_model.ChatToolResultSelection.__table__.columns.keys()
    assert 'text' not in columns
    async with db.sessions() as session:
        assert list((await session.execute(select(db.model.ChatToolResult))).scalars()) == []
    context = await candidate(db)
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='leaf')
    winner = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[]
    )
    assert winner[0]['method'] == 'original' and winner[0]['text'] == db.text


@pytest.mark.asyncio
async def test_concurrent_preparation_returns_one_canonical_winner_and_accounting(database):
    db = database
    contexts = [await candidate(db, str(i), policy=str(i)) for i in range(4)]
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    results = await asyncio.gather(
        *(
            db.selections.prepare_selections(
                'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db, str(i))]
            )
            for i, context in enumerate(contexts)
        )
    )
    assert all(results)
    assert len({rows[0]['id'] for rows in results}) == 1
    assert len({rows[0]['text'] for rows in results}) == 1
    winner = results[0][0]
    stats = await db.selections.latest_accounting('chat', 'model', 'leaf')
    assert stats['last_tool_batch']['decision_id'] == winner['text']
    assert stats['last_tool_batch']['decision_id'] == winner['decision_id']
    assert stats['last_tool_batch']['results'][0]['allocated_tokens'] == winner['allocation']


@pytest.mark.asyncio
async def test_invalid_choice_or_report_rolls_back_entire_preparation(database):
    db = database
    context = await candidate(db)
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    bad = {**choice(db, context), 'descriptor': {**descriptor(db), 'text': 'changed'}}
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=[choice(db, context), bad], batch_reports=[]
        )
        is None
    )
    bad_report = report(db)
    bad_report['results'].append(dict(message_id='a', output_id='missing', method='summary'))
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[bad_report]
        )
        is None
    )
    assert (await db.selections.list_selections('chat', 'model', 'leaf'))[0]['state'] == 'pending'
    assert await db.selections.latest_accounting('chat', 'model', 'leaf') is None


@pytest.mark.asyncio
async def test_context_identity_requires_same_source_model_and_ancestor_branch(database):
    db = database
    context = await candidate(db, model='other')
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[]
        )
        is None
    )
    await edit(db, 'sister', role='user', content='other', parentId='a')
    context = await candidate(db, branch='sister')
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[]
        )
        is None
    )


@pytest.mark.asyncio
async def test_unchanged_exact_read_selects_raw_without_copy_and_keeps_accounting(database):
    db = database
    exact = {**choice(db), 'method': 'read'}
    selected = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[exact], batch_reports=[report(db, method='read', processed=1000)]
    )
    assert selected[0]['method'] == 'read' and selected[0]['context_id'] is None
    assert selected[0]['text'] == db.text
    stats = await db.selections.latest_accounting('chat', 'model', 'leaf')
    assert stats['last_tool_batch']['processed_tokens'] == 1000
    assert stats['last_tool_batch']['results'][0]['method'] == 'read'


@pytest.mark.asyncio
async def test_complete_batch_rejects_accounting_if_any_member_is_edited(database):
    db = database
    data = descriptor(db)
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**data, 'output_id': 'second', 'call_id': 'second-call'}
    await db.store.save_result('chat', data, branch_id='a')
    await db.store.save_result('chat', second, branch_id='a')
    await db.selections.register_pending('chat', 'model', [data, second], branch_id='a')
    choices = [choice(db), {**choice(db), 'descriptor': second}]
    batch = report(db, method='original', processed=1000)
    # A complete report cannot omit an admitted member from the same batch.
    assert (
        await db.selections.prepare_selections(
            'chat', 'model', branch_id='leaf', choices=choices, batch_reports=[batch]
        )
        is None
    )
    batch['results'].append({**batch['results'][0], 'output_id': 'second'})
    selected = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=choices, batch_reports=[batch]
    )
    assert len(selected) == 2
    assert (await db.selections.latest_accounting('chat', 'model', 'leaf'))['last_tool_batch'][
        'processed_tokens'
    ] == 2000
    await edit(
        db, 'a', output=[db.raw['a']['output'][0], {**extra, 'output': [{'type': 'input_text', 'text': 'edited'}]}]
    )
    assert len(await db.selections.list_selections('chat', 'model', 'leaf')) == 1
    assert await db.selections.latest_accounting('chat', 'model', 'leaf') is None


@pytest.mark.asyncio
async def test_resumed_batch_accounts_for_prior_frozen_members_omitted_from_choices(database):
    db = database
    data = descriptor(db)
    first = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='a',
        choices=[choice(db)],
        batch_reports=[report(db, method='original', processed=1000)],
    )
    assert first[0]['batch_report']['results'][0]['output_id'] == 'out'
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**data, 'output_id': 'second', 'call_id': 'second-call'}
    await db.selections.register_pending('chat', 'model', [second], branch_id='a')
    batch = report(db, decision='next', method='original', processed=1000)
    batch['results'].append({**batch['results'][0], 'output_id': 'second'})
    second_choice = {**choice(db), 'descriptor': second}
    frozen = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[second_choice], batch_reports=[batch]
    )
    assert frozen and len(frozen) == 1
    stats = await db.selections.latest_accounting('chat', 'model', 'leaf')
    assert stats['last_tool_batch']['processed_tokens'] == 2000
    assert len(stats['last_tool_batch']['results']) == 2
    assert first[0]['recorded_at'] < stats['recorded_at']


@pytest.mark.asyncio
async def test_same_call_and_batch_ids_on_later_reply_have_independent_selections(database):
    db = database
    first = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='a',
        choices=[choice(db)],
        batch_reports=[report(db, 'first', method='original', processed=1000)],
    )
    await edit(db, 'a2', role='assistant', content='', parentId='leaf', output=copy.deepcopy(db.raw['a']['output']))
    second_descriptor = {**descriptor(db), 'message_id': 'a2'}
    await db.selections.register_pending('chat', 'model', [second_descriptor], branch_id='a2')
    next_report = report(db, 'second', method='original', processed=1000)
    next_report['source_message_id'] = 'a2'
    next_report['results'][0]['message_id'] = 'a2'
    second = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='a2',
        choices=[{**choice(db), 'descriptor': second_descriptor}],
        batch_reports=[next_report],
    )
    assert second[0]['id'] != first[0]['id']
    assert len(await db.selections.list_selections('chat', 'model', 'a2')) == 2
    assert (await db.selections.latest_accounting('chat', 'model', 'a2'))['last_tool_batch']['decision_id'] == 'second'
    assert (await db.selections.latest_accounting('chat', 'model', 'leaf'))['last_tool_batch']['decision_id'] == 'first'
    assert await db.selections.list_selections('other', 'model', 'a2') == []


@pytest.mark.asyncio
async def test_pending_preparation_requires_complete_atomic_accounting(database):
    db = database
    context = await candidate(db)
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    result = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[]
    )
    assert result is None
    assert (await db.selections.list_selections('chat', 'model', 'leaf'))[0]['state'] == 'pending'


@pytest.mark.asyncio
async def test_partially_overlapping_admission_preserves_winner_operation_metadata(database):
    db = database
    context = await candidate(db, branch='a')
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    first_report = report(db)
    first_report['results'][0].update(
        operation_id='winner-op',
        reduction_reasons=['single_limit'],
        result_limit=100,
        description='Tool result summarized',
    )
    first = await db.selections.prepare_selections(
        'chat', 'model', branch_id='a', choices=[choice(db, context)], batch_reports=[first_report]
    )
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**descriptor(db), 'output_id': 'second', 'call_id': 'second-call'}
    await db.selections.register_pending('chat', 'model', [second], branch_id='a')
    batch = report(db, 'later', method='original', processed=1000)
    batch['results'][0].update(
        operation_id='loser-op', reduction_reasons=['input_space'], result_limit=25, description='wrong'
    )
    batch['results'].append({**batch['results'][0], 'output_id': 'second'})
    selected = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='leaf',
        choices=[choice(db), {**choice(db), 'descriptor': second}],
        batch_reports=[batch],
    )
    assert selected[0]['id'] == first[0]['id']
    stats = await db.selections.latest_accounting('chat', 'model', 'leaf')
    winner = stats['last_tool_batch']['results'][0]
    assert winner['operation_id'] == 'winner-op'
    assert winner['reduction_reasons'] == ['single_limit']
    assert winner['description'] == 'Tool result summarized'
    assert winner['result_limit'] == 100
    assert winner['selection_id'] == first[0]['id']


@pytest.mark.asyncio
async def test_descendant_stats_never_claim_different_effective_selection(database):
    db = database
    context = await candidate(db)
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    first = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**descriptor(db), 'output_id': 'second', 'call_id': 'second-call'}
    await db.selections.register_pending('chat', 'model', [second], branch_id='a')
    ancestor_batch = report(db, 'ancestor', method='original', processed=1000)
    ancestor_batch['results'].append({**ancestor_batch['results'][0], 'output_id': 'second'})
    assert await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='a',
        choices=[choice(db), {**choice(db), 'descriptor': second}],
        batch_reports=[ancestor_batch],
    )
    stats = await db.selections.latest_accounting('chat', 'model', 'leaf')
    assert stats['last_tool_batch']['results'][0]['selection_id'] == first[0]['id']
    assert stats['last_tool_batch']['processed_tokens'] == 10
    assert stats['last_tool_batch']['complete'] is False


@pytest.mark.asyncio
async def test_canonical_payload_guard_rolls_back_fresh_members_of_partial_race(database):
    db = database
    context = await candidate(db, branch='a')
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    first = await db.selections.prepare_selections(
        'chat', 'model', branch_id='a', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**descriptor(db), 'output_id': 'second', 'call_id': 'second-call'}
    await db.selections.register_pending('chat', 'model', [second], branch_id='a')
    batch = report(db, method='original', processed=1000)
    batch['results'].append({**batch['results'][0], 'output_id': 'second'})
    observed = []

    def guard(rows, fresh_ids):
        observed.append((rows, fresh_ids))
        return False

    result = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='leaf',
        choices=[choice(db), {**choice(db), 'descriptor': second}],
        batch_reports=[batch],
        validate=guard,
    )
    assert result is None and len(observed) == 1
    rows, fresh_ids = observed[0]
    assert len(fresh_ids) == 1 and first[0]['id'] not in fresh_ids
    assert next(row for row in rows if row['output_id'] == 'out')['text'] == 'summary'
    saved = await db.selections.list_selections('chat', 'model', 'leaf')
    assert next(row for row in saved if row['output_id'] == 'second')['state'] == 'pending'
    assert next(row for row in saved if row['output_id'] == 'out')['id'] == first[0]['id']


@pytest.mark.asyncio
async def test_shared_mixed_batch_reports_effective_raw_fallback_without_rewriting_history(database, monkeypatch):
    from open_webui.utils.context_compaction_v2 import estimate_text

    db = database
    context = await candidate(db, branch='a')
    await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    first = await db.selections.prepare_selections(
        'chat', 'model', branch_id='a', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    frozen = copy.deepcopy(first[0])
    extra = {**db.raw['a']['output'][0], 'id': 'second', 'call_id': 'second-call'}
    await edit(db, 'a', output=[*db.raw['a']['output'], extra])
    second = {**descriptor(db), 'output_id': 'second', 'call_id': 'second-call'}
    await db.selections.register_pending('chat', 'model', [second], branch_id='a')
    module = __import__('open_webui.models.chat_tool_result_selections', fromlist=['_snapshot'])
    snapshot = module._snapshot
    shared = {'enabled': True}

    async def current_snapshot(*args, **kwargs):
        data = await snapshot(*args, **kwargs)
        if data and shared['enabled']:
            data['shared_history'] = True
        return data

    monkeypatch.setattr(module, '_snapshot', current_snapshot)
    batch = report(db, 'shared-current', method='original', processed=1000)
    batch['results'][0]['description'] = 'Original shared history'
    batch['results'].append({**batch['results'][0], 'output_id': 'second'})
    selected = await db.selections.prepare_selections(
        'chat',
        'model',
        branch_id='leaf',
        choices=[choice(db), {**choice(db), 'descriptor': second}],
        batch_reports=[batch],
    )
    assert selected[0]['id'] == frozen['id'] and selected[0]['context_id'] is None
    assert selected[0]['method'] == 'original' and selected[0]['text'] == db.text
    statistics = await db.selections.latest_accounting('chat', 'model', 'leaf')
    assert statistics is not None
    result = statistics['last_tool_batch']['results'][0]
    assert result['context_id'] is None and result['method'] == 'original'
    assert result['processed_tokens'] == result['allocated_tokens'] == estimate_text(db.text)
    assert statistics['last_tool_batch']['processed_tokens'] == estimate_text(db.text) + 1000
    shared['enabled'] = False
    restored = await db.selections.list_selections('chat', 'model', 'leaf')
    assert next(row for row in restored if row['id'] == frozen['id']) == frozen
