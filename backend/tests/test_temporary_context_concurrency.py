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


async def stop_fence(repo):
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.register_tasks(*args, [task('running')], expected_revision=0)
    stopped = await repo.request_stop(*args)
    assert stopped['status'] == 'open' and stopped['fence'] == 1
    assert (await repo.request_stop(*args))['fence'] == 1
    with pytest.raises(ContextConflict):
        await repo.register_tasks(*args, [task('queued', 'next')], expected_revision=0)
    with pytest.raises(ContextConflict):
        await repo.begin_execution(
            *args,
            task_id='running',
            identity={'round_id': 'round', 'call_id': 'call', 'reply_id': 'reply', 'model_id': 'model', 'ordinal': 0},
            target_hash='target',
            expected_fence=0,
        )
    await repo.acknowledge_task_done(*args, task_id='running')
    _, registered = await repo.register_tasks(*args, [task('next', 'next')], expected_revision=0)
    assert registered[0]['fence'] == 1


@pytest.mark.asyncio
async def test_memory_stop_fences_execution_but_preserves_page():
    repo, _ = repository()
    await stop_fence(repo)


@pytest.mark.asyncio
async def test_redis_stop_fences_execution_but_preserves_page(stores):
    await stop_fence(stores[0])


@pytest.mark.asyncio
async def test_sweep_observes_disconnect_without_treating_expiry_as_completion():
    repo, clock = repository(lease_seconds=10, disconnected_seconds=30)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.lease(*args, socket_id='original')
    await repo.register_tasks(*args, [task('running')], expected_revision=0)
    clock[0] += 11

    async def online(current):
        return True

    outcomes = await repo.sweep(socket_alive=online)
    assert not any(result['tasks'] for result in outcomes)

    async def offline(current):
        return False

    clock[0] += 11
    await repo.sweep(socket_alive=offline)
    current = await repo.read(*args)
    assert current['disconnected_deadline'] == clock[0] + 30
    clock[0] += 31
    results = await repo.sweep(socket_alive=offline)
    assert results[0]['status'] == 'closing' and results[0]['tasks'][0]['task_id'] == 'running'
    assert not (await repo.read(*args, allow_closed=True))['rebuild_allowed']


@pytest.mark.asyncio
async def test_disconnect_probe_cannot_overwrite_a_concurrent_authenticated_rebind():
    repo, clock = repository(lease_seconds=10)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.lease(*args, socket_id='original')
    clock[0] += 11

    async def race(current):
        await repo.lease(*args, socket_id='rebound')
        return False

    await repo.sweep(socket_alive=race)
    current = await repo.read(*args)
    assert current['socket']['id'] == 'rebound' and current['socket']['connected']
    assert current['disconnected_deadline'] is None


async def retired_rounds(repo, other, *, exclusive=False):
    """Exercise lifetime totals beyond both caps without changing byte quotas."""
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    original = {
        'snapshot': {'messages_map': {'u': {'content': 'original'}}},
        'result_admissions': {'original': {'source_hash': 'hash'}},
        'checkpoints': {'original': {'summary': 'summary'}},
        'selections': {'original': {'text': 'selected'}},
        'accounting': {'original': {'complete': True}},
    }
    await repo.transact(*args, lambda d, now: d['data'].update(original))
    first_execution = None
    width = 1 if exclusive else 5
    rounds = 36 if exclusive else 14
    for round_index in range(rounds):
        current = await other.read(*args)
        batch = [
            {**task(f'task-{round_index}-{i}', f'reply-{round_index}-{i}'), 'model_id': f'model-{i}'}
            for i in range(width)
        ]
        current, _ = await repo.register_tasks(*args, batch, expected_revision=current['revision'], exclusive=exclusive)
        assert len(current['tasks']) <= repo.config.max_tasks
        for values in batch:
            if not exclusive:
                for ordinal in range(2):
                    identity = dict(
                        round_id='round',
                        call_id=f'call-{ordinal}',
                        ordinal=ordinal,
                        reply_id=values['reply_id'],
                        model_id=values['model_id'],
                    )
                    entry, started = await repo.begin_execution(
                        *args, task_id=values['task_id'], identity=identity, target_hash='target'
                    )
                    assert started
                    await repo.resolve_execution(
                        *args, task_id=values['task_id'], execution_id=entry['id'], raw_transition=lambda d, now: None
                    )
                    duplicate, started = await other.begin_execution(
                        *args, task_id=values['task_id'], identity=identity, target_hash='target'
                    )
                    assert not started and duplicate['id'] == entry['id']
                    if first_execution is None:
                        first_execution = entry
            await repo.acknowledge_task_done(*args, task_id=values['task_id'])
        # Repeated worker completion is safe before and after retirement.
        await other.acknowledge_task_done(*args, task_id='task-0-0')
    current = await other.read(*args)
    assert len(current['tasks']) == width
    assert len(current['executions']) == (0 if exclusive else width * 2)
    assert {key: current['data'][key] for key in original} == original
    before = current
    with pytest.raises(ContextConflict):
        await repo.register_tasks(*args, [task('task-0-0', 'reused')], expected_revision=current['revision'])
    with pytest.raises(ContextConflict):
        await repo.begin_execution(
            *args,
            task_id='task-0-0',
            identity=dict(round_id='round', call_id='call-0', ordinal=0, reply_id='reply-0-0', model_id='model-0'),
            target_hash='target',
        )
    if first_execution:
        with pytest.raises(ContextConflict):
            await repo.resolve_execution(
                *args,
                task_id='task-0-0',
                execution_id=first_execution['id'],
                raw_transition=lambda d, now: d['data'].clear(),
            )
    assert await other.read(*args) == before
    assert (await repo.close(*args))['status'] == 'closed_confirmed'


