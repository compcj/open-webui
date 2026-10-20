# tasks.py
import asyncio
import logging
from contextlib import suppress
from uuid import uuid4

from redis.asyncio import Redis

from open_webui.env import REDIS_KEY_PREFIX, REDIS_RESPONSE_STREAM_TTL, REDIS_TASK_TTL
from open_webui.utils.json_codec import JSONCodec, dumps_bytes

log = logging.getLogger(__name__)

# A dictionary to keep track of active tasks
tasks: dict[str, asyncio.Task] = {}
item_tasks = {}
response_streams: dict[str, dict] = {}


REDIS_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks'
REDIS_ITEM_TASKS_KEY = f'{REDIS_KEY_PREFIX}:tasks:item'
REDIS_RESPONSE_STREAMS_KEY = f'{REDIS_KEY_PREFIX}:tasks:response_streams'
REDIS_TASK_STOP_KEY = f'{REDIS_KEY_PREFIX}:tasks:stop'
REDIS_PUBSUB_CHANNEL = f'{REDIS_KEY_PREFIX}:tasks:commands'
REDIS_PUBSUB_RECONNECT_INTERVAL = 1.0
REDIS_PUBSUB_MAX_RECONNECT_INTERVAL = 30.0
REDIS_TASK_STOP_POLL_INTERVAL = 0.5
TASK_STOP_TIMEOUT = 5.0
REDIS_TASK_STOP_TTL = max(REDIS_TASK_TTL, 300)


def cancel_local_task(task_id: str):
    task = tasks.get(task_id)
    # Repeated stop requests must not interrupt cancellation-state persistence
    # or transport cleanup that is already running in the owning task.
    if task and not task.done() and not task.cancelling():
        task.cancel()
    return task


async def redis_task_command_listener(app):
    redis: Redis = app.state.redis
    reconnect_interval = REDIS_PUBSUB_RECONNECT_INTERVAL

    while True:
        pubsub = None
        try:
            # RedisCluster can't route a pubsub subscribe until initialize() fills its slot cache.
            await redis.initialize()

            pubsub = redis.pubsub()
            await pubsub.subscribe(REDIS_PUBSUB_CHANNEL)
            reconnect_interval = REDIS_PUBSUB_RECONNECT_INTERVAL

            async for message in pubsub.listen():
                if message['type'] != 'message':
                    continue
                try:
                    command = JSONCodec.loads(message['data'])
                    if command.get('action') != 'stop':
                        continue

                    cancel_local_task(command.get('task_id'))
                except Exception as e:
                    log.exception(f'Error handling distributed task command: {e}')
            log.warning('Redis task command listener stopped. Retrying.')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception(f'Redis task command listener failed. Retrying: {e}')
        finally:
            if pubsub:
                with suppress(Exception):
                    await pubsub.aclose()

        await asyncio.sleep(reconnect_interval)
        reconnect_interval = min(reconnect_interval * 2, REDIS_PUBSUB_MAX_RECONNECT_INTERVAL)


async def redis_task_heartbeat(app):
    redis: Redis = app.state.redis
    next_heartbeat = asyncio.get_running_loop().time() + REDIS_TASK_TTL / 4
    while True:
        await asyncio.sleep(REDIS_TASK_STOP_POLL_INTERVAL)
        try:
            task_ids = list(tasks)
            if not task_ids:
                continue
            # Pub/Sub is only a fast notification. Stop requests survive a
            # subscriber disconnect independently of the task's liveness lease.
            pipe = redis.pipeline(transaction=False)
            for task_id in task_ids:
                pipe.get(f'{REDIS_TASK_STOP_KEY}:{task_id}')
            for task_id, state in zip(task_ids, await pipe.execute()):
                if state in ('cancelled', b'cancelled'):
                    cancel_local_task(task_id)

            now = asyncio.get_running_loop().time()
            if REDIS_TASK_TTL > 0 and now >= next_heartbeat:
                pipe = redis.pipeline(transaction=False)
                for task_id in task_ids:
                    # EXPIRE cannot recreate a task already removed by cleanup.
                    pipe.expire(f'{REDIS_TASKS_KEY}:{task_id}', REDIS_TASK_TTL)
                await pipe.execute()
                next_heartbeat = now + REDIS_TASK_TTL / 4
        except Exception:
            log.exception('Redis task heartbeat failed')


