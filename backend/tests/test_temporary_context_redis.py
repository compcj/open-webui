"""Real Redis acceptance; credentials come only from explicit fixture environment."""

import asyncio
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.cluster import key_slot

from open_webui.utils.context_store_errors import ContextCapacity, ContextConflict, ContextGone, ContextUnavailable
from open_webui.utils.temporary_context_store import (
    RedisTemporaryBackend,
    TemporaryContextConfig,
    TemporaryContextRepository,
)


@pytest_asyncio.fixture
async def stores():
    url = os.environ.get('REDIS_TEST_URL')
    fixture = os.environ.get('TEMPORARY_CONTEXT_TEST_SERVICES_JSON')
    if not url and fixture:
        url = json.loads(Path(fixture).read_text(encoding='utf-8-sig'))['redis_url']
    if not url:
        pytest.skip('Set REDIS_TEST_URL or TEMPORARY_CONTEXT_TEST_SERVICES_JSON for real Redis')
    clients = [Redis.from_url(url, decode_responses=True) for _ in range(2)]
    prefix = 'temporary-store-test-' + uuid.uuid4().hex
    config = replace(TemporaryContextConfig(), context_bytes=100_000, user_bytes=300_000, store_bytes=600_000)
    repos = [TemporaryContextRepository(RedisTemporaryBackend(client, config, prefix=prefix)) for client in clients]
    await clients[0].ping()
    try:
        yield repos
    finally:
        # Never flush a database/shared prefix; only this fixture's random keys.
        keys = [key async for key in clients[0].scan_iter(match=prefix + ':*')]
        if keys:
            await clients[0].delete(*keys)
        for client in clients:
            await client.aclose()


async def register(repo, doc):
    await repo.register_tasks(
        doc['id'],
        'alice',
        doc['epoch'],
        [
            {
                'task_id': 'task',
                'reply_id': 'reply',
                'model_id': 'model',
                'branch_id': 'branch',
                'worker_nonce': 'worker',
            }
        ],
        expected_revision=0,
    )


async def begin(repo, doc, ordinal=0):
    return await repo.begin_execution(
        doc['id'],
        'alice',
        doc['epoch'],
        task_id='task',
        identity={'round_id': 'round', 'call_id': 'call', 'reply_id': 'reply', 'model_id': 'model', 'ordinal': ordinal},
        target_hash='a' * 64,
    )


