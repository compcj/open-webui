"""Temporary callback nonces are authenticated, bounded, and never journal payloads."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_temporary_context_runtime import prepared
from open_webui.utils import temporary_context_runtime as runtime
from open_webui.utils.temporary_contexts import public_state
from open_webui.utils.context_store_errors import ContextStoreError, ContextConflict, ContextCapacity


async def bound(monkeypatch):
    repo, doc, request, identity, metadata = await prepared(monkeypatch)
    reservation = (
        await runtime.register_temporary_generation(
            request, SimpleNamespace(id='owner'), identity, metadata, [dict(message_id='a', model_id='model')]
        )
    )['a']
    metadata.update(message_id='a', task_id=reservation['scope'].task_id)
    runtime.bind_temporary_reply(request, metadata, reservation)
    return repo, doc, metadata


def claim_fields(envelope):
    return dict(
        socket_generation=envelope['socket_generation'],
        task_id=envelope['task_id'],
        reply_id=envelope['message_id'],
        model_id=envelope['model_id'],
        seq=envelope['seq'],
    )


@pytest.mark.asyncio
async def test_rpc_payload_is_not_journaled_and_nonce_claim_is_atomic(monkeypatch):
    repo, doc, metadata = await bound(monkeypatch)
    event = {'type': 'execute:tool', 'data': {'token': 'sensitive-synthetic-token', 'params': {'private': 'body'}}}
    envelope = await runtime.temporary_rpc_envelope(metadata, event, timeout=60)
    current = await repo.read(doc['id'], 'owner', doc['epoch'])
    assert 'sensitive-synthetic-token' not in json.dumps(current)
    assert 'private' not in json.dumps(current)
    assert envelope['data'] == event
    state = public_state(current, repo.config)
    assert state['pending_rpcs'][0]['rpc_id'] == envelope['rpc_id']
    assert not current['events']
    attempts = await asyncio.gather(
        *(
            runtime.claim_temporary_rpc(
                repo, doc['id'], 'owner', doc['epoch'], envelope['rpc_id'], claim_fields(envelope)
            )
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, dict) and result['claimed'] for result in attempts) == 1
    assert sum(isinstance(result, ContextConflict) for result in attempts) == 1
    await runtime.complete_temporary_rpc(metadata, envelope['rpc_id'])
    assert not public_state(await repo.read(doc['id'], 'owner', doc['epoch']), repo.config)['pending_rpcs']
    with pytest.raises(ContextStoreError):
        await runtime.validate_temporary_rpc(metadata, envelope['rpc_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', ['rebind', 'stop', 'expired', 'done'])
async def test_rpc_claim_rejects_every_stale_delivery(monkeypatch, invalidate):
    repo, doc, metadata = await bound(monkeypatch)
    envelope = await runtime.temporary_rpc_envelope(metadata, {'type': 'confirmation', 'data': {}}, timeout=1)
    if invalidate == 'rebind':
        await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket', rebind=True)
    elif invalidate == 'stop':
        await repo.request_stop(doc['id'], 'owner', doc['epoch'])
    elif invalidate == 'done':
        await repo.acknowledge_task_done(doc['id'], 'owner', doc['epoch'], task_id=metadata['task_id'])
    else:

        def expire(current, now):
            current['data']['rpc_calls'][envelope['rpc_id']]['expires_at'] = now - 1

        await repo.transact(doc['id'], 'owner', doc['epoch'], expire)
    with pytest.raises(ContextStoreError):
        await runtime.claim_temporary_rpc(
            repo, doc['id'], 'owner', doc['epoch'], envelope['rpc_id'], claim_fields(envelope)
        )
    assert not public_state(await repo.read(doc['id'], 'owner', doc['epoch']), repo.config)['pending_rpcs']


@pytest.mark.asyncio
async def test_only_explicit_same_sid_rebind_changes_generation(monkeypatch):
    repo, doc, metadata = await bound(monkeypatch)
    old = metadata['temporary_context']['socket_generation']
    first = await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket')
    second = await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket', rebind=True)
    assert first['socket']['generation'] == old
    assert second['socket']['generation'] == old + 1
    await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket')
    with pytest.raises(ContextStoreError):
        await runtime.temporary_rpc_envelope(metadata, {'type': 'execute:python', 'data': {}})


@pytest.mark.asyncio
async def test_pending_markers_have_a_bounded_count(monkeypatch):
    repo, doc, metadata = await bound(monkeypatch)
    for _ in range(runtime.MAX_PENDING_RPCS):
        await runtime.temporary_rpc_envelope(metadata, {'type': 'execute:python', 'data': {}}, timeout=60)
    with pytest.raises(ContextCapacity):
        await runtime.temporary_rpc_envelope(metadata, {'type': 'execute:python', 'data': {}}, timeout=60)


@pytest.mark.asyncio
async def test_hint_delivery_failure_cannot_prevent_remaining_reservation_release(monkeypatch):
    import sys
    from types import ModuleType
    from unittest.mock import AsyncMock

    repo, doc, request, identity, metadata = await prepared(monkeypatch, siblings=True)
    reservations = await runtime.register_temporary_generation(
        request,
        SimpleNamespace(id='owner'),
        identity,
        metadata,
        [dict(message_id=value, model_id='model') for value in ('a', 'b')],
    )
    socket = ModuleType('open_webui.socket.main')
    socket.sio = SimpleNamespace(emit=AsyncMock(side_effect=RuntimeError('hint delivery failed')))
    socket.get_event_call_session_error = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, socket.__name__, socket)
    await runtime.abort_temporary_reservations(request, metadata, reservations, {})
    current = await repo.read(doc['id'], 'owner', doc['epoch'])
    assert all(task['status'] == 'done' for task in current['tasks'].values())


@pytest.mark.asyncio
async def test_failed_recovery_does_not_starve_other_scope_stops(monkeypatch):
    import sys
    from types import ModuleType
    from unittest.mock import AsyncMock

    tasks = ModuleType('open_webui.tasks')
    tasks.stop_task = AsyncMock()
    socket = ModuleType('open_webui.socket.main')
    socket.get_event_call_session_error = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, tasks.__name__, tasks)
    monkeypatch.setitem(sys.modules, socket.__name__, socket)
    monkeypatch.setattr(runtime, '_pending_completions', {})
    monkeypatch.setattr(
        runtime,
        'TemporaryContextAdapter',
        lambda *args: SimpleNamespace(recover_publications=AsyncMock(side_effect=ContextConflict())),
    )
    monkeypatch.setattr(runtime.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError()))
    task = dict(task_id='one', reply_id='a', model_id='model', fence=1)
    results = [
        dict(id='first', actor_id='owner', epoch='epoch', publications=[task], tasks=[task]),
        dict(id='second', actor_id='owner', epoch='epoch', tasks=[{**task, 'task_id': 'two'}]),
    ]
    app = SimpleNamespace(
        state=SimpleNamespace(redis=None, temporary_contexts=SimpleNamespace(sweep=AsyncMock(return_value=results)))
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime.temporary_context_maintenance(app)
    assert [entry.args[1] for entry in tasks.stop_task.call_args_list] == ['one', 'two']


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['claimed', 'unclaimed', 'stop', 'rebind'])
async def test_socket_callback_requires_live_claim_and_cleans_marker(monkeypatch, outcome):
    import sys
    from types import ModuleType
    from unittest.mock import AsyncMock
    from test_event_call_access import runtime as socket_fixture

    repo, doc, metadata = await bound(monkeypatch)
    socket_runtime = socket_fixture.__wrapped__()
    ns = socket_runtime.ns
    ns['get_event_call_session_error'] = AsyncMock(return_value=None)
    ns['get_user_id_from_session_pool'] = lambda sid: 'owner'
    socket = ModuleType('open_webui.socket.main')
    socket.sio = ns['sio']
    socket.get_event_call_session_error = ns['get_event_call_session_error']
    monkeypatch.setitem(sys.modules, socket.__name__, socket)
    delivered = []

    async def answer(event, envelope, **kwargs):
        delivered.append(envelope)
        if outcome == 'claimed':
            await runtime.claim_temporary_rpc(
                repo, doc['id'], 'owner', doc['epoch'], envelope['rpc_id'], claim_fields(envelope)
            )
            return {'approved': True}
        if outcome == 'unclaimed':
            return {'approved': True}
        if outcome == 'stop':
            await repo.request_stop(doc['id'], 'owner', doc['epoch'])
        else:
            await repo.lease(doc['id'], 'owner', doc['epoch'], socket_id='socket', rebind=True)
        await asyncio.Future()

    ns['sio'].call.side_effect = answer
    caller = await ns['get_event_call'](metadata)
    result = await asyncio.wait_for(caller({'type': 'confirmation', 'data': {'token': 'private-rpc-token'}}), 2.5)
    assert result == {'approved': True} if outcome == 'claimed' else 'error' in result
    assert len(delivered) == 1
    current = await repo.read(doc['id'], 'owner', doc['epoch'])
    assert not current['data'].get('rpc_calls')
    assert 'private-rpc-token' not in json.dumps(current)
    if outcome == 'claimed':
        done = ns['sio'].emit.call_args.args[1]
        assert done['temporary_context']['id'] == doc['id']
        assert done['task_id'] == metadata['task_id']
        assert done['data']['type'] == 'request:interaction:done'


@pytest.mark.asyncio
async def test_failed_reservation_release_continues_and_retries_only_identity(monkeypatch):
    from unittest.mock import AsyncMock

    repo, doc, request, identity, metadata = await prepared(monkeypatch, siblings=True)
    metadata['secret'] = 'private-request-token'
    reservations = await runtime.register_temporary_generation(
        request,
        SimpleNamespace(id='owner'),
        identity,
        metadata,
        [dict(message_id=value, model_id='model') for value in ('a', 'b')],
    )
    monkeypatch.setattr(runtime, '_pending_completions', {})
    ack = AsyncMock(side_effect=[RuntimeError('first failed'), True])
    monkeypatch.setattr(runtime, 'acknowledge_temporary_task', ack)
    await runtime.abort_temporary_reservations(request, metadata, reservations, {})
    assert ack.await_count == 2
    assert len(runtime._pending_completions) == 1
    assert 'private-request-token' not in json.dumps(list(runtime._pending_completions.values()), default=str)
