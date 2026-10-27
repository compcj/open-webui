import asyncio
from dataclasses import replace

import pytest

from open_webui.utils.context_store_errors import ContextCapacity, ContextConflict, ContextGone, ContextNotFound
from open_webui.utils.temporary_context_store import (
    MemoryTemporaryBackend,
    TemporaryContextConfig,
    TemporaryContextRepository,
)


def repository(**changes):
    config = replace(TemporaryContextConfig(), **changes)
    clock = [1000.0]
    backend = MemoryTemporaryBackend(config, clock=lambda: clock[0])
    return TemporaryContextRepository(backend), clock


async def task(repo, doc, task_id='task-1'):
    return await repo.register_tasks(
        doc['id'],
        doc['actor_id'],
        doc['epoch'],
        [
            {
                'task_id': task_id,
                'reply_id': 'reply-1',
                'model_id': 'model-1',
                'branch_id': 'branch-1',
                'worker_nonce': 'worker-1',
            }
        ],
        expected_revision=doc['revision'],
    )


async def begin(repo, doc, task_id='task-1', ordinal=0):
    return await repo.begin_execution(
        doc['id'],
        doc['actor_id'],
        doc['epoch'],
        task_id=task_id,
        identity={
            'round_id': 'round-1',
            'ordinal': ordinal,
            'call_id': 'call-1',
            'reply_id': 'reply-1',
            'model_id': 'model-1',
        },
        target_hash='a' * 64,
    )


@pytest.mark.asyncio
async def test_owner_epoch_snapshot_and_revision_are_separate():
    repo, _ = repository()
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    with pytest.raises(ContextNotFound):
        await repo.read(doc['id'], 'admin', doc['epoch'])
    with pytest.raises(ContextGone) as exc:
        await repo.read(doc['id'], 'alice', 'old')
    assert exc.value.rebuild_allowed is False
    doc['data']['evil'] = True
    assert not (await repo.read(*args))['data']
    leased = await repo.lease(*args, socket_id='socket-1')
    assert leased['revision'] == 0 and leased['socket']['generation'] == 1
    committed, result = await repo.transact(
        *args, lambda d, now: d['data'].update(raw='中文'), expected_revision=0, source_changed=True
    )
    assert committed['revision'] == 1 and result is None
    with pytest.raises(ContextConflict):
        await repo.transact(*args, lambda d, now: None, expected_revision=0)


@pytest.mark.asyncio
async def test_concurrent_create_cannot_exceed_count_or_bytes():
    repo, _ = repository(max_contexts_per_user=2)
    outcomes = await asyncio.gather(*(repo.create('alice') for _ in range(8)), return_exceptions=True)
    assert sum(isinstance(value, dict) for value in outcomes) == 2
    assert all(isinstance(value, (dict, ContextCapacity)) for value in outcomes)


@pytest.mark.asyncio
async def test_cas_retries_pure_mutation_without_lost_updates():
    repo, _ = repository()
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])

    def increment(d, now):
        d['data']['count'] = d['data'].get('count', 0) + 1

    await asyncio.gather(*(repo.transact(*args, increment) for _ in range(30)))
    assert (await repo.read(*args))['data']['count'] == 30


@pytest.mark.asyncio
async def test_close_fences_new_work_but_allows_original_task_settlement():
    repo, _ = repository()
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await task(repo, doc)
    execution, started = await begin(repo, doc)
    assert started
    duplicate, started = await begin(repo, doc)
    assert not started and duplicate['id'] == execution['id']
    closing = await repo.close(*args)
    assert closing['status'] == 'closing' and closing['fence'] == 1
    assert (await repo.close(*args))['fence'] == 1
    with pytest.raises(ContextGone):
        await begin(repo, doc, ordinal=1)
    with pytest.raises(ContextGone):
        await repo.transact(*args, lambda d, now: d['data'].update(forbidden=True))
    await repo.resolve_execution(
        *args,
        task_id='task-1',
        execution_id=execution['id'],
        raw_transition=lambda d, now: d['data'].update(raw='original'),
    )
    final = await repo.acknowledge_task_done(*args, task_id='task-1')
    assert final['status'] == 'closed_confirmed'
    assert (await repo.read(*args, allow_closed=True))['data']['raw'] == 'original'