### ------------------------------
### REDIS-ENABLED HANDLERS
### ------------------------------


async def redis_save_task(redis: Redis, task_id: str, item_id: str | None):
    pipe = redis.pipeline(transaction=False)
    pipe.set(f'{REDIS_TASKS_KEY}:{task_id}', '1', ex=REDIS_TASK_TTL or None)
    pipe.hset(REDIS_TASKS_KEY, task_id, item_id or '')
    if item_id:
        pipe.sadd(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
    await pipe.execute()


async def redis_cleanup_task(redis: Redis, task_id: str, item_id: str | None):
    pipe = redis.pipeline(transaction=False)
    pipe.delete(f'{REDIS_TASKS_KEY}:{task_id}')
    pipe.hdel(REDIS_TASKS_KEY, task_id)
    pipe.hdel(REDIS_RESPONSE_STREAMS_KEY, task_id)
    if item_id:
        pipe.srem(f'{REDIS_ITEM_TASKS_KEY}:{item_id}', task_id)
    await pipe.execute()


async def redis_list_tasks(redis: Redis, item_id: str | None = None) -> list[str]:
    task_ids = list(
        await redis.smembers(f'{REDIS_ITEM_TASKS_KEY}:{item_id}')
        if item_id is not None
        else await redis.hkeys(REDIS_TASKS_KEY)
    )
    if not task_ids or REDIS_TASK_TTL == 0:
        return task_ids

    pipe = redis.pipeline(transaction=False)
    for task_id in task_ids:
        pipe.exists(f'{REDIS_TASKS_KEY}:{task_id}')

    active = []
    for task_id, exists in zip(task_ids, await pipe.execute()):
        if exists:
            active.append(task_id)
        else:
            task_item_id = item_id if item_id is not None else await redis.hget(REDIS_TASKS_KEY, task_id)
            await redis_cleanup_task(redis, task_id, task_item_id or None)
    return active


async def redis_send_command(redis: Redis, command: dict):
    command_json = dumps_bytes(command)
    # RedisCluster doesn't expose publish() directly, but the
    # PUBLISH command broadcasts across all cluster nodes server-side.
    if hasattr(redis, 'nodes_manager'):
        await redis.execute_command('PUBLISH', REDIS_PUBSUB_CHANNEL, command_json)
    else:
        await redis.publish(REDIS_PUBSUB_CHANNEL, command_json)


async def cleanup_task(redis, task_id: str, id=None):
    """
    Remove a completed or canceled task from the global `tasks` dictionary.
    """
    if redis:
        await redis_cleanup_task(redis, task_id, id)
        # Only the owner calls cleanup_task. Expiry pruning calls
        # redis_cleanup_task directly and must never acknowledge cancellation.
        await redis.set(f'{REDIS_TASK_STOP_KEY}:{task_id}', 'done', ex=REDIS_TASK_STOP_TTL)

    tasks.pop(task_id, None)  # Remove the task if it exists
    response_streams.pop(task_id, None)

    # If an ID is provided, remove the task from the item_tasks dictionary
    if id and task_id in item_tasks.get(id, []):
        item_tasks[id].remove(task_id)
        if not item_tasks[id]:  # If no tasks left for this ID, remove the entry
            item_tasks.pop(id, None)


async def create_task(redis, coroutine, id=None, task_id=None):
    """
    Create a new asyncio task and add it to the global task dictionary.
    """
    task_id = task_id or str(uuid4())  # Generate a unique ID for the task
    task = asyncio.create_task(coroutine)  # Create the task

    # Add a done callback for cleanup
    task.add_done_callback(lambda t: asyncio.create_task(cleanup_task(redis, task_id, id)))
    tasks[task_id] = task

    # If an ID is provided, associate the task with that ID
    if id:
        if item_tasks.get(id):
            item_tasks[id].append(task_id)
        else:
            item_tasks[id] = [task_id]

    if redis:
        await redis_save_task(redis, task_id, id)

    return task_id, task


async def list_tasks(redis):
    """
    List all currently active task IDs.
    """
    if redis:
        return await redis_list_tasks(redis)
    return list(tasks.keys())


async def list_task_ids_by_item_id(redis, id):
    """
    List all tasks associated with a specific ID.
    """
    if redis:
        return await redis_list_tasks(redis, id)
    return list(item_tasks.get(id, []))


async def save_response_stream(
    redis,
    task_id: str | None,
    chat_id: str | None,
    message_id: str | None,
    content: str,
    output: list,
):
    if not task_id or not chat_id or not message_id:
        return

    data = {
        'chat_id': chat_id,
        'message_id': message_id,
        'content': content,
        'output': output,
    }

    if redis:
        await redis.hset(REDIS_RESPONSE_STREAMS_KEY, task_id, dumps_bytes(data))
        if REDIS_RESPONSE_STREAM_TTL > 0:
            with suppress(Exception):
                await redis.hexpire(REDIS_RESPONSE_STREAMS_KEY, REDIS_RESPONSE_STREAM_TTL, task_id)
    else:
        response_streams[task_id] = data


async def get_response_streams_by_chat_id(redis, chat_id: str) -> list[dict]:
    task_ids = await list_task_ids_by_item_id(redis, chat_id)
    if not task_ids:
        return []

    if redis:
        values = await redis.hmget(REDIS_RESPONSE_STREAMS_KEY, task_ids)
        streams = []
        for value in values:
            if not value:
                continue
            try:
                data = JSONCodec.loads(value)
            except Exception:
                continue
            if data.get('chat_id') == chat_id:
                streams.append(data)
        return streams

    return [
        stream for task_id in task_ids if (stream := response_streams.get(task_id)) and stream.get('chat_id') == chat_id
    ]


async def clear_response_stream(redis, task_id: str | None):
    if not task_id:
        return
    if redis:
        await redis.hdel(REDIS_RESPONSE_STREAMS_KEY, task_id)
    else:
        response_streams.pop(task_id, None)


async def stop_task(redis, task_id: str, *, allow_missing=False):
    """Request cancellation and acknowledge it only after the owner finishes."""
    task = cancel_local_task(task_id)
    if task:
        # wait() neither cancels the task again on timeout nor propagates a
        # disconnect of the stop request into the task's cleanup.
        done, _ = await asyncio.wait({task}, timeout=TASK_STOP_TIMEOUT)
        if done:
            return {'status': True, 'message': f'Task {task_id} stopped.'}
    elif redis:
        stop_key = f'{REDIS_TASK_STOP_KEY}:{task_id}'
        if await redis.get(stop_key) in ('done', b'done'):
            return {'status': True, 'message': f'Task {task_id} already stopped.'}
        if (
            not allow_missing
            and await redis.hget(REDIS_TASKS_KEY, task_id) is None
            and await redis.get(stop_key) is None
        ):
            return {'status': False, 'message': f'Task with ID {task_id} not found.'}

        # NX cannot overwrite an acknowledgement racing this request. Keep the
        # intent separate from the lease so expiry pruning cannot erase it.
        await redis.set(stop_key, 'cancelled', nx=True, ex=REDIS_TASK_STOP_TTL)
        try:
            await redis_send_command(redis, {'action': 'stop', 'task_id': task_id})
        except Exception:
            log.exception('Could not publish task cancellation; the owner will poll the saved request')

        deadline = asyncio.get_running_loop().time() + TASK_STOP_TIMEOUT
        while True:
            if await redis.get(stop_key) in ('done', b'done'):
                return {'status': True, 'message': f'Task {task_id} stopped.'}
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(REDIS_TASK_STOP_POLL_INTERVAL, remaining))
    else:
        return {'status': False, 'message': f'Task with ID {task_id} not found.'}

    return {'status': False, 'message': 'Task cancellation has not finished. Please try stopping again.'}


async def stop_item_tasks(redis: Redis, item_id: str):
    """
    Stop all tasks associated with a specific item ID.
    """
    task_ids = await list_task_ids_by_item_id(redis, item_id)
    if not task_ids:
        return {'status': True, 'message': f'No tasks found for item {item_id}.'}

    results = await asyncio.gather(*(stop_task(redis, task_id) for task_id in task_ids))
    if failed := next((result for result in results if not result.get('status')), None):
        return failed

    return {'status': True, 'message': f'All tasks for item {item_id} stopped.'}


async def has_active_tasks(redis, chat_id: str) -> bool:
    """Check if a chat has any active tasks."""
    task_ids = await list_task_ids_by_item_id(redis, chat_id)
    return len(task_ids) > 0
