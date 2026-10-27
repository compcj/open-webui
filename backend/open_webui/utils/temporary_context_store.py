"""Bounded temporary context storage and execution lifecycle.

Transitions are synchronous, side-effect-free callbacks retried only after CAS
conflicts. Control, data and events share a CAS version; leases/events do not
fetch or rewrite data. Configured Redis is authoritative, with no fallback.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import math
import os
import time
import uuid
from dataclasses import dataclass, fields

from open_webui.utils.context_store_errors import (
    ContextCapacity,
    ContextConflict,
    ContextGone,
    ContextNotFound,
    ContextStoreError,
    ContextUnavailable,
)


@dataclass(frozen=True)
class TemporaryContextConfig:
    idle_seconds: int = 1800
    disconnected_seconds: int = 300
    maximum_seconds: int = 86400
    lease_seconds: int = 60
    context_bytes: int = 64 * 1024 * 1024
    user_bytes: int = 128 * 1024 * 1024
    store_bytes: int = 512 * 1024 * 1024
    max_contexts_per_user: int = 4
    control_reserve_bytes: int = 64 * 1024
    max_tasks: int = 32
    max_executions: int = 128
    max_events: int = 128
    event_bytes: int = 256 * 1024
    tombstone_seconds: int = 300
    cas_retries: int = 32

    def __post_init__(self):
        for field in fields(self):
            if type(getattr(self, field.name)) is not int or getattr(self, field.name) <= 0:
                raise ValueError(f'{field.name} must be a positive integer')
        if self.control_reserve_bytes < 4096 or self.context_bytes <= self.control_reserve_bytes + 512:
            raise ValueError('context_bytes must accommodate control reserve and data')
        if self.user_bytes < self.context_bytes or self.store_bytes < self.user_bytes:
            raise ValueError('context/user/store byte limits must be ordered')

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        values = {}
        for field in fields(cls):
            name = 'TEMPORARY_CONTEXT_' + field.name.upper()
            if name in environ:
                try:
                    values[field.name] = int(environ[name])
                except (TypeError, ValueError):
                    raise ValueError(f'{name} must be a positive integer') from None
        return cls(**values)


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)


def _bytes(value):
    return len(_encode(value).encode('utf-8'))


def _identifier(value, name='identifier'):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 128:
        raise ValueError(f'{name} must contain 1 to 128 UTF-8 bytes')
    return value


def _task_digest(task_id):
    return hashlib.sha256(_identifier(task_id, 'task_id').encode('utf-8')).hexdigest()


def _segments(doc, include_data=True):
    control = {key: value for key, value in doc.items() if key not in ('data', 'events')}
    return _encode(control), _encode(doc['data']) if include_data else None, _encode(doc['events'])


def _assemble(control, data, events):
    doc = json.loads(control)
    doc['data'] = json.loads(data) if data is not None else None
    doc['events'] = json.loads(events)
    _validate_document(doc)
    return doc


def _validate_document(doc):
    """Malformed retained control is a storage fault, never execution authority."""
    try:
        for key in ('id', 'actor_id', 'epoch', 'incarnation', 'last_operation'):
            _identifier(doc[key], key)
        for key in ('revision', 'version', 'fence', 'event_seq', 'events_dropped_through'):
            if type(doc[key]) is not int or not 0 <= doc[key] <= 2**53 - 1:
                raise ValueError()
        for key in ('created_at', 'idle_deadline', 'maximum_deadline', 'next_sweep_at'):
            if type(doc[key]) not in (int, float) or not math.isfinite(doc[key]):
                raise ValueError()
        for key in ('disconnected_deadline', 'stop_requested_at'):
            if doc[key] is not None and (type(doc[key]) not in (int, float) or not math.isfinite(doc[key])):
                raise ValueError()
        if doc['status'] not in ('open', 'closing', 'closed_confirmed', 'tombstone'):
            raise ValueError()
        if type(doc['rebuild_allowed']) is not bool:
            raise ValueError()
        if doc['data'] is not None and not isinstance(doc['data'], dict):
            raise ValueError()
        if not isinstance(doc['tasks'], dict) or not isinstance(doc['executions'], dict):
            raise ValueError()
        socket = doc['socket']
        if socket['id'] is not None:
            _identifier(socket['id'])
        if type(socket['generation']) is not int or socket['generation'] < 0 or type(socket['connected']) is not bool:
            raise ValueError()
        for task_id, task in doc['tasks'].items():
            if task['task_id'] != task_id or task['status'] not in ('registered', 'done'):
                raise ValueError()
            for key in ('task_id', 'reply_id', 'model_id', 'branch_id', 'worker_nonce'):
                _identifier(task[key])
            if type(task['fence']) is not int or not 0 <= task['fence'] <= doc['fence']:
                raise ValueError()
        for execution_id, entry in doc['executions'].items():
            task = doc['tasks'][entry['task_id']]
            if entry['id'] != execution_id or entry['status'] not in ('started', 'uncertain', 'resolved'):
                raise ValueError()
            if entry['fence'] != task['fence']:
                raise ValueError()
            for key in ('reply_id', 'model_id'):
                if entry['identity'][key] != task[key]:
                    raise ValueError()
            _identifier(entry['target_hash'])
        if not isinstance(doc['events'], list) or doc['events_dropped_through'] > doc['event_seq']:
            raise ValueError()
        previous_seq = doc['events_dropped_through']
        for event in doc['events']:
            if not previous_seq < event['seq'] <= doc['event_seq']:
                raise ValueError()
            if event['temporary_context']['id'] != doc['id'] or event['temporary_context']['epoch'] != doc['epoch']:
                raise ValueError()
            previous_seq = event['seq']
        if doc['status'] in ('closed_confirmed', 'tombstone') and (not _settled(doc) or not doc['rebuild_allowed']):
            raise ValueError()
        if doc['status'] == 'tombstone' and not math.isfinite(doc['retire_at']):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ContextUnavailable() from None


def _charge(control, data, events, config):
    if len(control.encode('utf-8')) > config.control_reserve_bytes:
        raise ContextCapacity(limit='control')
    return config.control_reserve_bytes + len(data.encode('utf-8')) + len(events.encode('utf-8'))


def _deadline(doc):
    if doc['status'] == 'tombstone':
        return doc['retire_at']
    if doc['status'] == 'closed_confirmed':
        return 0
    if doc['status'] == 'closing':
        return doc['next_sweep_at']
    return min(doc['idle_deadline'], doc['maximum_deadline'], doc['disconnected_deadline'] or math.inf)


def _sweep_due(doc):
    return min(_deadline(doc), doc['next_sweep_at']) if doc['status'] == 'open' else _deadline(doc)


def _settled(doc):
    return all(t['status'] == 'done' for t in doc['tasks'].values()) and all(
        e['status'] == 'resolved' and not e.get('publication_pending') for e in doc['executions'].values()
    )


def _finish_close(doc):
    if doc['status'] == 'closing' and _settled(doc):
        doc['status'] = 'closed_confirmed'
        doc['rebuild_allowed'] = True


def _fence(doc, now, config):
    if doc['status'] == 'open':
        doc['status'] = 'closing'
        doc['fence'] += 1
        doc['stop_requested_at'] = now
        doc['next_sweep_at'] = now + config.lease_seconds
    _finish_close(doc)


def _control_admission(doc, config):
    control, _, _ = _segments(doc, include_data=False)
    # Terminal timestamps/status/reason and close fields have precharged room.
    slack = 1024 + 256 * (len(doc['tasks']) + len(doc['executions']))
    if len(control.encode('utf-8')) + slack > config.control_reserve_bytes:
        raise ContextCapacity(limit='control')


class MemoryTemporaryBackend:
    """Explicitly single-worker storage; lost incarnations are never safe rebuilds."""

    def __init__(self, config=None, *, clock=time.time, incarnation=None, workers=1):
        known = [workers]
        for name in ('UVICORN_WORKERS', 'WEB_CONCURRENCY', 'GUNICORN_WORKERS'):
            if os.environ.get(name):
                known.append(int(os.environ[name]))
        if any(int(value) != 1 for value in known):
            raise ValueError('Temporary memory storage requires a single worker')
        self.config = config or TemporaryContextConfig()
        self.clock = clock
        self.incarnation = incarnation or uuid.uuid4().hex
        self._records = {}
        self._deferred = {}
        self._lock = asyncio.Lock()

    async def now(self):
        return float(self.clock())

    async def load(self, context_id, *, include_data=True):
        async with self._lock:
            stored = self._records.get(context_id)
            if stored is None:
                raise ContextGone()
            control, data, events = stored
            return _assemble(control, data if include_data else None, events), float(self.clock())

    async def commit(self, doc, *, expected_version, operation_id, include_data=True):
        async with self._lock:
            old = self._records.get(doc['id'])
            if old is not None:
                previous = json.loads(old[0])
                if previous['last_operation'] == operation_id:
                    return
                if expected_version is None or previous['version'] != expected_version:
                    raise ContextConflict()
            elif expected_version is not None:
                raise ContextGone()
            control, data, events = _segments(doc, include_data)
            if doc['status'] == 'open' and (
                _deadline(doc) <= self.clock()
                or (old is not None and previous['status'] == 'open' and _deadline(previous) <= self.clock())
            ):
                raise ContextConflict()
            if data is None:
                data = old[1]
            size = _charge(control, data, events, self.config)
            actor = doc['actor_id']
            total = user_total = user_count = 0
            for key, record in self._records.items():
                if key == doc['id']:
                    continue
                c = json.loads(record[0])
                charge = _charge(*record, self.config)
                total += charge
                if c['actor_id'] == actor:
                    user_total += charge
                    user_count += c['status'] != 'tombstone'
            for amount, maximum, limit in (
                (size, self.config.context_bytes, 'context'),
                (user_total + size, self.config.user_bytes, 'user'),
                (total + size, self.config.store_bytes, 'store'),
            ):
                if amount > maximum:
                    raise ContextCapacity(limit=limit)
            if user_count + (doc['status'] != 'tombstone') > self.config.max_contexts_per_user:
                raise ContextCapacity(limit='live_contexts')
            self._records[doc['id']] = (control, data, events)

    async def due(self, limit):
        async with self._lock:
            now = float(self.clock())
            due = []
            for key, value in self._records.items():
                if self._deferred.get(key, 0) > now:
                    continue
                try:
                    deadline = _sweep_due(json.loads(value[0]))
                except (ValueError, TypeError, KeyError):
                    deadline = 0
                if deadline <= now:
                    due.append(key)
                    if len(due) >= limit:
                        break
            return due

    async def defer(self, context_id, seconds):
        async with self._lock:
            if context_id in self._records:
                self._deferred[context_id] = float(self.clock()) + seconds

    async def retire(self, doc):
        async with self._lock:
            record = self._records.get(doc['id'])
            if record is None:
                return
            current = json.loads(record[0])
            if current['version'] != doc['version']:
                raise ContextConflict()
            if current['status'] != 'tombstone' or current['retire_at'] > self.clock():
                raise ContextConflict()
            del self._records[doc['id']]
            self._deferred.pop(doc['id'], None)


# All four keys share a deployment-prefixed hash slot. Redis computes byte
# deltas from retained and proposed values rather than trusting caller deltas.
_REDIS_COMMIT = r"""
local old = redis.call('HGET', KEYS[1], 'control')
local expected = tonumber(ARGV[1])
local proposed = cjson.decode(ARGV[2])
local previous = nil
if old then
    previous = cjson.decode(old)
    if previous.last_operation == ARGV[5] then return {'OK'} end
    if expected < 0 or previous.version ~= expected then return {'CONFLICT'} end