@pytest.mark.asyncio
async def test_oversized_utf8_result_is_uncertain_and_never_redispatched():
    repo, _ = repository(context_bytes=70_000)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await task(repo, doc)
    execution, _ = await begin(repo, doc)
    with pytest.raises(ContextCapacity):
        await repo.resolve_execution(
            *args,
            task_id='task-1',
            execution_id=execution['id'],
            raw_transition=lambda d, now: d['data'].update(raw='中' * 2000),
        )
    current = await repo.read(*args)
    assert current['data'] == {}
    assert current['executions'][execution['id']]['status'] == 'uncertain'
    _, started = await begin(repo, doc)
    assert not started
    await repo.acknowledge_task_done(*args, task_id='task-1')
    assert (await repo.close(*args))['status'] == 'closing'


@pytest.mark.asyncio
async def test_expiry_never_reclaims_long_running_or_uncertain_work():
    repo, clock = repository(idle_seconds=10, disconnected_seconds=5)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await task(repo, doc)
    execution, _ = await begin(repo, doc)
    clock[0] += 11
    with pytest.raises(ContextGone):
        await repo.read(*args)
    await repo.sweep()
    retained = await repo.read(*args, allow_closed=True)
    assert retained['status'] == 'closing'
    await repo.acknowledge_task_done(*args, task_id='task-1')
    await repo.sweep()
    retained = await repo.read(*args, allow_closed=True)
    assert retained['executions'][execution['id']]['status'] == 'uncertain'
    assert retained['status'] == 'closing'


@pytest.mark.asyncio
async def test_confirmed_tombstones_retire_but_missing_never_means_safe_rebuild():
    repo, clock = repository(tombstone_seconds=10)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.close(*args)
    await repo.sweep()
    tombstone = await repo.read(*args, allow_closed=True)
    assert tombstone['rebuild_allowed'] and tombstone['status'] == 'tombstone'
    clock[0] += 11
    await repo.sweep()
    with pytest.raises(ContextGone) as exc:
        await repo.read(*args, allow_closed=True)
    assert not exc.value.rebuild_allowed


@pytest.mark.asyncio
async def test_events_gap_preserves_canonical_data_and_source_revision():
    repo, _ = repository(max_events=2)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await repo.transact(*args, lambda d, now: d['data'].update(raw='canonical'))
    for i in range(4):
        await repo.append_event(*args, {'type': 'progress', 'value': i})
    current = await repo.read(*args)
    assert current['event_seq'] == 4 and len(current['events']) == 2
    assert current['events_dropped_through'] == 2
    assert current['revision'] == 0 and current['data']['raw'] == 'canonical'


@pytest.mark.asyncio
async def test_memory_restart_invalidates_old_context_and_multi_worker_rejected():
    repo, _ = repository()
    doc = await repo.create('alice')
    restarted, _ = repository()
    with pytest.raises(ContextGone) as exc:
        await restarted.read(doc['id'], 'alice', doc['epoch'])
    assert not exc.value.rebuild_allowed
    with pytest.raises(ValueError):
        MemoryTemporaryBackend(workers=2)


def test_configuration_rejects_infeasible_reserve_and_invalid_values():
    with pytest.raises(ValueError):
        TemporaryContextConfig(context_bytes=65536)
    with pytest.raises(ValueError):
        TemporaryContextConfig(idle_seconds=0)


@pytest.mark.asyncio
async def test_control_reserve_admission_leaves_room_to_settle_every_execution():
    repo, _ = repository(control_reserve_bytes=4096, max_executions=100)
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await task(repo, doc)
    executions = []
    for ordinal in range(100):
        try:
            record, started = await begin(repo, doc, ordinal=ordinal)
            assert started
            executions.append(record)
        except ContextCapacity:
            break
    assert executions and len(executions) < 100
    await repo.close(*args)
    for record in executions:
        await repo.mark_uncertain(*args, task_id='task-1', execution_id=record['id'])
    final = await repo.acknowledge_task_done(*args, task_id='task-1')
    assert final['status'] == 'closing'


