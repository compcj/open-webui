"""Exercise the socket delivery authorization boundary without importing the app."""

import logging
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

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
        'get_socket_session_user': AsyncMock(side_effect=lambda sid, **kwargs: sid_users[sid]),
        'Users': SimpleNamespace(
            get_user_by_id=AsyncMock(side_effect=lambda user_id: SimpleNamespace(id=user_id, role='user'))
        ),
        'Chats': SimpleNamespace(has_chat_access=AsyncMock(side_effect=lambda chat_id, user: access[user.id])),
        'sio': SimpleNamespace(leave_room=AsyncMock()),
        '_socket_emit': emit,
        '_socket_handle_emit': AsyncMock(),
        'is_saved_chat_id': lambda cid: isinstance(cid, str) and cid.startswith('chat-'),
    }
    load_functions(
        'socket/main.py',
        {
            'get_authorized_chat_session_ids',
            'emit_local_chat_event',
            'chat_delivery_id',
            'handle_chat_emit',
            'get_authorized_encryption_session_ids',
            'emit_with_chat_access',
        },
        ns,
    )
    return SimpleNamespace(ns=ns, access=access, emit=emit, sessions=sid_users)


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
    runtime.ns['Chats'].has_chat_access.side_effect = RuntimeError('storage unavailable')
    await runtime.ns['emit_local_chat_event']('events', {}, 'chat:chat-1')
    runtime.emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_delivery_rechecks_each_device_of_the_same_user(runtime):
    ns = runtime.ns
    runtime.sessions['phone'] = {'id': 'member'}
    ns['LOCAL_AUTHENTICATED_SIDS'].add('phone')
    ns['get_session_ids_from_room'] = lambda _: ['phone', 'member']
    await ns['emit_local_chat_event']('events', {}, 'user:member', chat_id='chat-1')
    assert [call.kwargs['to'] for call in runtime.emit.await_args_list] == ['phone', 'member']
    runtime.emit.reset_mock()
    runtime.sessions['phone'] = None
    await ns['emit_local_chat_event']('events', {}, 'user:member', chat_id='chat-1')
    assert [call.kwargs['to'] for call in runtime.emit.await_args_list] == ['member']


@pytest.fixture
def saved_emitter(runtime, monkeypatch):
    ns = runtime.ns
    monkeypatch.setitem(sys.modules, 'open_webui.utils.chat', SimpleNamespace(get_temporary_chat=AsyncMock()))
    ns.update(WEBSOCKET_MANAGER='', time=time, is_temporary_chat_id=lambda _: False)
    ns['get_session_ids_from_room'] = lambda room: (
        ['member', 'expired'] if room == 'user:member' else ['owner', 'member']
    )
    ns['sio'].emit = ns['emit_with_chat_access']
    load_functions('socket/main.py', {'get_event_emitter'}, ns)
    return ns['get_event_emitter']


@pytest.mark.asyncio
@pytest.mark.parametrize('failed_lookup', ['user', 'chat'])
async def test_author_lookup_failure_still_delivers_to_other_readers_and_persists_status(
    runtime, saved_emitter, failed_lookup
):
    ns = runtime.ns
    if failed_lookup == 'user':

        def user_by_id(user_id):
            if user_id == 'member':
                raise RuntimeError('user lookup unavailable')
            return SimpleNamespace(id=user_id, role='user')

        ns['Users'].get_user_by_id.side_effect = user_by_id
    else:

        def has_access(chat_id, user):
            if user.id == 'member':
                raise RuntimeError('chat lookup unavailable')
            return True

        ns['Chats'].has_chat_access.side_effect = has_access
    persist = ns['Chats'].add_message_status_to_chat_by_id_and_message_id = AsyncMock()
    emit = await saved_emitter({'user_id': 'member', 'chat_id': 'chat-1', 'message_id': 'reply'}, update_db=True)
    await emit({'type': 'status', 'data': {'done': True}})
    assert [call.kwargs['to'] for call in runtime.emit.await_args_list] == ['owner']
    persist.assert_awaited_once_with('chat-1', 'reply', {'done': True})


