"""A saved interaction may reconnect, but its authority must remain live."""

import asyncio
import copy
import logging
import sys
import json
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import socketio
from redis.exceptions import RedisError

from test_auth_revocation import load_functions
from open_webui.utils.chat_id import is_temporary_chat_id


@pytest.fixture
def runtime(monkeypatch):
    sessions = {'old': {'id': 'author'}, 'new': {'id': 'author'}}
    state = {'access': True, 'author': 'author', 'authenticated': True}
    chat = SimpleNamespace(user_id='owner')
    storage = ModuleType('open_webui.utils.chat')
    storage.get_temporary_chat = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, storage.__name__, storage)
    ns = {
        'asyncio': asyncio,
        'socketio': socketio,
        'RedisError': RedisError,
        'uuid4': uuid4,
        'log': logging.getLogger(__name__),
        'WEBSOCKET_EVENT_CALLER_TIMEOUT': 3,
        'LOCAL_AUTHENTICATED_SIDS': {'old', 'new'},
        'WEBSOCKET_MANAGER': '',
        'REDIS': None,
        'REDIS_EVENT_CHANNEL': 'test:interaction',
        'INTERACTION_AUTH_PENDING': {},
        'INTERACTION_AUTH_TIMEOUT': 0.2,
        'dumps_bytes': lambda value: json.dumps(value).encode(),
        'SESSION_POOL': sessions,
        'get_socket_session_user': AsyncMock(
            side_effect=lambda sid: sessions.get(sid) if state['authenticated'] else None
        ),
        'get_user_id_from_session_pool': lambda sid: (sessions.get(sid) or {}).get('id'),
        'get_user_chat_session_id': lambda *args: 'new',
        'is_saved_chat_id': lambda cid: cid == 'chat',
        'is_temporary_chat_id': is_temporary_chat_id,
        'get_redis_client': lambda **kwargs: None,
        'Users': SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id='author'))),
        'Chats': SimpleNamespace(
            get_accessible_chat_by_id=AsyncMock(side_effect=lambda *args, **kwargs: chat if state['access'] else None),
            get_message_by_id_and_message_id=AsyncMock(side_effect=lambda *args: {'user_id': state['author']}),
        ),
        'sio': SimpleNamespace(call=AsyncMock(return_value={'approved': True}), emit=AsyncMock()),
    }
    load_functions(
        'socket/main.py',
        {
            'get_event_call',
            'get_event_call_session_error',
            '_get_local_event_call_session_error',
            'handle_interaction_auth_event',
        },
        ns,
    )
    info = {'session_id': 'old', 'chat_id': 'chat', 'message_id': 'reply', 'user_id': 'author'}
    return SimpleNamespace(ns=ns, sessions=sessions, state=state, info=info, storage=storage)


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', ['access', 'authenticated', 'author'])
async def test_answer_rechecks_authority_after_client_has_seen_prompt(runtime, changed):
    async def answer(*args, **kwargs):
        runtime.state[changed] = 'another-user' if changed == 'author' else False
        return {'approved': True}

    runtime.ns['sio'].call.side_effect = answer
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await caller({'type': 'confirmation', 'data': {'tool_call': {'id': 'call'}}})
    assert 'error' in result
    assert 'approved' not in result


@pytest.mark.asyncio
async def test_non_author_cannot_receive_another_replys_interaction(runtime):
    runtime.state['author'] = 'owner'
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await caller({'type': 'input', 'data': {'prompt': 'Private question'}})
    assert 'error' in result
    runtime.ns['sio'].call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke', [False, True])
async def test_saved_reconnect_rechecks_access_and_reuses_interaction_identity(runtime, revoke):
    delivered = []

    async def call(event, data, *, to, **kwargs):
        delivered.append((to, copy.deepcopy(data)))
        if to == 'old':
            runtime.sessions.pop('old')
            runtime.state['access'] = not revoke
            await asyncio.Future()
        return {'value': 'answer'}

    runtime.ns['sio'].call.side_effect = call
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await caller({'type': 'input', 'data': {'prompt': 'Question'}})
    if revoke:
        assert 'error' in result
        assert [sid for sid, _ in delivered] == ['old']
    else:
        assert result == {'value': 'answer'}
        assert [sid for sid, _ in delivered] == ['old', 'new']
        assert len({data['data']['data']['interaction_id'] for _, data in delivered}) == 1


@pytest.mark.asyncio
async def test_temporary_interaction_disconnect_still_cancels(runtime):
    runtime.info['chat_id'] = 'temporary:old'

    async def call(*args, **kwargs):
        runtime.sessions.pop('old', None)
        runtime.storage.get_temporary_chat.return_value = {'user_id': 'author', 'session_id': 'new'}
        await asyncio.Future()

    runtime.ns['sio'].call.side_effect = call
    caller = await runtime.ns['get_event_call'](runtime.info)
    assert 'disconnected' in (await caller({'type': 'input'}))['error']
    assert runtime.ns['sio'].call.await_count == 1


@pytest.mark.asyncio
async def test_remote_session_requires_owner_worker_verification(runtime):
    runtime.ns['LOCAL_AUTHENTICATED_SIDS'] = set()
    runtime.ns['WEBSOCKET_MANAGER'] = 'redis'
    published = []

    async def publish(channel, raw):
        event = json.loads(raw)
        published.append(event)
        assert event['phase'] == 'request'
        # A different scope cannot fulfill this request even with the same nonce.
        wrong = {**event, 'phase': 'response', 'error': None, 'scope': {**event['scope'], 'message_id': 'other'}}
        await runtime.ns['handle_interaction_auth_event'](wrong)
        pending = runtime.ns['INTERACTION_AUTH_PENDING'][event['nonce']][1]
        assert not pending.done()
        await runtime.ns['handle_interaction_auth_event']({**event, 'phase': 'response', 'error': None})

    runtime.ns['REDIS'] = SimpleNamespace(publish=publish)
    caller = await runtime.ns['get_event_call'](runtime.info)
    assert await caller({'type': 'input'}) == {'approved': True}
    assert len(published) == 2  # Before delivery and after acknowledgment.
    runtime.ns['get_socket_session_user'].assert_not_awaited()
    assert not runtime.ns['INTERACTION_AUTH_PENDING']