@pytest.mark.asyncio
async def test_real_two_instances_cas_quota_and_single_hash_slot(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    assert len({key_slot(key.encode()) for key in first.backend.keys(doc['id'], 'alice')}) == 1

    def increment(current, now):
        current['data']['count'] = current['data'].get('count', 0) + 1

    await asyncio.gather(*(stores[i % 2].transact(*args, increment) for i in range(20)))
    assert (await second.read(*args))['data']['count'] == 20
    attempts = await asyncio.gather(*(stores[i % 2].create('alice') for i in range(8)), return_exceptions=True)
    assert sum(isinstance(item, dict) for item in attempts) == 3
    assert all(isinstance(item, (dict, ContextCapacity)) for item in attempts)
    client = first.backend.client
    keys = first.backend.keys(doc['id'], 'alice')
    assert await client.ttl(keys[0]) == -1
    context_keys = [key async for key in client.scan_iter(match=first.backend.prefix + ':ctx:*')]
    actual = 0
    for key in context_keys:
        record = await client.hgetall(key)
        actual += int(record['reserve']) + len(record['data'].encode()) + len(record['events'].encode())
    assert int(await client.hget(keys[1], 'bytes')) == actual


@pytest.mark.asyncio
async def test_real_cross_worker_execution_winner_and_atomic_raw_resolution(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await register(first, doc)
    results = await asyncio.gather(begin(first, doc), begin(second, doc))
    assert sum(started for _, started in results) == 1
    execution = results[0][0]
    await second.close(*args)
    with pytest.raises(ContextGone):
        await begin(first, doc, ordinal=1)
    await first.resolve_execution(
        *args,
        task_id='task',
        execution_id=execution['id'],
        raw_transition=lambda current, now: current['data'].update(raw='原始结果'),
    )
    current = await second.read(*args, allow_closed=True)
    assert current['data']['raw'] == '原始结果'
    assert current['executions'][execution['id']]['status'] == 'resolved'
    assert current['revision'] == 1
    assert (await second.acknowledge_task_done(*args, task_id='task'))['status'] == 'closed_confirmed'
    await first.sweep()
    current = await second.read(*args, allow_closed=True)
    assert current['status'] == 'tombstone' and current['data'] == {}


@pytest.mark.asyncio
async def test_real_result_capacity_preserves_original_and_uncertain_control(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await register(first, doc)
    await first.transact(*args, lambda current, now: current['data'].update(original='retained'))
    execution, _ = await begin(first, doc)
    with pytest.raises(ContextCapacity):
        await first.resolve_execution(
            *args,
            task_id='task',
            execution_id=execution['id'],
            raw_transition=lambda current, now: current['data'].update(raw='中' * 12_000),
        )
    current = await second.read(*args)
    assert current['data'] == {'original': 'retained'}
    assert current['executions'][execution['id']]['status'] == 'uncertain'
    assert (await begin(second, doc))[1] is False
    await first.close(*args)
    await first.acknowledge_task_done(*args, task_id='task')
    assert (await second.read(*args, allow_closed=True))['status'] == 'closing'


class LostReplyClient:
    def __init__(self, client, *, lose_reads=False):
        self.client = client
        self.lose_reads = lose_reads
        self.lost = False

    async def eval(self, script, count, *args):
        if self.lost and self.lose_reads:
            raise TimeoutError('synthetic unavailable storage')
        value = await self.client.eval(script, count, *args)
        if count == 4 and not self.lost:
            self.lost = True
            raise TimeoutError('synthetic lost response after commit')
        return value


@pytest.mark.asyncio
async def test_real_committed_start_with_lost_response_reconciles_without_repeat(stores):
    first, second = stores
    doc = await first.create('alice')
    await register(first, doc)
    first.backend.client = LostReplyClient(first.backend.client)
    execution, started = await begin(first, doc)
    assert started
    assert (await begin(second, doc))[1] is False
    assert execution['status'] == 'started'


@pytest.mark.asyncio
async def test_real_unknown_start_does_not_authorize_another_worker(stores):
    first, second = stores
    doc = await first.create('alice')
    await register(first, doc)
    first.backend.client = LostReplyClient(first.backend.client, lose_reads=True)
    with pytest.raises(ContextUnavailable):
        await begin(first, doc)
    assert (await begin(second, doc))[1] is False


@pytest.mark.asyncio
async def test_real_lease_and_event_writes_preserve_raw_and_revision(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await first.transact(*args, lambda current, now: current['data'].update(raw='中' * 5000), source_changed=True)
    key = first.backend.keys(doc['id'], 'alice')[0]
    raw = await first.backend.client.hget(key, 'data')
    await second.lease(*args, socket_id='socket')
    await first.append_event(*args, {'type': 'progress'})
    assert await first.backend.client.hget(key, 'data') == raw
    assert (await second.read(*args))['revision'] == 1


@pytest.mark.asyncio
async def test_real_missing_control_and_quota_keys_fail_closed(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    keys = first.backend.keys(doc['id'], 'alice')
    await first.backend.client.delete(keys[1])
    with pytest.raises(ContextUnavailable):
        await second.transact(*args, lambda current, now: current['data'].update(raw='x'))
    await first.backend.client.delete(keys[0])
    with pytest.raises(ContextGone) as exc:
        await second.read(*args)
    assert not exc.value.rebuild_allowed


@pytest.mark.asyncio
async def test_real_expiry_uses_server_time_and_retains_registered_task(stores):
    first, second = stores
    doc = await first.create('alice')
    await register(first, doc)
    args = (doc['id'], 'alice', doc['epoch'])
    key = first.backend.keys(doc['id'], 'alice')[0]
    control = json.loads(await first.backend.client.hget(key, 'control'))
    control['idle_deadline'] = 1
    await first.backend.client.hset(key, 'control', json.dumps(control))
    with pytest.raises(ContextGone):
        await second.read(*args)
    assert (await second.read(*args, allow_closed=True))['status'] == 'closing'
    assert await first.backend.client.ttl(key) == -1


@pytest.mark.asyncio
async def test_real_expired_execution_can_settle_without_a_prior_read_or_sweep(stores):
    first, second = stores
    doc = await first.create('alice')
    await register(first, doc)
    execution, _ = await begin(first, doc)
    args = (doc['id'], 'alice', doc['epoch'])
    key = first.backend.keys(doc['id'], 'alice')[0]
    control = json.loads(await first.backend.client.hget(key, 'control'))
    control['idle_deadline'] = 1
    await first.backend.client.hset(key, 'control', json.dumps(control))
    await second.resolve_execution(
        *args,
        task_id='task',
        execution_id=execution['id'],
        raw_transition=lambda current, now: current['data'].update(raw='settled after expiry'),
    )
    current = await first.read(*args, allow_closed=True)
    assert current['status'] == 'closing' and current['fence'] == 1
    assert current['data']['raw'] == 'settled after expiry'


@pytest.mark.asyncio
async def test_real_corrupted_control_is_typed_unavailable(stores):
    first, second = stores
    doc = await first.create('alice')
    key = first.backend.keys(doc['id'], 'alice')[0]
    await first.backend.client.hset(key, 'control', '{}')
    with pytest.raises(ContextUnavailable):
        await second.read(doc['id'], 'alice', doc['epoch'])


@pytest.mark.asyncio
@pytest.mark.parametrize('quota_index', [1, 2])
async def test_real_quota_loss_cannot_reset_limits_for_a_new_context(stores, quota_index):
    first, second = stores
    doc = await first.create('alice')
    await first.backend.client.delete(first.backend.keys(doc['id'], 'alice')[quota_index])
    with pytest.raises(ContextUnavailable):
        await second.create('alice')


@pytest.mark.asyncio
async def test_real_missing_data_preserves_fence_and_settlement_ledger(stores):
    first, second = stores
    doc = await first.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await register(first, doc)
    execution, _ = await begin(first, doc)
    key = first.backend.keys(doc['id'], 'alice')[0]
    await first.backend.client.hdel(key, 'data')
    with pytest.raises(ContextGone) as exc:
        await second.read(*args)
    assert not exc.value.rebuild_allowed
    assert (await second.close(*args))['status'] == 'closing'
    await second.mark_uncertain(*args, task_id='task', execution_id=execution['id'])
    assert (await second.acknowledge_task_done(*args, task_id='task'))['status'] == 'closing'
    control = json.loads(await first.backend.client.hget(key, 'control'))
    assert control['executions'][execution['id']]['status'] == 'uncertain'
    assert control['fence'] == 1