elseif expected >= 0 then return {'GONE'} end
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
-- A proposal must not renew a retained lease that expired while this CAS waited.
if previous and previous.status == 'open' and proposed.status == 'open' and
    (previous.maximum_deadline <= now or previous.idle_deadline <= now or
     (previous.disconnected_deadline ~= cjson.null and previous.disconnected_deadline <= now)) then
    return {'EXPIRED'}
end
if proposed.status == 'open' and (proposed.maximum_deadline <= now or proposed.idle_deadline <= now
    or (proposed.disconnected_deadline ~= cjson.null and proposed.disconnected_deadline <= now)) then
    return {'EXPIRED'}
end
local data = ARGV[3]
if ARGV[6] == '0' then data = redis.call('HGET', KEYS[1], 'data') end
local events = ARGV[4]
local reserve = tonumber(ARGV[7])
if string.len(ARGV[2]) > reserve then return {'CAPACITY', 'control'} end
local datasize = data and string.len(data) or tonumber(redis.call('HGET', KEYS[1], 'data_bytes'))
if not datasize then return {'UNAVAILABLE'} end
if not data and proposed.status == 'open' then return {'GONE'} end
local size = reserve + datasize + string.len(events)
local oldsize = 0
local oldlive = 0
if old then
    local olddata = redis.call('HGET', KEYS[1], 'data')
    local oldevents = redis.call('HGET', KEYS[1], 'events')
    if not oldevents then return {'GONE'} end
    local olddatasize = olddata and string.len(olddata) or tonumber(redis.call('HGET', KEYS[1], 'data_bytes'))
    if not olddatasize then return {'UNAVAILABLE'} end
    if not olddata and ARGV[6] == '1' and proposed.status ~= 'tombstone' then return {'GONE'} end
    oldsize = tonumber(redis.call('HGET', KEYS[1], 'reserve')) + olddatasize + string.len(oldevents)
    if previous.status ~= 'tombstone' then oldlive = 1 end