@pytest.mark.asyncio
@pytest.mark.parametrize('offline', [False, True])
async def test_remote_verification_timeout_or_redis_failure_denies_without_fallback(runtime, offline):
    runtime.ns['LOCAL_AUTHENTICATED_SIDS'] = set()
    runtime.ns['WEBSOCKET_MANAGER'] = 'redis'
    publish = AsyncMock(side_effect=ConnectionError('synthetic unavailable') if offline else None)
    runtime.ns['REDIS'] = SimpleNamespace(publish=publish)
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await caller({'type': 'input'})
    assert 'retry' in result['error'].lower()
    runtime.ns['sio'].call.assert_not_awaited()
    runtime.ns['get_socket_session_user'].assert_not_awaited()
    assert not runtime.ns['INTERACTION_AUTH_PENDING']


@pytest.mark.asyncio
async def test_owner_worker_uses_live_local_checks_for_remote_probe(runtime):
    runtime.ns['REDIS'] = SimpleNamespace(publish=AsyncMock())
    runtime.state['author'] = 'someone-else'
    event = {'kind': 'interaction_auth_v1', 'phase': 'request', 'nonce': 'probe', 'scope': runtime.info}
    assert await runtime.ns['handle_interaction_auth_event'](event)
    reply = json.loads(runtime.ns['REDIS'].publish.call_args.args[1])
    assert reply['scope'] == runtime.info
    assert reply['nonce'] == 'probe'
    assert 'author' in reply['error']
    assert reply['phase'] == 'response'
    runtime.ns['REDIS'].publish.reset_mock()
    runtime.ns['LOCAL_AUTHENTICATED_SIDS'].clear()
    assert await runtime.ns['handle_interaction_auth_event'](event)
    runtime.ns['REDIS'].publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_relay_data_cannot_become_internal_auth_response(runtime):
    event = {'channel': 'author:chat:stream', 'data': {'kind': 'interaction_auth_v1', 'phase': 'response'}}
    assert await runtime.ns['handle_interaction_auth_event'](event) is False


@pytest.mark.asyncio
async def test_owner_probe_timeout_does_not_stall_the_redis_listener(runtime):
    async def stalled(*args):
        await asyncio.Future()

    runtime.ns['_get_local_event_call_session_error'] = stalled
    runtime.ns['REDIS'] = SimpleNamespace(publish=AsyncMock())
    event = {'kind': 'interaction_auth_v1', 'phase': 'request', 'nonce': 'probe', 'scope': runtime.info}
    assert await asyncio.wait_for(runtime.ns['handle_interaction_auth_event'](event), timeout=0.5)
    response = json.loads(runtime.ns['REDIS'].publish.call_args.args[1])
    assert 'retry' in response['error'].lower()
    assert await runtime.ns['handle_interaction_auth_event']({'channel': 'ordinary', 'data': {}}) is False


@pytest.mark.asyncio
async def test_owner_probe_publish_is_bounded(runtime):
    async def stalled(*args):
        await asyncio.Future()

    runtime.ns['REDIS'] = SimpleNamespace(publish=stalled)
    event = {'kind': 'interaction_auth_v1', 'phase': 'request', 'nonce': 'probe', 'scope': runtime.info}
    task = asyncio.create_task(runtime.ns['handle_interaction_auth_event'](event))
    try:
        done, _ = await asyncio.wait({task}, timeout=0.5)
        assert task in done
        with pytest.raises(TimeoutError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('during_reconnect', [False, True])
async def test_routing_store_outage_after_prompt_returns_retryable_error(runtime, during_reconnect):
    shown = asyncio.Event()
    cleaned = asyncio.Event()

    async def answer(*args, **kwargs):
        shown.set()
        if during_reconnect:
            runtime.sessions.pop('old')
        try:
            await asyncio.Future()
        finally:
            cleaned.set()

    def lookup(sid):
        if shown.is_set() and not during_reconnect:
            raise RedisError('synthetic routing store outage')
        return (runtime.sessions.get(sid) or {}).get('id')

    def reconnect(*args):
        raise RedisError('synthetic routing store outage')

    runtime.ns['get_user_id_from_session_pool'] = lookup
    runtime.ns['get_user_chat_session_id'] = reconnect
    runtime.ns['sio'].call.side_effect = answer
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await caller({'type': 'input'})
    assert 'retry' in result['error'].lower()
    assert shown.is_set() and cleaned.is_set()
    runtime.ns['sio'].call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('stalled', [False, True])
async def test_completion_notice_failure_does_not_replace_validated_answer(runtime, stalled):
    async def emit(*args, **kwargs):
        if stalled:
            await asyncio.Future()
        raise ConnectionError('synthetic notice delivery outage')

    runtime.ns['sio'].emit.side_effect = emit
    caller = await runtime.ns['get_event_call'](runtime.info)
    result = await asyncio.wait_for(caller({'type': 'input'}), timeout=0.5)
    assert result == {'approved': True}
    runtime.ns['sio'].call.assert_awaited_once()
