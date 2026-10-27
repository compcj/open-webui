import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from test_temporary_context_graph import raw_graph
from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository
from open_webui.utils.temporary_contexts import sync_temporary_history, configure_temporary_repository
from open_webui.utils import temporary_context_runtime as runtime
from open_webui.utils.context_store_errors import ContextConflict, ContextNotFound


async def prepared(monkeypatch, *, siblings=False):
    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')
    graph = raw_graph()
    graph['messages']['a'].update(content='', done=False)
    if siblings:
        graph['messages']['b'] = {**graph['messages']['a'], 'id': 'b'}
    identity = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 0, graph)
    await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket')
    request = Request(
        {
            'type': 'http',
            'headers': [],
            'state': {},
            'app': SimpleNamespace(state=SimpleNamespace(temporary_contexts=repo, redis=None)),
        }
    )
    metadata = dict(user_id='owner', chat_id='temporary:' + doc['id'], session_id='socket', user_message_id='u')
    monkeypatch.setattr(runtime, 'authenticate_socket', AsyncMock())
    configure_temporary_repository(repo)
    return repo, doc, request, {**identity, 'branch_id': 'u'}, metadata


@pytest.mark.asyncio
async def test_generation_reserves_sibling_models_before_effects_and_rejects_replay(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch, siblings=True)
    entries = [dict(message_id=key, model_id='model') for key in ('a', 'b')]
    reservations = await runtime.register_temporary_generation(
        request, SimpleNamespace(id='owner'), identity, metadata, entries
    )
    assert len({v['scope'].task_id for v in reservations.values()}) == 2
    assert {v['scope'].branch_id for v in reservations.values()} == {'a', 'b'}
    with pytest.raises(ContextConflict):
        await runtime.register_temporary_generation(request, SimpleNamespace(id='owner'), identity, metadata, entries)
    with pytest.raises(ContextNotFound):
        await runtime.register_temporary_generation(request, SimpleNamespace(id='admin'), identity, metadata, entries)


@pytest.mark.asyncio
async def test_generation_rejects_whole_batch_when_one_placeholder_is_not_its_branch(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch)
    with pytest.raises(ContextConflict):
        await runtime.register_temporary_generation(
            request,
            SimpleNamespace(id='owner'),
            identity,
            metadata,
            [dict(message_id='a', model_id='model'), dict(message_id='forged', model_id='model')],
        )
    assert not (await repo.read(doc['id'], 'owner', doc['epoch']))['tasks']


@pytest.mark.asyncio
async def test_events_persist_raw_before_delivery_and_socket_rebinding_invalidates_rpc(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch)
    reservations = await runtime.register_temporary_generation(
        request, SimpleNamespace(id='owner'), identity, metadata, [dict(message_id='a', model_id='model')]
    )
    scope = reservations['a']['scope']
    metadata.update(message_id='a', task_id=scope.task_id)
    runtime.bind_temporary_reply(request, metadata, reservations['a'])
    event = {'type': 'chat:completion', 'data': {'content': 'partial', 'done': False}}
    saved, envelope = await runtime.persist_temporary_event(metadata, event)
    assert saved['data']['snapshot']['messages_map']['a']['content'] == 'partial'
    assert envelope['seq'] == 1 and envelope['temporary_context']['revision'] == 2
    assert envelope['chat_id'] == metadata['chat_id']
    await runtime.validate_scope_socket(metadata)
    await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='new-socket')
    with pytest.raises(ContextConflict):
        await runtime.validate_scope_socket(metadata)
    await repo.request_stop(doc['id'], 'owner', doc['epoch'])
    # Original worker may still persist buffered output during cancellation.
    saved, _ = await runtime.persist_temporary_event(metadata, {'type': 'chat:tasks:cancel'})
    assert saved['data']['snapshot']['messages_map']['a']['done'] is True
    assert saved['data']['snapshot']['messages_map']['a']['content'] == 'partial'


def test_parent_alias_cannot_escape_graph_validation():
    from fastapi import HTTPException
    from open_webui.utils.temporary_contexts import validate_temporary_history

    graph = raw_graph()
    graph['messages']['u']['parent_id'] = 'a'
    with pytest.raises(HTTPException):
        validate_temporary_history(graph)


@pytest.mark.asyncio
async def test_aborted_fanout_releases_every_unstarted_reservation(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch, siblings=True)
    entries = [dict(message_id=key, model_id='model') for key in ('a', 'b')]
    reservations = await runtime.register_temporary_generation(
        request, SimpleNamespace(id='owner'), identity, metadata, entries
    )

    # Avoid transport imports; cache completion is the authority under test.
    async def ack(bound):
        await repo.acknowledge_task_done(doc['id'], 'owner', doc['epoch'], task_id=bound['task_id'])

    monkeypatch.setattr(runtime, 'acknowledge_temporary_task', ack)
    await runtime.abort_temporary_reservations(request, metadata, reservations, {})
    state = await repo.close(doc['id'], 'owner', doc['epoch'])
    assert state['status'] == 'closed_confirmed'
    assert all(task['status'] == 'done' for task in state['tasks'].values())


@pytest.mark.asyncio
async def test_continue_keeps_original_reply_content_and_requires_explicit_identity(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch)
    graph = raw_graph()
    graph['messages']['a'].update(content='partial original', done=False)
    next_identity = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], identity['revision'], graph)
    identity = {**next_identity, 'branch_id': 'u'}
    with pytest.raises(ContextConflict):
        await runtime.register_temporary_generation(
            request, SimpleNamespace(id='owner'), identity, metadata, [dict(message_id='a', model_id='model')]
        )
    metadata['assistant_message_id'] = 'a'
    result = await runtime.register_temporary_generation(
        request, SimpleNamespace(id='owner'), identity, metadata, [dict(message_id='a', model_id='model')]
    )
    assert result['a']['scope'].reply_id == 'a'
    assert (await repo.read(doc['id'], 'owner', doc['epoch']))['data']['snapshot']['messages_map']['a'][
        'content'
    ] == 'partial original'