@pytest_asyncio.fixture
async def acl_database(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from open_webui.internal import db as database
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.chats import Chat
    from open_webui.models.folders import Folder
    from open_webui.models.groups import Group, GroupMember
    from open_webui.models.shared_chats import SharedChat

    engine = create_async_engine('sqlite+aiosqlite://')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, 'AsyncSessionLocal', sessions)
    try:
        async with engine.begin() as conn:
            for model in (Chat, SharedChat, AccessGrant, Group, GroupMember, Folder):
                await conn.run_sync(model.__table__.create)
        yield engine, sessions
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stream_acl_reads_only_metadata_and_the_latest_share_mode(
    runtime, saved_emitter, acl_database, monkeypatch
):
    from sqlalchemy import Select, event
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.shared_chats import SharedChat

    engine, sessions = acl_database
    clean = Mock(wraps=Chats._clean_null_bytes)
    monkeypatch.setattr(Chats, '_clean_null_bytes', clean)
    runtime.ns['Chats'] = Chats
    history = {'messages': [{'content': 'saved content'}]}
    share = {'chat_id': 'chat-1', 'user_id': 'owner', 'title': 'Chat'}
    body_reads = []

    @event.listens_for(engine.sync_engine, 'before_execute')
    def record_body_reads(conn, clauseelement, multiparams, params, execution_options):
        if isinstance(clauseelement, Select) and any(
            column.shares_lineage(body)
            for column in clauseelement.selected_columns
            for body in (Chat.chat, SharedChat.chat)
        ):
            body_reads.append(str(clauseelement))

    async with sessions.begin() as db:
        db.add(Chat(id='chat-1', user_id='owner', title='Chat', created_at=1, updated_at=1, chat=history))
        db.add(SharedChat(id='first', **share, chat={**history, 'share_mode': 'continue'}, created_at=5, updated_at=1))
        db.add(
            AccessGrant(
                id='read',
                resource_type='shared_chat',
                resource_id='chat-1',
                principal_type='user',
                principal_id='member',
                permission='read',
                created_at=1,
            )
        )
    emit = await saved_emitter(
        {'user_id': 'member', 'chat_id': 'chat-1', 'message_id': 'reply', 'session_id': 'member'}, update_db=True
    )
    delta = {'type': 'chat:completion', 'data': {'content': 'live'}}
    await emit(delta)
    assert [call.kwargs['to'] for call in runtime.emit.await_args_list] == ['member', 'owner']
    # The newer snapshot has no continue mode. The older snapshot still exists with a valid grant.
    async with sessions.begin() as db:
        db.add(SharedChat(id='latest', **share, chat=history, created_at=2, updated_at=2))
    runtime.emit.reset_mock()
    await emit(delta)
    assert [call.kwargs['to'] for call in runtime.emit.await_args_list] == ['owner']
    assert not body_reads, body_reads
    clean.assert_not_called()

    owner = SimpleNamespace(id='owner', role='user')
    assert (await Chats.get_accessible_chat_by_id('chat-1', owner)).chat == history
    assert body_reads
    clean.assert_called_once()


@pytest.mark.asyncio
async def test_stream_recipient_rechecks_folder_group_and_admin_access(runtime, acl_database, monkeypatch):
    from sqlalchemy import delete, update
    from open_webui.models import chats
    from open_webui.models.access_grants import AccessGrant
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.folders import Folder
    from open_webui.models.groups import Group, GroupMember

    _, sessions = acl_database
    common = {'user_id': 'owner', 'created_at': 1, 'updated_at': 1}
    async with sessions.begin() as db:
        db.add(Folder(id='parent', name='Parent', **common))
        db.add(Folder(id='child', name='Child', parent_id='parent', **common))
        db.add(Chat(id='chat-1', folder_id='child', title='Chat', chat={}, **common))
        db.add(Group(id='team', name='Team', description='', **common))
        db.add(GroupMember(id='membership', group_id='team', user_id='member'))
        db.add(
            AccessGrant(
                id='grant',
                resource_type='folder',
                resource_id='parent',
                principal_type='group',
                principal_id='team',
                permission='read',
                created_at=1,
            )
        )
    ns = runtime.ns
    ns['Chats'] = Chats
    ns['get_session_ids_from_room'] = lambda room: ['member']
    recipients = ns['get_authorized_chat_session_ids']
    assert await recipients('chat:chat-1') == ['member']
    async with sessions.begin() as db:
        await db.execute(delete(GroupMember).where(GroupMember.id == 'membership'))
    assert await recipients('chat:chat-1') == []

    admin = SimpleNamespace(id='member', role='admin')
    ns['Users'].get_user_by_id.side_effect = lambda _: admin
    monkeypatch.setattr(chats, 'ENABLE_ADMIN_CHAT_ACCESS', True)
    assert await recipients('chat:chat-1') == ['member']
    assert not await Chats.has_chat_access('chat-1', admin, permission='write')
    monkeypatch.setattr(chats, 'ENABLE_ADMIN_CHAT_ACCESS', False)
    assert await recipients('chat:chat-1') == []
    async with sessions.begin() as db:
        await db.execute(update(Chat).where(Chat.id == 'chat-1').values(meta={'internal': True}))
    assert await recipients('chat:chat-1') == ['member']


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
    sent.reset_mock()
    runtime.access['member'] = False
    await manager._handle_emit(message)
    sent.assert_not_awaited()
    published.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('unavailable', [False, True])
