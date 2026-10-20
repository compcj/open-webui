"""Run the actual task registry with isolated workers and a lossy Redis transport."""

import ast
import asyncio
import json
import logging
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from test_streaming_error_recovery import runtime as streaming_runtime, tool_call_chunk


class RedisDouble:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.sets = {}
        self.published = []

    def pipeline(self, **kwargs):
        redis = self
        commands = []

        class Pipeline:
            def __getattr__(self, name):
                def enqueue(*args, **kwargs):
                    commands.append((name, args, kwargs))
                    return self

                return enqueue

            async def execute(self):
                return [await getattr(redis, name)(*args, **kwargs) for name, args, kwargs in commands]

        return Pipeline()

    async def set(self, key, value, *, ex=None, xx=False, nx=False, keepttl=False):
        if xx and key not in self.values:
            return None
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)

    async def exists(self, key):
        return int(key in self.values)

    async def expire(self, key, ttl):
        assert ttl > 0
        return await self.exists(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    async def hkeys(self, key):
        return list(self.hashes.get(key, {}))

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self.sets.get(key, set()).discard(value)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def publish(self, channel, command):
        # No subscribers: model a worker reconnecting when the stop is published.
        self.published.append((channel, json.loads(command)))
        return 0


def worker(ttl=300):
    path = Path(__file__).parents[1] / 'open_webui/tasks.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    # Keep the production state and functions; avoid env/config import side effects.
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    ns = {
        'asyncio': asyncio,
        'logging': logging,
        'suppress': suppress,
        'uuid4': uuid4,
        'Redis': RedisDouble,
        'JSONCodec': json,
        'dumps_bytes': lambda value: json.dumps(value).encode(),
        'REDIS_KEY_PREFIX': 'synthetic',
        'REDIS_TASK_TTL': ttl,
        'REDIS_RESPONSE_STREAM_TTL': 3600,
        '__name__': __name__,
    }
    exec(compile(tree, str(path), 'exec'), ns)
    # Keep the same algorithm with short test deadlines.
    ns['TASK_STOP_TIMEOUT'] = 0.2
    ns['REDIS_TASK_STOP_POLL_INTERVAL'] = 0.01
    return ns


async def finish(*tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_redis_stop_cancels_local_tool_without_pubsub_delivery():
    ns, redis = worker(), RedisDouble()
    entered, release = asyncio.Event(), asyncio.Event()
    continuations = []

    async def reply():
        entered.set()
        await release.wait()
        continuations.append('model continued after tool')

    task_id, task = await ns['create_task'](redis, reply(), id='chat')
    try:
        await entered.wait()
        result = await ns['stop_task'](redis, task_id)
        release.set()
        await asyncio.sleep(0)
        assert result['status'] is True
        assert task.cancelled(), 'Redis-enabled local task continued after the stop reported success'
        assert continuations == []
    finally:
        await finish(task)


@pytest.mark.asyncio
@pytest.mark.parametrize('ttl', [0, 300])
async def test_remote_worker_recovers_dropped_stop_before_acknowledging(ttl):
    owner, caller, redis = worker(ttl), worker(ttl), RedisDouble()
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    continuations = []

    async def reply():
        entered.set()
        try:
            await release.wait()
            continuations.append('model continued after tool')
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task_id, task = await owner['create_task'](redis, reply(), id='chat')
    heartbeat = asyncio.create_task(owner['redis_task_heartbeat'](SimpleNamespace(state=SimpleNamespace(redis=redis))))
    try:
        await entered.wait()
        result = await caller['stop_task'](redis, task_id)
        assert cancelled.is_set(), 'Stop was acknowledged before the owning worker cancelled the tool'
        assert result['status'] is True
        release.set()
        await asyncio.sleep(0)
        assert continuations == []
        assert await owner['list_task_ids_by_item_id'](redis, 'chat') == []
    finally:
        await finish(heartbeat, task)


@pytest.mark.asyncio
async def test_unacknowledged_stop_preserves_task_for_retry():
    owner, caller, redis = worker(), worker(), RedisDouble()
    task_id, task = await owner['create_task'](redis, asyncio.Event().wait(), id='chat')
    try:
        result = await caller['stop_task'](redis, task_id)
        assert result['status'] is False, 'An undelivered cancellation must not be reported as stopped'
        assert await caller['list_task_ids_by_item_id'](redis, 'chat') == [task_id]
    finally:
        await finish(task)


@pytest.mark.asyncio
async def test_remote_stop_persists_interrupted_tool_and_never_sends_continuation(streaming_runtime):
    owner, caller, redis = worker(), worker(), RedisDouble()
    entered = asyncio.Event()

    async def execute(*args):
        entered.set()
        await asyncio.Event().wait()

    streaming_runtime.ns['execute_tool_call'] = execute
    task_id, task = await owner['create_task'](redis, streaming_runtime.run([tool_call_chunk()]), id='saved-chat')
    heartbeat = asyncio.create_task(owner['redis_task_heartbeat'](SimpleNamespace(state=SimpleNamespace(redis=redis))))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result = await caller['stop_task'](redis, task_id)
        assert result['status'] is True
        assert task.cancelled()
        assert streaming_runtime.saved['done'] is True
        output = streaming_runtime.saved['output']
        assert next(item for item in output if item['type'] == 'function_call')['status'] == 'incomplete'
        assert any(event['type'] == 'chat:tasks:cancel' for event in streaming_runtime.events)
        streaming_runtime.ns['generate_chat_completion'].assert_not_awaited()
        streaming_runtime.ns['publish_chat_finished_event'].assert_not_awaited()
    finally:
        await finish(heartbeat, task)


@pytest.mark.asyncio
@pytest.mark.parametrize('use_redis', [False, True])
async def test_repeated_stop_waits_for_cleanup_without_cancelling_it_again(use_redis):
    ns, redis = worker(), RedisDouble() if use_redis else None
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    persisted = []

    async def reply():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            persisted.append('interrupted output')
            raise

    task_id, task = await ns['create_task'](redis, reply(), id='chat')
    stops = []
    try:
        await entered.wait()
        stops.append(asyncio.create_task(ns['stop_task'](redis, task_id)))
        await cleaning.wait()
        assert task_id in await ns['list_task_ids_by_item_id'](redis, 'chat')
        stops.append(asyncio.create_task(ns['stop_task'](redis, task_id)))
        await asyncio.sleep(0)
        release.set()
        assert all(result['status'] for result in await asyncio.gather(*stops))
        assert task.cancelled()
        assert persisted == ['interrupted output']
    finally:
        release.set()
        await finish(*stops, task)


@pytest.mark.asyncio
async def test_stop_all_reports_failure_and_cancels_every_task():
    ns = worker()
    entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def slow_cleanup():
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            raise

    first_id, first = await ns['create_task'](None, slow_cleanup(), id='chat')
    _, second = await ns['create_task'](None, asyncio.Event().wait(), id='chat')
    try:
        await entered.wait()
        result = await ns['stop_item_tasks'](None, 'chat')
        assert result['status'] is False
        assert cleaning.is_set()
        assert second.cancelled()
        assert await ns['list_task_ids_by_item_id'](None, 'chat') == [first_id]
    finally:
        release.set()
        await finish(first, second)


@pytest.mark.asyncio
async def test_remote_stop_survives_publish_failure():
    owner, caller, redis = worker(), worker(), RedisDouble()

    async def unavailable(*args):
        raise ConnectionError('Synthetic subscriber connection failure')

    redis.publish = unavailable
    task_id, task = await owner['create_task'](redis, asyncio.Event().wait(), id='chat')
    heartbeat = asyncio.create_task(owner['redis_task_heartbeat'](SimpleNamespace(state=SimpleNamespace(redis=redis))))
    try:
        result = await caller['stop_task'](redis, task_id)
        assert result['status'] is True
        assert task.cancelled()
    finally:
        await finish(heartbeat, task)


@pytest.mark.asyncio
async def test_expired_lease_is_not_a_cancellation_acknowledgement():
    owner, caller, redis = worker(), worker(), RedisDouble()
    published = asyncio.Event()

    async def publish(*args):
        published.set()
        return 0

    redis.publish = publish
    task_id, task = await owner['create_task'](redis, asyncio.Event().wait(), id='chat')
    stopping = asyncio.create_task(caller['stop_task'](redis, task_id))
    heartbeat = None
    try:
        await published.wait()
        # A disconnected worker's lease expires and a reader prunes its registry.
        await redis.delete(f'{owner["REDIS_TASKS_KEY"]}:{task_id}')
        assert await caller['list_task_ids_by_item_id'](redis, 'chat') == []
        await asyncio.sleep(caller['REDIS_TASK_STOP_POLL_INTERVAL'] * 2)
        assert not stopping.done(), 'Registry expiry was mistaken for an owner acknowledgement'
        assert not task.done()
        heartbeat = asyncio.create_task(
            owner['redis_task_heartbeat'](SimpleNamespace(state=SimpleNamespace(redis=redis)))
        )
        assert (await stopping)['status'] is True
        assert task.cancelled()
    finally:
        await finish(*([heartbeat] if heartbeat else []), stopping, task)


def chat_stop_endpoint(ns, chat):
    root = Path(__file__).parents[1] / 'open_webui'
    tree = ast.parse((root / 'main.py').read_text(encoding='utf-8'))
    endpoint = next(node for node in tree.body if getattr(node, 'name', '') == 'stop_tasks_by_chat_id_endpoint')
    endpoint.decorator_list = []
    endpoint.args.defaults = [ast.Constant(None)]
    chats_tree = ast.parse((root / 'models/chats.py').read_text(encoding='utf-8'))
    authors = next(node for node in ast.walk(chats_tree) if getattr(node, 'name', '') == 'filter_task_ids_by_user_id')
    approval_tree = ast.parse((root / 'utils/tool_approval.py').read_text(encoding='utf-8'))
    pending = next(node for node in approval_tree.body if getattr(node, 'name', '') == 'pending_tool_calls')
    module = ast.Module(
        body=[
            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
            endpoint,
            authors,
            pending,
        ],
        type_ignores=[],
    )
    ns['get_temporary_chat_session_id'] = lambda value: None
    ns['Chats'] = SimpleNamespace(
        get_accessible_chat_by_id=AsyncMock(return_value=chat),
        get_messages_map_by_chat_id=AsyncMock(return_value=chat.chat['history']['messages']),
        upsert_message_to_chat_by_id_and_message_id=AsyncMock(),
    )
    ns['get_event_emitter'] = AsyncMock(return_value=AsyncMock())
    exec(compile(ast.fix_missing_locations(module), str(root / 'main.py'), 'exec'), ns)

    async def filter_authors(chat, user_id, task_ids):
        return await ns['filter_task_ids_by_user_id'](None, chat, user_id, task_ids)

    ns['Chats'].filter_task_ids_by_user_id = filter_authors
    return ns['stop_tasks_by_chat_id_endpoint']


@pytest.mark.asyncio
async def test_saved_chat_stop_keeps_author_scope_and_reports_any_failure():
    chat = SimpleNamespace(
        user_id='owner',
        chat={
            'history': {
                'messages': {
                    task_id: {'user_id': actor, 'meta': {'task_id': task_id}}
                    for task_id, actor in [('slow', 'member'), ('other', 'owner'), ('fast', 'member')]
                }
            }
        },
    )

    async def stop(redis, task_id, **kwargs):
        return {'status': task_id != 'slow', 'message': task_id}

    ns = {
        'asyncio': asyncio,
        'list_task_ids_by_item_id': AsyncMock(return_value=['slow', 'other', 'fast']),
        'stop_task': AsyncMock(side_effect=stop),
    }
    result = await chat_stop_endpoint(ns, chat)(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
        'chat',
        SimpleNamespace(id='member'),
    )
    assert result == {'status': False, 'message': 'slow'}
    assert [call.args[1] for call in ns['stop_task'].await_args_list] == ['slow', 'fast']


@pytest.mark.asyncio
@pytest.mark.parametrize('retry', [False, True])
async def test_chat_stop_recovers_author_task_after_expiry_instead_of_finalizing_live_output(retry):
    owner, caller, redis = worker(), worker(), RedisDouble()
    task_id, task = await owner['create_task'](redis, asyncio.Event().wait(), id='chat')
    other_id, other = await owner['create_task'](redis, asyncio.Event().wait(), id='chat')
    chat = SimpleNamespace(
        user_id='owner',
        chat={
            'history': {
                'messages': {
                    'reply': {'role': 'assistant', 'user_id': 'member', 'done': False, 'meta': {'task_id': task_id}},
                    'other': {'role': 'assistant', 'user_id': 'owner', 'done': False, 'meta': {'task_id': other_id}},
                }
            }
        },
    )
    endpoint = chat_stop_endpoint(caller, chat)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis)))
    heartbeat = None
    try:
        if retry:
            assert (await endpoint(request, 'chat', SimpleNamespace(id='member')))['status'] is False
        for expired_id in (task_id, other_id):
            await redis.delete(f'{owner["REDIS_TASKS_KEY"]}:{expired_id}')
        assert await caller['list_task_ids_by_item_id'](redis, 'chat') == []
        result = await endpoint(request, 'chat', SimpleNamespace(id='member'))
        assert result['status'] is False, 'Missing lease must not finalize a still-running reply'
        caller['Chats'].upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()
        assert not task.done()
        heartbeat = asyncio.create_task(
            owner['redis_task_heartbeat'](SimpleNamespace(state=SimpleNamespace(redis=redis)))
        )
        assert (await endpoint(request, 'chat', SimpleNamespace(id='member')))['status'] is True
        assert task.cancelled()
        assert not other.done(), 'Recovering expired tasks must preserve the reply author boundary'
    finally:
        await finish(*([heartbeat] if heartbeat else []), task, other)


@pytest.mark.asyncio
async def test_chat_stop_can_still_reject_paused_approval_after_task_acknowledgement_expires():
    ns, redis = worker(), RedisDouble()
    chat = SimpleNamespace(
        user_id='author',
        chat={
            'history': {
                'messages': {
                    'reply': {
                        'role': 'assistant',
                        'done': False,
                        'meta': {'task_id': 'finished-long-ago'},
                        'output': [
                            {'type': 'function_call', 'call_id': 'call', 'status': 'pending'},
                        ],
                    },
                }
            }
        },
    )
    result = await chat_stop_endpoint(ns, chat)(
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis))),
        'chat',
        SimpleNamespace(id='author'),
    )
    assert result['status'] is True
    saved = ns['Chats'].upsert_message_to_chat_by_id_and_message_id.await_args.args[2]
    assert saved['done'] is True
    assert saved['output'][0]['status'] == 'rejected'
    assert redis.published == []