end
local live = 1
if proposed.status == 'tombstone' then live = 0 end
local total = tonumber(redis.call('HGET', KEYS[2], 'bytes') or '0')
local user = tonumber(redis.call('HGET', KEYS[3], 'bytes') or '0')
local count = tonumber(redis.call('HGET', KEYS[3], 'count') or '0')
if redis.call('HEXISTS', KEYS[2], 'bytes') == 0 and redis.call('ZCARD', KEYS[4]) > 0 then
    return {'UNAVAILABLE'}
end
local registereduser = tonumber(redis.call('HGET', KEYS[2], KEYS[3]) or '0')
if registereduser ~= user then return {'UNAVAILABLE'} end
if old and (total < oldsize or user < oldsize or count < oldlive) then return {'UNAVAILABLE'} end
if size > tonumber(ARGV[8]) then return {'CAPACITY', 'context'} end
if user - oldsize + size > tonumber(ARGV[9]) then return {'CAPACITY', 'user'} end
if total - oldsize + size > tonumber(ARGV[10]) then return {'CAPACITY', 'store'} end
if count - oldlive + live > tonumber(ARGV[11]) then return {'CAPACITY', 'live_contexts'} end
redis.call('HSET', KEYS[1], 'control', ARGV[2], 'events', events, 'reserve', ARGV[7], 'data_bytes', datasize)
if ARGV[6] == '1' then redis.call('HSET', KEYS[1], 'data', data) end
redis.call('HSET', KEYS[2], 'bytes', total - oldsize + size)
redis.call('HSET', KEYS[2], KEYS[3], user - oldsize + size)
redis.call('HSET', KEYS[3], 'bytes', user - oldsize + size, 'count', count - oldlive + live)
redis.call('ZADD', KEYS[4], ARGV[12], proposed.id)
return {'OK'}
"""

_REDIS_LOAD = r"""
local control = redis.call('HGET', KEYS[1], 'control')
if not control then return {'GONE'} end
local data = ''
if ARGV[1] == '1' then data = redis.call('HGET', KEYS[1], 'data') end
local events = redis.call('HGET', KEYS[1], 'events')
if not data or not events then return {'GONE'} end
local clock = redis.call('TIME')
return {'OK', control, data, events, clock[1], clock[2]}
"""

_REDIS_RETIRE = r"""
local control = redis.call('HGET', KEYS[1], 'control')
if not control then return {'OK'} end
local current = cjson.decode(control)
local clock = redis.call('TIME')
if current.version ~= tonumber(ARGV[1]) or current.status ~= 'tombstone'
    or current.retire_at > tonumber(clock[1]) + tonumber(clock[2]) / 1000000 then return {'CONFLICT'} end