@pytest.mark.asyncio
async def test_raw_callback_cannot_add_unbounded_control_fields():
    repo, _ = repository()
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    await task(repo, doc)
    record, _ = await begin(repo, doc)
    with pytest.raises(ContextConflict):
        await repo.resolve_execution(
            *args,
            task_id='task-1',
            execution_id=record['id'],
            raw_transition=lambda d, now: d.update(arbitrary_control='forged'),
        )


@pytest.mark.asyncio
async def test_generic_transaction_rejects_control_forgery_and_async_callback():
    repo, _ = repository()
    doc = await repo.create('alice')
    args = (doc['id'], 'alice', doc['epoch'])
    with pytest.raises(ContextConflict):
        await repo.transact(*args, lambda d, now: d.update(fence=20))

    async def invalid(d, now):
        d['data']['raw'] = 'never'

    with pytest.raises(TypeError):
        await repo.transact(*args, invalid)
    assert not (await repo.read(*args))['data']


@pytest.mark.asyncio
async def test_factory_configured_failure_never_constructs_memory(monkeypatch):
    import sys
    from types import SimpleNamespace
    from open_webui.utils.temporary_context_store import create_temporary_context_repository
    from open_webui.utils.context_store_errors import ContextUnavailable

    calls = []

    def connect(url, **kwargs):
        calls.append(kwargs)
        raise OSError('synthetic storage outage')

    monkeypatch.setitem(sys.modules, 'open_webui.utils.redis', SimpleNamespace(get_redis_connection=connect))
    with pytest.raises(ContextUnavailable):
        await create_temporary_context_repository(
            redis_url='redis://example.invalid', redis_sentinels=[('sentinel.invalid', 26379)], redis_cluster=True
        )
    assert calls == [
        {
            'redis_sentinels': [('sentinel.invalid', 26379)],
            'redis_cluster': True,
            'async_mode': True,
            'decode_responses': True,
        }
    ]


@pytest.mark.asyncio
async def test_factory_does_not_downgrade_sentinel_tls(monkeypatch):
    import sys
    from types import SimpleNamespace
    from open_webui.utils.temporary_context_store import create_temporary_context_repository
    from open_webui.utils.context_store_errors import ContextUnavailable

    calls = []
    monkeypatch.setitem(
        sys.modules,
        'open_webui.utils.redis',
        SimpleNamespace(get_redis_connection=lambda *args, **kwargs: calls.append(args)),
    )
    with pytest.raises(ContextUnavailable):
        await create_temporary_context_repository(
            redis_url='rediss://master', redis_sentinels=[('sentinel.invalid', 26379)]
        )
    assert not calls


@pytest.mark.asyncio
async def test_public_transition_cannot_commit_invalid_data_type():
    repo, _ = repository()
    doc = await repo.create('alice')
    with pytest.raises(ContextConflict):
        await repo.transact(doc['id'], 'alice', doc['epoch'], lambda d, now: d.update(data=[]))


def test_store_modules_import_without_database_or_app_bootstrap():
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            '-c',
            "import sys; sys.path.insert(0, 'backend'); import open_webui.utils.temporary_context_store; "
            "assert not any(x == 'sqlalchemy' or x.startswith('open_webui.models') or "
            "x in ('open_webui.main', 'open_webui.internal.db', 'open_webui.env') for x in sys.modules)",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_expired_lease_persists_fence_before_returning_gone():
    repo, clock = repository(idle_seconds=10)
    doc = await repo.create('alice')
    await task(repo, doc)
    clock[0] += 11
    with pytest.raises(ContextGone):
        await repo.lease(doc['id'], 'alice', doc['epoch'], socket_id='late')
    retained, _ = await repo.backend.load(doc['id'], include_data=False)
    assert retained['status'] == 'closing' and retained['fence'] == 1