async def test_encrypted_delivery_rechecks_local_token_and_fails_closed(runtime, unavailable):
    ns = runtime.ns
    ns['encryption'] = SimpleNamespace(
        authorize=AsyncMock(
            side_effect=RuntimeError('revocation lookup unavailable') if unavailable else ValueError('revoked')
        ),
        eligible_users=AsyncMock(return_value={}),
    )
    ns['get_session_ids_from_room'] = lambda room: ['member']
    message = {
        'event': 'encryption:event',
        'data': [{'kind': 'packet', 'channel': 'private-channel', 'packet': 'ciphertext'}],
        'room': 'member',
        'namespace': '/',
        'host_id': 'other-worker',
    }
    await ns['handle_chat_emit'](message)
    ns['_socket_handle_emit'].assert_not_awaited()
    ns['encryption'].authorize.assert_awaited_once_with('member', 'private-channel')


@pytest.mark.asyncio
async def test_encrypted_delivery_keeps_remote_routing_and_delivers_only_eligible_local_sids(runtime):
    ns = runtime.ns
    ns['encryption'] = SimpleNamespace(
        authorize=AsyncMock(return_value=SimpleNamespace(id='member')),
        eligible_users=AsyncMock(return_value={'member': object()}),
    )
    ns['get_session_ids_from_room'] = lambda room: ['member', 'remote-sid']
    message = {
        'event': 'encryption:event',
        'data': [{'kind': 'packet', 'channel': 'private-channel'}],
        'room': 'member',
        'namespace': '/',
    }
    await ns['handle_chat_emit'](message)
    ns['encryption'].authorize.assert_awaited_once_with('member', 'private-channel')
    assert ns['_socket_handle_emit'].call_args.args[0] == {**message, 'room': 'member', 'skip_sid': None}
    ns['_socket_handle_emit'].reset_mock()
    ns['encryption'].eligible_users.return_value = {}
    await ns['handle_chat_emit'](message)
    ns['_socket_handle_emit'].assert_not_awaited()


@pytest.mark.asyncio
async def test_encryption_end_notice_requires_token_but_not_revoked_channel_access(runtime):
    ns = runtime.ns
    ns['encryption'] = SimpleNamespace(authorize=AsyncMock(), eligible_users=AsyncMock())
    ns['get_session_ids_from_room'] = lambda room: ['member', 'expired']
    message = {
        'event': 'encryption:event',
        'data': [{'kind': 'ended', 'channel': 'private-channel', 'session': 'epoch'}],
        'room': 'member',
        'namespace': '/',
    }
    await ns['handle_chat_emit'](message)
    assert ns['_socket_handle_emit'].await_count == 1
    assert ns['_socket_handle_emit'].call_args.args[0]['room'] == 'member'
    assert ns['get_socket_session_user'].await_count == 2
    assert all(call.kwargs == {'wait_for_disconnect': False} for call in ns['get_socket_session_user'].call_args_list)
    ns['encryption'].authorize.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('manager,ignore_queue', [('memory', False), ('redis', True)])
async def test_local_encrypted_delivery_also_checks_live_access(runtime, manager, ignore_queue):
    ns = runtime.ns
    ns['WEBSOCKET_MANAGER'] = manager
    ns['encryption'] = SimpleNamespace(authorize=AsyncMock(side_effect=ValueError('revoked')))
    ns['get_session_ids_from_room'] = lambda room: ['member']
    await ns['emit_with_chat_access'](
        'encryption:event', {'kind': 'packet', 'channel': 'private'}, to='member', ignore_queue=ignore_queue
    )
    runtime.emit.assert_not_awaited()
    ns['encryption'].authorize.assert_awaited_once_with('member', 'private')
