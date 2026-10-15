"""App channel actions obey the existing human channel authorization boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from test_web_tool_citations import load_functions


class Reply(SimpleNamespace):
    @classmethod
    def model_validate(cls, value):
        return cls(**value)

    def model_dump(self):
        return vars(self).copy()


class Message(SimpleNamespace):
    model_fields = {'content': None, 'actions': None}


@pytest.fixture
def app_runtime():
    user = SimpleNamespace(id='actor', role='user')
    channel = SimpleNamespace(id='channel', type='channel', deleted_at=None, archived_at=None)
    app = SimpleNamespace(id='app', api_key='synthetic', data={'manifest': {'public_key': 'synthetic'}})
    flags = {'apps.enable': True, 'channels.enable': True, 'user.permissions': {}}
    permission = {'feature': True, 'write': False, 'member': True}

    async def resource_access(user_id, resource_type, resource_id, access, **kwargs):
        return access == 'read' or permission['write']

    ns = {
        'HTTPException': HTTPException,
        'Config': SimpleNamespace(get=AsyncMock(side_effect=lambda key: flags.get(key))),
        'Channels': SimpleNamespace(
            get_channel_by_id=AsyncMock(return_value=channel),
            is_user_channel_member=AsyncMock(side_effect=lambda *a, **k: permission['member']),
            is_user_channel_manager=AsyncMock(return_value=False),
        ),
        'AccessGrants': SimpleNamespace(has_access=AsyncMock(side_effect=resource_access)),
        'has_permission': AsyncMock(side_effect=lambda *args: permission['feature']),
        'get_app_by_id': AsyncMock(return_value=app),
        'has_app_access': AsyncMock(return_value=True),
        'check_app_channel_membership': AsyncMock(return_value=channel),
        'Users': SimpleNamespace(get_user_by_id=AsyncMock(return_value=user)),
        'AppChannelReply': Reply,
        'AppChannelMessage': Message,
        'send_app_invocation': AsyncMock(),
        'create_app_message': AsyncMock(return_value={'id': 'public-new'}),
        'update_app_message': AsyncMock(return_value={'id': 'public-existing'}),
    }
    load_functions(
        ns, 'utils/apps.py', {'check_app_channel_access', 'check_app_invocation_access', 'generate_app_channel_reply'}
    )
    return SimpleNamespace(ns=ns, user=user, channel=channel, app=app, flags=flags, permission=permission)


@pytest.mark.asyncio
async def test_app_endpoints_cannot_bypass_disabled_user_channel_feature(app_runtime):
    r = app_runtime
    r.permission['feature'] = False
    with pytest.raises(HTTPException) as error:
        await r.ns['check_app_channel_access']('channel', db=None, user=r.user)
    assert error.value.status_code in (401, 403)


@pytest.mark.asyncio
@pytest.mark.parametrize('manager', [False, True])
async def test_group_nonmember_admin_cannot_view_or_manage_apps(app_runtime, manager):
    r = app_runtime
    r.user.role = 'admin'
    r.channel.type = 'group'
    r.permission['member'] = False
    with pytest.raises(HTTPException) as error:
        await r.ns['check_app_channel_access']('channel', db=None, user=r.user, require_manager=manager)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_member_admin_keeps_channel_feature_and_management_policy(app_runtime):
    r = app_runtime
    r.user.role = 'admin'
    r.channel.type = 'group'
    r.permission['feature'] = False
    assert await r.ns['check_app_channel_access']('channel', db=None, user=r.user, require_manager=True) is r.channel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'reply_type,visibility', [('message', 'channel'), ('update', 'channel'), ('update', 'private')]
)
async def test_readonly_actor_cannot_persist_public_reply_or_update(app_runtime, reply_type, visibility):
    r = app_runtime
    r.ns['send_app_invocation'].return_value = {
        'type': reply_type,
        'visibility': visibility,
        'actions': [],
        'content': 'reply',
    }
    with pytest.raises(HTTPException) as error:
        await r.ns['generate_app_channel_reply'](
            r.app, r.user, 'channel', 'action', {}, db=None, source={'id': 'source', 'updated_at': 1}
        )
    assert error.value.status_code == 403
    r.ns['create_app_message'].assert_not_awaited()
    r.ns['update_app_message'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('reply_type', ['message', 'download', 'ack', 'error'])
async def test_readonly_actor_can_keep_private_interactions(app_runtime, reply_type):
    r = app_runtime
    r.ns['send_app_invocation'].return_value = {
        'type': reply_type,
        'visibility': 'private',
        'actions': [],
        'content': '',
    }
    result = await r.ns['generate_app_channel_reply'](r.app, r.user, 'channel', 'action', {}, db=None)
    assert result['type'] == reply_type
    r.ns['create_app_message'].assert_not_awaited()
    r.ns['update_app_message'].assert_not_awaited()


@pytest.mark.asyncio
async def test_write_revocation_during_app_request_denies_public_persistence(app_runtime):
    r = app_runtime
    r.permission['write'] = True

    async def send(*args, **kwargs):
        r.permission['write'] = False
        return {'type': 'message', 'visibility': 'channel', 'actions': [], 'content': 'reply'}

    r.ns['send_app_invocation'].side_effect = send
    with pytest.raises(HTTPException):
        await r.ns['generate_app_channel_reply'](r.app, r.user, 'channel', 'action', {}, db=None)
    r.ns['create_app_message'].assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_writer_can_publish_app_reply(app_runtime):
    r = app_runtime
    r.permission['write'] = True
    r.ns['send_app_invocation'].return_value = {
        'type': 'message',
        'visibility': 'channel',
        'actions': [],
        'content': 'reply',
    }
    result = await r.ns['generate_app_channel_reply'](r.app, r.user, 'channel', 'action', {}, db=None)
    assert result['message'] == {'id': 'public-new'}
    r.ns['create_app_message'].assert_awaited_once()
