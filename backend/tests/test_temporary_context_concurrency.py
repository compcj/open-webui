"""Admission, expiry and lost-record races at the actual CAS boundary."""

import asyncio
import json

import pytest
from test_temporary_context_store import repository
from test_temporary_context_redis import stores
from open_webui.utils.context_store_errors import ContextConflict, ContextGone


def task(task_id, reply='reply'):
    return dict(task_id=task_id, reply_id=reply, model_id='model', branch_id='branch', worker_nonce='worker')


async def duplicate_reply(repo, other):
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    results = await asyncio.gather(
        repo.register_tasks(*args, [task('one')], expected_revision=0),
        other.register_tasks(*args, [task('two')], expected_revision=0),
        return_exceptions=True,
    )
    assert sum(isinstance(value, tuple) for value in results) == 1
    assert sum(isinstance(value, ContextConflict) for value in results) == 1
    with pytest.raises(ContextConflict):
        await repo.register_tasks(*args, [task('three', 'other'), task('four', 'other')], expected_revision=0)
    await repo.register_tasks(*args, [task('five', 'sibling'), task('six', 'same-model')], expected_revision=0)
    state = await repo.read(*args)
    assert len(state['tasks']) == 3


@pytest.mark.asyncio
async def test_memory_one_active_owner_per_reply():
    repo, _ = repository()
    await duplicate_reply(repo, repo)


@pytest.mark.asyncio
async def test_redis_one_active_owner_per_reply(stores):
    await duplicate_reply(*stores)


@pytest.mark.asyncio
async def test_memory_lease_cannot_extend_a_deadline_that_passed_during_commit(monkeypatch):
    repo, clock = repository(idle_seconds=10)
    doc = await repo.create('alice')
    clock[0] = 1009
    original = repo.backend.commit
    delayed = False

    async def commit(proposed, **kwargs):
        nonlocal delayed
        if not delayed and proposed['status'] == 'open':
            delayed = True
            clock[0] = 1011
        return await original(proposed, **kwargs)

    monkeypatch.setattr(repo.backend, 'commit', commit)
    with pytest.raises(ContextGone):
        await repo.lease(doc['id'], 'alice', doc['epoch'], socket_id='reconnected')
    state = await repo.read(doc['id'], 'alice', doc['epoch'], allow_closed=True)
    assert state['fence'] == 1 and state['status'] == 'closed_confirmed'


@pytest.mark.asyncio
async def test_redis_reconnect_cannot_remove_a_deadline_that_passed_during_commit(stores, monkeypatch):
    repo = stores[0]
    doc = await repo.create('alice')
    key = repo.backend.keys(doc['id'], 'alice')[0]
    control = json.loads(await repo.backend.client.hget(key, 'control'))
    control['disconnected_deadline'] = await repo.backend.now() + 0.25
    await repo.backend.client.hset(key, 'control', json.dumps(control))
    original = repo.backend.commit
    delayed = False

    async def commit(proposed, **kwargs):
        nonlocal delayed
        if not delayed and proposed['status'] == 'open':
            delayed = True
            await asyncio.sleep(0.35)
        return await original(proposed, **kwargs)

    monkeypatch.setattr(repo.backend, 'commit', commit)
    with pytest.raises(ContextGone):
        await repo.lease(doc['id'], 'alice', doc['epoch'], socket_id='reconnected')
    state = await repo.read(doc['id'], 'alice', doc['epoch'], allow_closed=True)
    assert state['fence'] == 1 and state['status'] == 'closed_confirmed'


@pytest.mark.asyncio
async def test_missing_full_sweep_batch_does_not_starve_healthy_expiry(stores):
    repo = stores[0]
    docs = [await repo.create('alice') for _ in range(3)]
    client = repo.backend.client
    catalog = repo.backend.prefix + ':catalog'
    for index, doc in enumerate(docs):
        key = repo.backend.keys(doc['id'], 'alice')[0]
        if index < 2:
            await client.delete(key)
        else:
            control = json.loads(await client.hget(key, 'control'))
            control['idle_deadline'] = 1
            await client.hset(key, 'control', json.dumps(control))
        await client.zadd(catalog, {doc['id']: index})
    charged = await client.hget(repo.backend.prefix + ':quota', 'bytes')
    await repo.sweep(limit=2)
    await repo.sweep(limit=2)
    state, _ = await repo.backend.load(docs[2]['id'])
    assert state['status'] == 'tombstone' and state['fence'] == 1
    assert await client.hget(repo.backend.prefix + ':quota', 'bytes') == charged
    with pytest.raises(ContextGone) as gone:
        await repo.read(docs[0]['id'], 'alice', docs[0]['epoch'])
    assert gone.value.rebuild_allowed is False


async def exclusive_race(repo, other):
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    results = await asyncio.gather(
        repo.register_tasks(*args, [task('compact', 'current')], expected_revision=0, exclusive=True),
        other.register_tasks(*args, [task('generate', 'new')], expected_revision=0),
        return_exceptions=True,
    )
    assert sum(isinstance(value, tuple) for value in results) == 1
    assert sum(isinstance(value, ContextConflict) for value in results) == 1
    current = await repo.read(*args)
    owner = next(iter(current['tasks']))
    await repo.acknowledge_task_done(*args, task_id=owner)
    await repo.register_tasks(*args, [task('later', 'newer')], expected_revision=0)
    await repo.close(*args)
    closed = await repo.acknowledge_task_done(*args, task_id='later')
    assert closed['status'] == 'closed_confirmed'


@pytest.mark.asyncio
async def test_memory_compact_and_generation_reservation_are_atomic():
    repo, _ = repository()
    await exclusive_race(repo, repo)


@pytest.mark.asyncio
async def test_redis_compact_and_generation_reservation_are_atomic(stores):
    await exclusive_race(*stores)
