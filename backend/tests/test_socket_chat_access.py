"""Exercise the socket delivery authorization boundary without importing the app."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_auth_revocation import load_functions


@pytest.fixture
def runtime():
    sid_users = {'owner': {'id': 'owner'}, 'member': {'id': 'member'}, 'expired': None}
    access = {'owner': True, 'member': True}
    emit = AsyncMock()
    ns = {
        'log': logging.getLogger(__name__),
        'LOCAL_AUTHENTICATED_SIDS': set(sid_users),
        'SESSION_POOL': {
            sid: {**(user or {'id': 'expired'}), 'chat_ids': ['chat-1']} for sid, user in sid_users.items()
        },
        'get_session_ids_from_room': lambda room: list(sid_users),
        'get_socket_session_user': AsyncMock(side_effect=lambda sid: sid_users[sid]),
        'Users': SimpleNamespace(get_user_by_id=AsyncMock(side_effect=lambda user_id: SimpleNamespace(id=user_id))),
        'Chats': SimpleNamespace(
            get_accessible_chat_by_id=AsyncMock(side_effect=lambda chat_id, user: access[user.id])
        ),
        'sio': SimpleNamespace(leave_room=AsyncMock()),
        '_socket_emit': emit,
        '_socket_handle_emit': AsyncMock(),
        'is_saved_chat_id': lambda cid: isinstance(cid, str) and cid.startswith('chat-'),
    }
    load_functions(
        'socket/main.py',
        {'get_authorized_chat_session_ids', 'emit_local_chat_event', 'chat_delivery_id', 'handle_chat_emit'},
        ns,
    )
    return SimpleNamespace(ns=ns, access=access, emit=emit)


@pytest.mark.asyncio
async def test_live_delivery_rechecks_revoked_membership(runtime):
    await runtime.ns['emit_local_chat_event']('events', {'content': 'first'}, 'chat:chat-1')
    assert {call.kwargs['to'] for call in runtime.emit.call_args_list} == {'owner', 'member'}
    runtime.emit.reset_mock()
    runtime.access['member'] = False
    await runtime.ns['emit_local_chat_event']('events', {'content': 'secret'}, 'chat:chat-1')
    assert [call.kwargs['to'] for call in runtime.emit.call_args_list] == ['owner']
    runtime.ns['sio'].leave_room.assert_awaited_once_with('member', 'chat:chat-1')
    assert runtime.ns['SESSION_POOL']['member']['chat_ids'] == []


@pytest.mark.asyncio
async def test_skip_sid_and_stale_token_never_receive(runtime):
    await runtime.ns['emit_local_chat_event']('events', {}, 'chat:chat-1', skip_sid='owner')
    assert [call.kwargs['to'] for call in runtime.emit.call_args_list] == ['member']


@pytest.mark.asyncio
async def test_permission_lookup_failure_fails_closed(runtime):
    runtime.ns['Chats'].get_accessible_chat_by_id.side_effect = RuntimeError('storage unavailable')
    await runtime.ns['emit_local_chat_event']('events', {}, 'chat:chat-1')
    runtime.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_manager_delivery_uses_current_worker_recipients_once(runtime):
    message = {
        'event': 'events',
        'data': [{'chat_id': 'chat-1', 'content': 'secret'}],
        'room': 'chat:chat-1',
        'namespace': '/',
        'host_id': 'remote-worker',
    }
    runtime.access['member'] = False
    await runtime.ns['handle_chat_emit'](message)
    handled = runtime.ns['_socket_handle_emit'].call_args_list
    assert len(handled) == 1
    assert handled[0].args[0] == {**message, 'room': 'owner', 'skip_sid': None}
    assert message['room'] == 'chat:chat-1'


@pytest.mark.asyncio
async def test_user_room_chat_payload_also_rechecks_token_and_acl(runtime):
    runtime.access['member'] = False
    message = {
        'event': 'events',
        'data': [{'chat_id': 'chat-1', 'data': {'type': 'chat:completion'}}],
        'room': 'user:member',
        'namespace': '/',
    }
    runtime.ns['get_session_ids_from_room'] = lambda _: ['member', 'expired']
    await runtime.ns['handle_chat_emit'](message)
    runtime.ns['_socket_handle_emit'].assert_not_awaited()


def test_access_notification_has_no_content_and_still_reaches_revoked_user(runtime):
    assert (
        runtime.ns['chat_delivery_id']('events', {'chat_id': 'chat-1', 'data': {'type': 'chat:access'}}, 'user:member')
        is None
    )


@pytest.mark.asyncio
async def test_installed_redis_manager_keeps_native_packet_transport_and_filters_revoked(runtime):
    import socketio

    manager = socketio.AsyncRedisManager('redis://unused-test-double')
    sent = AsyncMock()
    manager.set_server(SimpleNamespace(packet_class=socketio.packet.Packet, _send_eio_packet=sent))
    for sid in ['owner', 'member', 'expired']:
        manager.basic_enter_room(sid, '/', None, eio_sid=f'eio-{sid}')
        manager.basic_enter_room(sid, '/', sid)
        manager.basic_enter_room(sid, '/', 'chat:chat-1')
    published = AsyncMock()
    manager._publish = published
    runtime.ns['_socket_handle_emit'] = manager._handle_emit
    manager._handle_emit = runtime.ns['handle_chat_emit']
    runtime.ns['get_session_ids_from_room'] = lambda room: list(manager.rooms['/'].get(room, {}))
    runtime.ns['sio'].leave_room = lambda sid, room: manager.leave_room(sid, '/', room)
    runtime.access['member'] = False
    await manager.emit(
        'events',
        {'chat_id': 'chat-1', 'data': {'type': 'chat:completion', 'content': 'secret'}},
        namespace='/',
        room='chat:chat-1',
    )
    assert [call.args[0] for call in sent.call_args_list] == ['eio-owner']
    published.assert_awaited_once()
    message = published.call_args.args[0]
    assert message['room'] == 'chat:chat-1'
    assert message['host_id'] == manager.host_id
    assert message['data'][0]['data']['content'] == 'secret'
    assert 'member' not in manager.rooms['/']['chat:chat-1']
    assert 'secret' in sent.call_args.args[1].data


@pytest.mark.asyncio
async def test_installed_manager_remote_delivery_does_not_publish_again(runtime):
    import socketio

    manager = socketio.AsyncRedisManager('redis://unused-test-double')
    sent = AsyncMock()
    manager.set_server(SimpleNamespace(packet_class=socketio.packet.Packet, _send_eio_packet=sent))
    manager.basic_enter_room('member', '/', None, eio_sid='remote-member')
    manager.basic_enter_room('member', '/', 'member')
    manager.basic_enter_room('member', '/', 'chat:chat-1')
    published = AsyncMock()
    manager._publish = published
    runtime.ns['_socket_handle_emit'] = manager._handle_emit
    manager._handle_emit = runtime.ns['handle_chat_emit']
    runtime.ns['get_session_ids_from_room'] = lambda room: list(manager.rooms['/'].get(room, {}))
    message = {
        'method': 'emit',
        'event': 'events',
        'data': [{'chat_id': 'chat-1', 'content': 'remote'}],
        'namespace': '/',
        'room': 'chat:chat-1',
        'skip_sid': 'owner',
        'host_id': 'origin-worker',
        'callback': None,
    }
    await manager._handle_emit(message)
    assert [call.args[0] for call in sent.call_args_list] == ['remote-member']
    published.assert_not_awaited()