local data = redis.call('HGET', KEYS[1], 'data')
local events = redis.call('HGET', KEYS[1], 'events')
local reserve = redis.call('HGET', KEYS[1], 'reserve')
if not data or not events or not reserve then return {'UNAVAILABLE'} end
local size = tonumber(reserve) + string.len(data) + string.len(events)
local total = tonumber(redis.call('HGET', KEYS[2], 'bytes') or '0')
local user = tonumber(redis.call('HGET', KEYS[3], 'bytes') or '0')
if total < size or user < size then return {'UNAVAILABLE'} end
redis.call('HSET', KEYS[2], 'bytes', total - size)
redis.call('HSET', KEYS[3], 'bytes', user - size)
redis.call('HSET', KEYS[2], KEYS[3], user - size)
redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[4], current.id)
if user == size and tonumber(redis.call('HGET', KEYS[3], 'count') or '0') == 0 then
    redis.call('DEL', KEYS[3])
    redis.call('HDEL', KEYS[2], KEYS[3])
end
return {'OK'}
"""

_REDIS_DUE = r"""
local clock = redis.call('TIME')
return redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', tonumber(clock[1]) + tonumber(clock[2]) / 1000000,
                  'LIMIT', 0, ARGV[1])
"""


_REDIS_DEFER = r"""
local clock = redis.call('TIME')
local next_attempt = tonumber(clock[1]) + tonumber(clock[2]) / 1000000 + tonumber(ARGV[2])
local current = redis.call('ZSCORE', KEYS[1], ARGV[1])
if current and tonumber(current) < next_attempt then
    redis.call('ZADD', KEYS[1], next_attempt, ARGV[1])
