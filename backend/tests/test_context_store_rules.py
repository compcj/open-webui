"""Storage-independent selection transitions agree with SQL and are atomic."""

import copy
import importlib

import pytest
from sqlalchemy import select
from test_tool_result_storage import database, descriptor
from test_tool_result_selections import candidate, choice, report


def rules():
    return importlib.import_module('open_webui.utils.context_store_rules')


async def records(db):
    async with db.sessions() as session:
        selections = list((await session.execute(select(db.selection_model.ChatToolResultSelection))).scalars())
        contexts = list((await session.execute(select(db.model.ChatToolResultContext))).scalars())
        raws = list((await session.execute(select(db.model.ChatToolResult))).scalars())
        return tuple([[db.model.row_dict(row) for row in rows] for rows in (selections, contexts, raws)])


@pytest.mark.asyncio
async def test_pure_resolver_matches_sql_and_preserves_fixed_selection(database):
    db = database
    context = await candidate(db)
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    snapshot = {'messages_map': copy.deepcopy(db.raw), 'shared_history': False}
    selected, contexts, raws = await records(db)
    transition = rules().prepare_selection_transition(
        snapshot,
        'chat',
        'model',
        'leaf',
        selected,
        contexts,
        raws,
        choices=[choice(db, context)],
        batch_reports=[report(db)],
        now=lambda: 100,
        new_id=lambda: 'pure-id',
    )
    assert transition is not None
    assert transition['canonical'][0]['text'] == 'summary'
    assert selected[0]['state'] == 'pending'
    sql = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    assert sql[0]['text'] == transition['canonical'][0]['text']
    selected, contexts, raws = await records(db)
    effective = rules().effective_selections(snapshot, 'chat', 'model', 'leaf', selected, contexts)
    assert [rules().resolved_selection(*item) for item in effective.values()] == sql
    assert rules().latest_valid_accounting(snapshot, effective, 'chat', 'model', 'leaf') == (
        await db.selections.latest_accounting('chat', 'model', 'leaf')
    )
    loser = rules().prepare_selection_transition(
        snapshot,
        'chat',
        'model',
        'leaf',
        selected,
        contexts,
        raws,
        choices=[choice(db)],
        batch_reports=[],
        now=lambda: 999,
    )
    assert loser['canonical'] == sql
    assert loser['writes'] == []


@pytest.mark.asyncio
async def test_failed_pure_admission_cannot_mutate_source_rows_or_reports(database):
    db = database
    context = await candidate(db)
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    selected, contexts, raws = await records(db)
    snapshot = {'messages_map': copy.deepcopy(db.raw), 'shared_history': False}
    batch = report(db)
    before = copy.deepcopy((snapshot, selected, contexts, raws, batch))
    seen = []
    result = rules().prepare_selection_transition(
        snapshot,
        'chat',
        'model',
        'leaf',
        selected,
        contexts,
        raws,
        choices=[choice(db, context)],
        batch_reports=[batch],
        validate=lambda versions, fresh: seen.append((versions, fresh)) or False,
    )
    assert result is None and len(seen) == 1 and len(seen[0][1]) == 1
    assert (snapshot, selected, contexts, raws, batch) == before
    assert (
        rules().prepare_selection_transition(
            snapshot,
            'chat',
            'model',
            'leaf',
            selected,
            contexts,
            raws,
            choices=[choice(db, context)],
            batch_reports=[],
        )
        is None
    )
    assert (snapshot, selected, contexts, raws, batch) == before


@pytest.mark.asyncio
async def test_shared_fallback_and_source_edits_use_identical_rules(database):
    db = database
    context = await candidate(db)
    assert await db.selections.register_pending('chat', 'model', [descriptor(db)], branch_id='a')
    sql = await db.selections.prepare_selections(
        'chat', 'model', branch_id='leaf', choices=[choice(db, context)], batch_reports=[report(db)]
    )
    selected, contexts, raws = await records(db)
    snapshot = {'messages_map': copy.deepcopy(db.raw), 'shared_history': True}
    effective = rules().effective_selections(snapshot, 'chat', 'model', 'leaf', selected, contexts)
    fallback = [rules().resolved_selection(*item) for item in effective.values()]
    assert fallback[0]['id'] == sql[0]['id']
    assert fallback[0]['text'] == db.text and fallback[0]['method'] == 'original'
    assert rules().latest_valid_accounting(snapshot, effective, 'chat', 'model', 'leaf') is None
    snapshot['messages_map']['a']['output'].append({'id': 'later', 'type': 'message', 'content': 'answer'})
    assert rules().effective_selections(snapshot, 'chat', 'model', 'leaf', selected, contexts)
    snapshot['messages_map']['u']['content'] = 'edited ancestor'
    assert rules().effective_selections(snapshot, 'chat', 'model', 'leaf', selected, contexts) == {}
    assert next(row for row in selected if row['id'] == sql[0]['id'])['method'] == 'summary'


def test_branch_validation_rejects_cycles_dangling_and_preserves_empty_results():
    module = rules()
    source = {
        'id': 'a',
        'parentId': 'u',
        'role': 'assistant',
        'output': [
            {
                'id': 'out',
                'type': 'function_call_output',
                'call_id': 'call',
                'output': [],
                'tool_result': {'batch_id': 'batch', 'name': 'tool', 'kind': 'native'},
            }
        ],
    }
    snapshot = {'messages_map': {'u': {'id': 'u', 'role': 'user', 'content': 'q'}, 'a': source}}
    desc = dict(message_id='a', output_id='out', call_id='call', batch_id='batch', name='tool', kind='native', text='')
    assert module.source_state(snapshot, desc, 'a')
    assert module.source_state(snapshot, {**desc, 'text': 'invented'}, 'a') is None
    source['parentId'] = 'a'
    assert module.branch_chain(snapshot['messages_map'], 'a') is None
    source['parentId'] = 'missing'
    assert module.branch_chain(snapshot['messages_map'], 'a') is None