@pytest.mark.asyncio
@pytest.mark.parametrize('exclusive', [False, True], ids=['five-models', 'compact-promotion'])
async def test_memory_retired_control_does_not_limit_scope_lifetime(exclusive):
    repo, _ = repository()
    await retired_rounds(repo, repo, exclusive=exclusive)


@pytest.mark.asyncio
@pytest.mark.parametrize('exclusive', [False, True], ids=['five-models', 'compact-promotion'])
async def test_redis_retired_control_does_not_limit_scope_lifetime(stores, exclusive):
    await retired_rounds(*stores, exclusive=exclusive)


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['active', 'uncertain', 'publication_pending'])
async def test_retirement_preserves_unsettled_tasks_and_execution_dedup(state):
    repo, _ = repository(max_tasks=1)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.register_tasks(*args, [task('old')], expected_revision=0)
    identity = dict(round_id='round', call_id='call', ordinal=0, reply_id='reply', model_id='model')
    entry, _ = await repo.begin_execution(*args, task_id='old', identity=identity, target_hash='target')
    if state != 'uncertain':
        await repo.resolve_execution(
            *args,
            task_id='old',
            execution_id=entry['id'],
            raw_transition=lambda d, now: d['data'].update(raw='original'),
            requires_publication=state == 'publication_pending',
        )
    if state != 'active':
        await repo.acknowledge_task_done(*args, task_id='old')
    before = await repo.read(*args)
    from open_webui.utils.context_store_errors import ContextCapacity

    with pytest.raises((ContextConflict, ContextCapacity)):
        await repo.register_tasks(*args, [task('next', 'new')], expected_revision=before['revision'])
    assert await repo.read(*args) == before
    if state == 'active':
        _, started = await repo.begin_execution(*args, task_id='old', identity=identity, target_hash='target')
        assert not started
    elif state == 'publication_pending':
        await repo.publish_execution_results(
            *args, task_id='old', execution_ids=[entry['id']], expected_fence=0, raw_transition=lambda d, now: None
        )
        current = await repo.read(*args)
        current, _ = await repo.register_tasks(*args, [task('next', 'new')], expected_revision=current['revision'])
        assert 'old' not in current['tasks'] and entry['id'] not in current['executions']
        assert current['data']['raw'] == 'original'


async def retirement_race(repo, other):
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    for task_id in ('old-one', 'old-two'):
        await repo.register_tasks(*args, [task(task_id)], expected_revision=0)
        await repo.acknowledge_task_done(*args, task_id=task_id)
    outcomes = await asyncio.gather(
        repo.register_tasks(*args, [task('new-one')], expected_revision=0),
        other.register_tasks(*args, [task('new-two')], expected_revision=0),
        return_exceptions=True,
    )
    assert sum(isinstance(value, tuple) for value in outcomes) == 1
    assert sum(isinstance(value, ContextConflict) for value in outcomes) == 1
    current = await other.read(*args)
    assert len(current['tasks']) == 1 and len(current['data']['retired_tasks']) == 2
    for task_id in ('old-one', 'old-two'):
        await other.acknowledge_task_done(*args, task_id=task_id)
        with pytest.raises(ContextConflict):
            await repo.register_tasks(*args, [task(task_id, 'other')], expected_revision=0)
    after = await repo.read(*args)
    assert after['tasks'] == current['tasks']
    assert after['events'] == current['events']
    assert after['revision'] == current['revision']


@pytest.mark.asyncio
async def test_memory_retirement_and_registration_share_cas():
    repo, _ = repository()
    await retirement_race(repo, repo)


@pytest.mark.asyncio
async def test_redis_retirement_and_registration_share_cas(stores):
    await retirement_race(*stores)