end
return {'OK'}
"""


class RedisTemporaryBackend:
    def __init__(self, client, config=None, *, prefix='open-webui'):
        if client is None:
            raise ContextUnavailable()
        if not isinstance(prefix, str) or not prefix or len(prefix) > 128 or '{' in prefix or '}' in prefix:
            raise ValueError('Redis prefix must be bounded and cannot contain hash tags')
        self.client = client
        self.config = config or TemporaryContextConfig()
        self.prefix = prefix + ':{temporary-contexts}'
        self.incarnation = 'redis'

    def keys(self, context_id, actor_id):
        owner = hashlib.sha256(actor_id.encode('utf-8')).hexdigest()
        return [
            self.prefix + ':ctx:' + context_id,
            self.prefix + ':quota',
            self.prefix + ':user:' + owner,
            self.prefix + ':catalog',
        ]

    async def _eval(self, script, keys, args):
        try:
            result = await self.client.eval(script, len(keys), *keys, *args)
            return [x.decode('utf-8') if isinstance(x, bytes) else x for x in result]
        except Exception:
            raise ContextUnavailable() from None

    @staticmethod
    def _check(result):
        if result[0] == 'OK':
            return
        if result[0] in ('CONFLICT', 'EXPIRED'):
            raise ContextConflict()
        if result[0] == 'GONE':
            raise ContextGone()
        if result[0] == 'CAPACITY':
            raise ContextCapacity(limit=result[1])
        raise ContextUnavailable()

    async def now(self):
        try:
            seconds, micros = await self.client.time()
            return int(seconds) + int(micros) / 1_000_000
        except Exception:
            raise ContextUnavailable() from None

    async def load(self, context_id, *, include_data=True):
        result = await self._eval(_REDIS_LOAD, [self.keys(context_id, '')[0]], [int(include_data)])
        self._check(result)
        try:
            return _assemble(result[1], result[2] if include_data else None, result[3]), (
                int(result[4]) + int(result[5]) / 1_000_000
            )
        except (TypeError, ValueError, KeyError):
            raise ContextUnavailable() from None

    async def commit(self, doc, *, expected_version, operation_id, include_data=True):
        control, data, events = _segments(doc, include_data)
        config = self.config
        args = [
            -1 if expected_version is None else expected_version,
            control,
            data or '',
            events,
            operation_id,
            int(include_data),
            config.control_reserve_bytes,
            config.context_bytes,
            config.user_bytes,
            config.store_bytes,
            config.max_contexts_per_user,
            _sweep_due(doc),
        ]
        try:
            result = await self._eval(_REDIS_COMMIT, self.keys(doc['id'], doc['actor_id']), args)
            self._check(result)
        except ContextUnavailable:
            # Timeout may follow a committed write. Reconcile only this operation;
            # absence or later operations never prove the caller effect failed.
            try:
                current, _ = await self.load(doc['id'], include_data=False)
            except ContextStoreError:
                raise ContextUnavailable() from None
            if current['last_operation'] != operation_id or current['version'] != doc['version']:
                raise ContextUnavailable() from None

    async def due(self, limit):
        return await self._eval(_REDIS_DUE, [self.prefix + ':catalog'], [limit])

    async def defer(self, context_id, seconds):
        self._check(await self._eval(_REDIS_DEFER, [self.prefix + ':catalog'], [context_id, seconds]))

    async def retire(self, doc):
        result = await self._eval(_REDIS_RETIRE, self.keys(doc['id'], doc['actor_id']), [doc['version']])
        self._check(result)


def append_event_to_document(doc, event, config=None, *, task_id=None, reply_id=None, model_id=None):
    """Append after canonical mutation; dropped deltas require reconciliation."""
    config = config or TemporaryContextConfig()
    for value in (task_id, reply_id, model_id):
        if value is not None:
            _identifier(value)
    doc['event_seq'] += 1
    envelope = {
        'temporary_context': {'id': doc['id'], 'epoch': doc['epoch'], 'revision': doc['revision']},
        'seq': doc['event_seq'],
        'message_id': reply_id,
        'model_id': model_id,
        'task_id': task_id,
        'data': copy.deepcopy(event),
    }
    doc['events'].append(envelope)
    while len(doc['events']) > config.max_events or _bytes(doc['events']) > config.event_bytes:
        dropped = doc['events'].pop(0)
        doc['events_dropped_through'] = dropped['seq']
    return envelope


class TemporaryContextRepository:
    def __init__(self, backend, config=None, *, id_factory=None):
        self.backend = backend
        self.config = config or backend.config
        if self.config != backend.config:
            raise ValueError('Repository and backend limits must match')
        self.id_factory = id_factory or (lambda: uuid.uuid4().hex)

    def _id(self):
        return _identifier(self.id_factory())

    @staticmethod
    def _authorize(doc, actor_id, epoch):
        if doc['actor_id'] != actor_id:
            raise ContextNotFound()
        if doc['epoch'] != epoch:
            raise ContextGone()

    async def create(self, actor_id):
        _identifier(actor_id, 'actor_id')
        now = await self.backend.now()
        doc = {
            'id': self._id(),
            'actor_id': actor_id,
            'epoch': self._id(),
            'incarnation': self.backend.incarnation,
            'revision': 0,
            'version': 0,
            'fence': 0,
            'status': 'open',
            'created_at': now,
            'idle_deadline': now + self.config.idle_seconds,
            'maximum_deadline': now + self.config.maximum_seconds,
            'disconnected_deadline': None,
            'socket': {'id': None, 'generation': 0, 'connected': False},
            'tasks': {},
            'executions': {},
            'events': [],
            'event_seq': 0,
            'events_dropped_through': 0,
            'data': {},
            'rebuild_allowed': False,
            'stop_requested_at': None,
            'next_sweep_at': now + self.config.lease_seconds,
            'last_operation': self._id(),
        }
        await self.backend.commit(doc, expected_version=None, operation_id=doc['last_operation'])
        return copy.deepcopy(doc)

    async def read(self, context_id, actor_id, epoch, *, allow_closed=False):
        try:
            doc, now = await self.backend.load(_identifier(context_id))
        except ContextGone:
            # Loss of raw data is not loss of the independent execution ledger.
            # Fence retained control, but never substitute empty raw history.
            control, _ = await self.backend.load(context_id, include_data=False)
            self._authorize(control, actor_id, epoch)
            await self.close(context_id, actor_id, epoch)
            raise ContextGone() from None
        self._authorize(doc, actor_id, epoch)
        if doc['status'] == 'open' and _deadline(doc) <= now:
            await self.close(context_id, actor_id, epoch)
            doc, _ = await self.backend.load(context_id)
        if not allow_closed and doc['status'] != 'open':
            raise ContextGone(rebuild_allowed=doc['rebuild_allowed'])
        return doc

    async def transact(
        self,
        context_id,
        actor_id,
        epoch,
        transition,
        *,
        expected_revision=None,
        expected_fence=None,
        settlement_task_id=None,
        source_changed=False,
        operation_id=None,
    ):
        return await self._transact(
            context_id,
            actor_id,
            epoch,
            transition,
            expected_revision=expected_revision,
            expected_fence=expected_fence,
            settlement_task_id=settlement_task_id,
            source_changed=source_changed,
            operation_id=operation_id,
        )

    async def _transact(
        self,
        context_id,
        actor_id,
        epoch,
        transition,
        *,
        expected_revision=None,
        expected_fence=None,
        settlement_task_id=None,
        source_changed=False,
        operation_id=None,
        lifecycle=False,
        include_data=True,
    ):
        _identifier(context_id)
        operation_id = _identifier(operation_id) if operation_id else self._id()
        for _ in range(self.config.cas_retries):
            doc, now = await self.backend.load(context_id, include_data=include_data)
            self._authorize(doc, actor_id, epoch)
            if doc['last_operation'] == operation_id:
                raise ContextConflict()  # Arbitrary transition results cannot be reconstructed.
            old = copy.deepcopy(doc)
            if doc['status'] == 'open' and _deadline(doc) <= now and not lifecycle:
                await self.close(context_id, actor_id, epoch)
                if settlement_task_id is not None:
                    continue
                raise ContextGone()
            if doc['status'] == 'open' and _deadline(doc) <= now and lifecycle:
                _fence(doc, now, self.config)
            if expected_revision is not None and doc['revision'] != expected_revision:
                raise ContextConflict()
            if settlement_task_id is not None:
                task = doc['tasks'].get(settlement_task_id)
                if task is None or task['status'] == 'done':
                    raise ContextConflict()
                if expected_fence is not None and task['fence'] != expected_fence:
                    raise ContextConflict()
            elif not lifecycle:
                if doc['status'] != 'open':
                    raise ContextGone(rebuild_allowed=doc['rebuild_allowed'])
                if expected_fence is not None and doc['fence'] != expected_fence:
                    raise ContextConflict()
            if source_changed:
                doc['revision'] += 1
            try:
                result = transition(doc, now)
            except ContextGone:
                if old['status'] == 'open' and _deadline(old) <= now:
                    # Admission/lease callbacks reject the expired proposal;
                    # commit the stop fence independently before reporting gone.
                    await self.close(context_id, actor_id, epoch)
                raise
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError('Storage transitions must be synchronous')
            if not lifecycle:
                mutable = {'data', 'events', 'event_seq', 'events_dropped_through', 'revision'}
                for key in set(doc) | set(old):
                    if key not in mutable and doc.get(key) != old.get(key):
                        raise ContextConflict()
                if doc['revision'] != old['revision'] + int(source_changed):
                    raise ContextConflict()
            doc['version'] = old['version'] + 1
            doc['last_operation'] = operation_id
            try:
                _validate_document(doc)
            except ContextUnavailable:
                raise ContextConflict() from None
            try:
                await self.backend.commit(
                    doc,
                    expected_version=old['version'],
                    operation_id=operation_id,
                    include_data=include_data or doc['data'] is not None,
                )
                return copy.deepcopy(doc), copy.deepcopy(result)
            except ContextConflict:
                continue
        raise ContextConflict()

    async def register_tasks(self, context_id, actor_id, epoch, tasks, *, expected_revision, exclusive=False):
        if not isinstance(tasks, list) or not tasks:
            raise ValueError('A nonempty task list is required')
        prepared = []
        for task in tasks:
            prepared.append(
                {
                    key: _identifier(task.get(key), key)
                    for key in ('task_id', 'reply_id', 'model_id', 'branch_id', 'worker_nonce')
                }
            )
        if (
            len({t['task_id'] for t in prepared}) != len(prepared)
            or len({t['reply_id'] for t in prepared}) != len(prepared)
            or (exclusive and len(prepared) != 1)
        ):
            raise ContextConflict()

        def transition(doc, now):
            self._require_open(doc, now)
            active = [task for task in doc['tasks'].values() if task['status'] != 'done']
            unsettled_tasks = {
                entry['task_id']
                for entry in doc['executions'].values()
                if entry['status'] != 'resolved' or entry.get('publication_pending')
            }
            if (
                any(task.get('exclusive', False) or task.get('stop_requested_at') is not None for task in active)
                or (exclusive and (active or unsettled_tasks))
                or any(
                    entry['status'] == 'uncertain' or entry.get('publication_pending')
                    for entry in doc['executions'].values()
                )
            ):
                raise ContextConflict()
            occupied = {
                task['reply_id']
                for task in doc['tasks'].values()
                if task['status'] != 'done' or task['task_id'] in unsettled_tasks
            }
            if any(values['reply_id'] in occupied for values in prepared):
                raise ContextConflict()
            retired = doc['data'].get('retired_tasks', {})
            if any(
                values['task_id'] in doc['tasks'] or _task_digest(values['task_id']) in retired for values in prepared
            ):
                raise ContextConflict()
            # Completion must come from the worker, and every execution must
            # already be settled and published. Keep active-task retry records.
            finished = {
                task_id
                for task_id, task in doc['tasks'].items()
                if task['status'] == 'done' and task_id not in unsettled_tasks
            }
            if finished:
                # This anti-reuse ledger belongs to byte-charged data, not the
                # bounded control reserve. Never accept it from client history.
                retired = doc['data'].setdefault('retired_tasks', {})
                retired.update({_task_digest(task_id): True for task_id in finished})
                for task_id in finished:
                    del doc['tasks'][task_id]
                doc['executions'] = {
                    key: entry for key, entry in doc['executions'].items() if entry['task_id'] not in finished
                }
            if len(doc['tasks']) + len(prepared) > self.config.max_tasks:
                raise ContextCapacity(limit='tasks')
            entries = []
            for values in prepared:
                entry = {
                    **values,
                    'fence': doc['fence'],
                    'status': 'registered',
                    'created_at': now,
                    'done_at': None,
                    'exclusive': bool(exclusive),
                }
                doc['tasks'][values['task_id']] = entry
                entries.append(entry)
            _control_admission(doc, self.config)
            return entries

        return await self._transact(
            context_id,
            actor_id,
            epoch,
            transition,
            expected_revision=expected_revision,
            lifecycle=True,
        )

    def _require_open(self, doc, now):
        if doc['status'] != 'open' or _deadline(doc) <= now:
            raise ContextGone(rebuild_allowed=doc['rebuild_allowed'])

    @staticmethod
    def _task(doc, task_id, *, allow_done=False):
        task = doc['tasks'].get(task_id)
        if task is None or (task['status'] == 'done' and not allow_done):
            raise ContextConflict()
        return task

    async def begin_execution(
        self, context_id, actor_id, epoch, *, task_id, identity, target_hash, expected_fence=None
    ):
        _identifier(task_id)
        _identifier(target_hash, 'target_hash')
        fields = ('round_id', 'call_id', 'reply_id', 'model_id')
        identity = {key: _identifier(identity.get(key), key) for key in fields} | {'ordinal': identity.get('ordinal')}
        if type(identity['ordinal']) is not int or not 0 <= identity['ordinal'] <= 1_000_000:
            raise ValueError('ordinal must be a bounded nonnegative integer')
        execution_id = hashlib.sha256(_encode([context_id, epoch, task_id, identity]).encode('utf-8')).hexdigest()

        def transition(doc, now):
            self._require_open(doc, now)
            task = self._task(doc, task_id)
            if (
                task.get('exclusive', False)
                or task['fence'] != doc['fence']
                or (expected_fence is not None and expected_fence != doc['fence'])
            ):
                raise ContextConflict()
            if any(identity[key] != task[key] for key in ('reply_id', 'model_id')):
                raise ContextConflict()
            existing = doc['executions'].get(execution_id)
            if existing:
                if existing['target_hash'] != target_hash:
                    raise ContextConflict()
                return existing, False
            if len(doc['executions']) >= self.config.max_executions:
                raise ContextCapacity(limit='executions')
            entry = {
                'id': execution_id,
                'task_id': task_id,
                'identity': identity,
                'target_hash': target_hash,
                'fence': task['fence'],
                'status': 'started',
                'started_at': now,
                'resolved_at': None,
                'reason_code': None,
            }
            doc['executions'][execution_id] = entry
            _control_admission(doc, self.config)
            return entry, True

        _, result = await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)
        return result

    async def resolve_execution(
        self, context_id, actor_id, epoch, *, task_id, execution_id, raw_transition, requires_publication=False
    ):
        def transition(doc, now):
            self._task(doc, task_id, allow_done=True)
            entry = doc['executions'].get(execution_id)
            if entry is None or entry['task_id'] != task_id:
                raise ContextConflict()
            if entry['status'] == 'resolved':
                return entry
            doc['revision'] += 1
            original_control = {
                k: copy.deepcopy(v)
                for k, v in doc.items()
                if k not in ('data', 'events', 'event_seq', 'events_dropped_through')
            }
            value = raw_transition(doc, now)
            if inspect.isawaitable(value):
                if inspect.iscoroutine(value):
                    value.close()
                raise TypeError('Storage transitions must be synchronous')
            current_control = {
                k: v for k, v in doc.items() if k not in ('data', 'events', 'event_seq', 'events_dropped_through')
            }
            if current_control != original_control:
                raise ContextConflict()
            entry['status'] = 'resolved'
            entry['resolved_at'] = now
            entry['publication_pending'] = bool(requires_publication)
            entry['reason_code'] = None
            _finish_close(doc)
            return entry

        try:
            return await self._transact(context_id, actor_id, epoch, transition, lifecycle=True)
        except (ContextCapacity, ContextUnavailable):
            try:
                await self.mark_uncertain(
                    context_id,
                    actor_id,
                    epoch,
                    task_id=task_id,
                    execution_id=execution_id,
                    reason_code='result_persistence_failed',
                )
            except ContextStoreError:
                pass
            raise

    async def publish_execution_results(
        self,
        context_id,
        actor_id,
        epoch,
        *,
        task_id,
        execution_ids,
        raw_transition,
        expected_fence,
    ):
        """Publish already resolved originals, including after executor completion.

        This is a settlement-only port. The callback cannot change control state,
        and the original task/fence owns every result being released.
        """

        def transition(doc, now):
            task = self._task(doc, task_id, allow_done=True)
            if task['fence'] != expected_fence:
                raise ContextConflict()
            entries = [doc['executions'].get(key) for key in execution_ids]
            if any(not entry or entry['status'] != 'resolved' or entry['task_id'] != task_id for entry in entries):
                raise ContextConflict()
            if task['status'] == 'done' and not any(entry.get('publication_pending') for entry in entries):
                raise ContextConflict()
            mutable = {'data', 'events', 'event_seq', 'events_dropped_through'}
            before = {k: copy.deepcopy(v) for k, v in doc.items() if k not in mutable}
            result = raw_transition(doc, now)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError('Storage transitions must be synchronous')
            if before != {k: v for k, v in doc.items() if k not in mutable}:
                raise ContextConflict()
            for entry in entries:
                entry['publication_pending'] = False
            _finish_close(doc)
            return result

        return await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, source_changed=True)

    async def mark_uncertain(
        self, context_id, actor_id, epoch, *, task_id, execution_id, reason_code='outcome_unknown'
    ):
        if reason_code not in (
            'outcome_unknown',
            'result_persistence_failed',
            'worker_lost',
            'cancelled',
            'disconnected',
            'task_completed_unsettled',
        ):
            raise ValueError('Unknown bounded uncertainty reason')

        def transition(doc, now):
            self._task(doc, task_id, allow_done=True)
            entry = doc['executions'].get(execution_id)
            if entry is None or entry['task_id'] != task_id:
                raise ContextConflict()
            if entry['status'] != 'resolved':
                entry['status'] = 'uncertain'
                entry['reason_code'] = reason_code
            return entry

        return await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)

    async def acknowledge_task_done(self, context_id, actor_id, epoch, *, task_id):
        def transition(doc, now):
            task = self._task(doc, task_id, allow_done=True)
            if task['status'] != 'done':
                task['status'] = 'done'
                task['done_at'] = now
                append_event_to_document(
                    doc,
                    {'type': 'temporary:task-done', 'data': {'task_id': task_id}},
                    self.config,
                    task_id=task_id,
                    reply_id=task['reply_id'],
                    model_id=task['model_id'],
                )
            for entry in doc['executions'].values():
                if entry['task_id'] == task_id and entry['status'] == 'started':
                    entry['status'] = 'uncertain'
                    entry['reason_code'] = 'task_completed_unsettled'
            _finish_close(doc)

        try:
            # Active control must still settle when the data segment was lost.
            doc, _ = await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)
        except ContextConflict:
            doc = await self.read(context_id, actor_id, epoch, allow_closed=True)
            if _task_digest(task_id) not in doc['data'].get('retired_tasks', {}):
                raise
            # A retired completion retry is read-only and cannot touch new work.
        return doc

    async def lease(self, context_id, actor_id, epoch, *, socket_id=None, connected=True, rebind=False):
        if socket_id is not None:
            _identifier(socket_id)

        def transition(doc, now):
            self._require_open(doc, now)
            if socket_id is not None and (rebind or doc['socket']['id'] != socket_id or not doc['socket']['connected']):
                doc['socket']['generation'] += 1
                doc['socket']['id'] = socket_id
            doc['socket']['connected'] = bool(connected)
            doc['idle_deadline'] = min(now + self.config.idle_seconds, doc['maximum_deadline'])
            if connected:
                doc['disconnected_deadline'] = None
            elif doc['disconnected_deadline'] is None:
                doc['disconnected_deadline'] = min(now + self.config.disconnected_seconds, doc['maximum_deadline'])

        doc, _ = await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)
        return doc

    async def request_stop(self, context_id, actor_id, epoch):
        """Persist stop intent and fence new effects without closing the page.

        Already running effects may only settle under their original task. The
        executor's acknowledgement, never a lease or Pub/Sub, releases admission.
        """

        def transition(doc, now):
            active = [task for task in doc['tasks'].values() if task['status'] != 'done']
            if any(task.get('stop_requested_at') is None for task in active):
                doc['fence'] += 1
                for task in active:
                    task.setdefault('stop_requested_at', now)

        doc, _ = await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)
        return doc

    async def close(self, context_id, actor_id, epoch):
        def transition(doc, now):
            _fence(doc, now, self.config)

        doc, _ = await self._transact(context_id, actor_id, epoch, transition, lifecycle=True, include_data=False)
        return doc

    async def append_event(self, context_id, actor_id, epoch, event, *, task_id=None, reply_id=None, model_id=None):
        def transition(doc, now):
            if task_id is not None:
                task = self._task(doc, task_id)
                if reply_id != task['reply_id'] or model_id != task['model_id']:
                    raise ContextConflict()
            return append_event_to_document(
                doc, event, self.config, task_id=task_id, reply_id=reply_id, model_id=model_id
            )

        return await self._transact(
            context_id, actor_id, epoch, transition, settlement_task_id=task_id, include_data=False
        )

    async def sweep(self, *, limit=100, socket_alive=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('sweep limit must be between 1 and 1000')
        outcomes = []
        for context_id in await self.backend.due(limit):
            try:
                doc, _ = await self.backend.load(context_id, include_data=False)
                if doc['status'] == 'tombstone':
                    await self.backend.retire(doc)
                    outcomes.append({'id': context_id, 'status': 'retired', 'tasks': []})
                    continue

                observed_socket = copy.deepcopy(doc['socket'])
                alive = None
                if socket_alive is not None and doc['status'] == 'open':
                    alive = await socket_alive(copy.deepcopy(doc))

                def transition(current, stamp):
                    if current['status'] == 'open':
                        current['next_sweep_at'] = stamp + self.config.lease_seconds
                        if alive is False and current['socket'] == observed_socket:
                            current['socket']['connected'] = False
                            if current['disconnected_deadline'] is None:
                                current['disconnected_deadline'] = min(
                                    stamp + self.config.disconnected_seconds, current['maximum_deadline']
                                )
                    if current['status'] == 'open' and _deadline(current) <= stamp:
                        _fence(current, stamp, self.config)
                    if current['status'] == 'closed_confirmed':
                        current['status'] = 'tombstone'
                        current['retire_at'] = stamp + self.config.tombstone_seconds
                        current['data'] = {}
                        current['events'] = []
                        current['tasks'] = {}
                        current['executions'] = {}
                        current['socket'] = {
                            'id': None,
                            'generation': current['socket']['generation'],
                            'connected': False,
                        }
                    elif current['status'] == 'closing':
                        current['next_sweep_at'] = stamp + self.config.lease_seconds

                updated, _ = await self._transact(
                    context_id, doc['actor_id'], doc['epoch'], transition, lifecycle=True, include_data=False
                )
                outcomes.append(
                    {
                        'id': context_id,
                        'epoch': doc['epoch'],
                        'actor_id': doc['actor_id'],
                        'status': updated['status'],
                        'publications': [
                            copy.deepcopy(t)
                            for t in updated['tasks'].values()
                            if t['status'] == 'done'
                            and any(
                                e['task_id'] == t['task_id'] and e.get('publication_pending')
                                for e in updated['executions'].values()
                            )
                        ],
                        'tasks': [
                            copy.deepcopy(t)
                            for t in updated['tasks'].values()
                            if t['status'] != 'done'
                            and (updated['status'] == 'closing' or t.get('stop_requested_at') is not None)
                        ],
                    }
                )
            except (ContextGone, ContextUnavailable):
                # Retain conservative quota charges and unknown completion. A
                # lost/corrupt leading entry must not starve healthy due work.
                await self.backend.defer(context_id, self.config.lease_seconds)
                continue
            except ContextConflict:
                continue
        return outcomes


async def create_temporary_context_repository(
    *, config=None, redis_url=None, redis_sentinels=None, redis_cluster=False, prefix='open-webui', workers=1
):
    """Pass explicit deployment settings; rediss:// retains existing TLS handling."""
    config = config or TemporaryContextConfig.from_env()
    if redis_url or redis_sentinels or redis_cluster:
        try:
            from open_webui.utils.redis import get_redis_connection

            client = get_redis_connection(
                redis_url,
                redis_sentinels=redis_sentinels,
                redis_cluster=redis_cluster,
                async_mode=True,
                decode_responses=True,
            )
            if client is None:
                raise ContextUnavailable()
        except Exception:
            raise ContextUnavailable() from None
        backend = RedisTemporaryBackend(client, config, prefix=prefix)
    else:
        backend = MemoryTemporaryBackend(config, workers=workers)
    return TemporaryContextRepository(backend)
