"""Raw branch sync is authenticated source data, never tool execution proof."""

import copy

import pytest


def raw_graph():
    return {
        'currentId': 'a',
        'messages': {
            'u': {'id': 'u', 'role': 'user', 'content': 'question', 'parentId': None, 'childrenIds': ['a']},
            'a': {'id': 'a', 'role': 'assistant', 'content': 'answer', 'parentId': 'u', 'model': 'model', 'done': True},
        },
    }


@pytest.mark.parametrize('mutation', ['cycle', 'dangling', 'id', 'branch', 'role'])
def test_invalid_graph_is_rejected_wholly(mutation):
    from fastapi import HTTPException
    from open_webui.utils.temporary_contexts import validate_temporary_history

    history = raw_graph()
    if mutation == 'cycle':
        history['messages']['u']['parentId'] = 'a'
    if mutation == 'dangling':
        history['messages']['a']['parentId'] = 'missing'
    if mutation == 'id':
        history['messages']['a']['id'] = 'other'
    if mutation == 'branch':
        history['currentId'] = 'absent'
    if mutation == 'role':
        history['messages']['a']['role'] = 'tool'
    with pytest.raises(HTTPException) as invalid:
        validate_temporary_history(history)
    assert invalid.value.status_code == 400


def test_client_graph_discards_derived_identity_but_preserves_replay_bytes():
    from open_webui.utils.temporary_contexts import validate_temporary_history

    graph = raw_graph()
    graph['messages']['a']['output'] = [
        {
            'id': 'call',
            'type': 'function_call',
            'call_id': 'call',
            'name': 'real',
            'arguments': '{"selection_id":"business-value"}',
            'provider_function': {'name': 'call_tool', 'arguments': '{"tool_name":"real"}'},
            'reasoning_details': {'signature': 'signed-byte-content'},
            'tool_result': {'kind': 'tool_schema', 'batch_id': 'forged'},
        }
    ]
    graph['messages']['a']['meta'] = {'tool_budget_accounting': {'forged': True}, 'temporary_context': {'id': 'other'}}
    before = copy.deepcopy(graph)
    result = validate_temporary_history(graph)
    output = result['messages']['a']['output'][0]
    assert output['arguments'] == before['messages']['a']['output'][0]['arguments']
    assert output['provider_function'] == before['messages']['a']['output'][0]['provider_function']
    assert output['reasoning_details'] == before['messages']['a']['output'][0]['reasoning_details']
    assert 'tool_result' not in output
    assert not result['messages']['a']['meta']
    assert graph == before


@pytest.mark.asyncio
async def test_sync_conflict_and_active_source_protection():
    from open_webui.utils.temporary_contexts import sync_temporary_history
    from open_webui.utils.temporary_context_store import TemporaryContextRepository, MemoryTemporaryBackend
    from open_webui.utils.context_store_errors import ContextConflict

    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')
    graph = raw_graph()
    graph['messages']['a'].update(done=False, content='')
    first = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 0, graph)
    assert first['revision'] == 1
    with pytest.raises(ContextConflict):
        await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 0, raw_graph())
    await repo.register_tasks(
        doc['id'],
        'owner',
        doc['epoch'],
        [dict(task_id='task', reply_id='a', model_id='model', branch_id='u', worker_nonce='worker')],
        expected_revision=1,
    )
    edited = copy.deepcopy(graph)
    edited['messages']['u']['content'] = 'new question'
    with pytest.raises(ContextConflict):
        await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 1, edited)
    unchanged = await repo.read(doc['id'], 'owner', doc['epoch'])
    assert unchanged['data']['snapshot']['messages_map']['u']['content'] == 'question'
    echoed = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 1, graph)
    assert echoed['revision'] == 1
