import asyncio
from unittest.mock import AsyncMock

import pytest
from test_task_cancellation import worker, RedisDouble


@pytest.mark.asyncio
async def test_persistence_failure_never_starts_work_but_confirms_unstarted_owner():
    ns = worker()
    ns['redis_save_task'] = AsyncMock(side_effect=ConnectionError('unavailable'))
    effect = AsyncMock()
    completed = AsyncMock()
    with pytest.raises(ConnectionError):
        await ns['create_task'](RedisDouble(), effect(), task_id='task', on_complete=completed)
    effect.assert_not_awaited()
    completed.assert_awaited_once()
    assert 'task' not in ns['tasks']


@pytest.mark.asyncio
async def test_cancel_before_first_instruction_confirms_only_after_task_is_done():
    ns = worker()
    effects, confirmations = [], []

    async def operation():
        effects.append('started')

    async def completed():
        confirmations.append(task.done())

    _, task = await ns['create_task'](None, operation(), task_id='task', on_complete=completed)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    for _ in range(5):
        await asyncio.sleep(0)
    assert effects == []
    assert confirmations == [True]
