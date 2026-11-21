"""Stable private model identities and live Tether discovery across upgrades."""

import asyncio
import logging
import time
from functools import partial
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from test_auth_revocation import load_functions


def test_tether_model_identity_is_qualified_by_device_service_and_raw_model():
    ns = {'quote': quote}
    load_functions('routers/openai.py', {'tether_model_id'}, ns)
    model_id = ns['tether_model_id']
    assert model_id('device-a', 'openai-a', 'model/x:y') == 'tether-model:device-a:openai-a:model%2Fx%3Ay'
    assert (
        len(
            {
                model_id(device, service, 'same')
                for device, service in [('device-a', 'openai-a'), ('device-b', 'openai-a'), ('device-a', 'openai-b')]
            }
        )
        == 3
    )


@pytest.mark.asyncio
async def test_tether_models_hidden_after_disable_even_with_cached_catalog():
    cached = AsyncMock(return_value={'stale': {}})
    sources = AsyncMock(return_value=[])
    ns = {'get_tether_connections': sources, '_get_tether_models': cached}
    load_functions('routers/openai.py', {'get_tether_models'}, ns)
    assert await ns['get_tether_models'](SimpleNamespace(id='viewer')) == {}
    sources.assert_awaited_once_with('viewer', 'openai')
    cached.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_chat_requires_the_participants_own_terminal_grant(monkeypatch):
    # Exercise the production resolver with an owner-granted chat and template. The
    # participant can write the chat, but the owner's session cannot provide an ACL.
    from open_webui.models.chats import Chats
    from open_webui.models.users import UserModel, Users
    from open_webui.utils import terminals
    from open_webui.utils.agents import session as agent_session

    def actor(identity):
        return UserModel(
            id=identity,
            name=identity,
            email=identity + '@example.test',
            role='user',
            last_active_at=1,
            created_at=1,
            updated_at=1,
        )

    owner, participant = actor('owner'), actor('participant')
    selector = terminals.template_terminal_id('managed', 'sandbox')
    grants = {owner.id}

    async def require_grant(user, resource):
        if user.id not in grants or resource != selector:
            raise HTTPException(403, 'Terminal access denied')

    stored = {'id': 'managed', 'url': 'https://managed.example.test', 'server_type': 'orchestrator'}
    chat = SimpleNamespace(user_id=owner.id, chat={'terminal_id': selector}, meta={})
    catalog = AsyncMock(return_value={'id': 'sandbox', 'data': {'scope': 'chat'}})
    session = AsyncMock(return_value=None)
    monkeypatch.setattr(terminals, 'ENABLE_TOOL_SERVERS', True)
    monkeypatch.setattr(terminals, 'get_connection', AsyncMock(return_value=stored))
    monkeypatch.setattr(terminals, 'has_connection_write_access', AsyncMock(return_value=False))
    monkeypatch.setattr(terminals, 'require_terminal_access', require_grant)
    monkeypatch.setattr(terminals, 'server_request', catalog)
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=chat))
    monkeypatch.setattr(Users, 'get_user_by_id', AsyncMock(return_value=owner))
    monkeypatch.setattr(agent_session, 'get_session', session)
    from open_webui.utils import access_control

    monkeypatch.setattr(access_control, 'has_connection_access', AsyncMock(return_value=True))

    metadata = {'chat_id': 'chat-a'}
    with pytest.raises(HTTPException) as denied:
        await terminals.find_terminal_connection(participant, selector, metadata)
    assert denied.value.status_code == 403
    catalog.assert_not_awaited()
    session.assert_not_awaited()
    grants.add(participant.id)
    selected = await terminals.find_terminal_connection(participant, selector, metadata)
    assert selected['context_id'] == 'chat:chat-a'
    assert selected['terminal_context']['user_id'] == participant.id
    session.assert_awaited_once_with(chat, user_id=participant.id)
    grants.remove(participant.id)
    with pytest.raises(HTTPException) as revoked:
        await terminals.find_terminal_connection(participant, selector, metadata)
    assert revoked.value.status_code == 403


@pytest.mark.asyncio
async def test_tether_terminal_uploads_default_to_webui_and_follow_owner_per_service(monkeypatch):
    from open_webui.utils import tether

    presence = {
        'connection_id': 'device',
        'name': 'Laptop',
        'services': ['open_terminal', 'work'],
        'connections': [{'type': 'terminal', 'name': 'Workspace', 'service': 'work'}],
    }
    monkeypatch.setattr(tether, 'ENABLE_TOOL_SERVERS', True)
    monkeypatch.setattr(tether, 'ENABLE_WEBSOCKET_SUPPORT', True)
    monkeypatch.setattr(
        tether.Config, 'get', AsyncMock(side_effect=lambda key, default=None: True if key == 'tether.enable' else [])
    )
    monkeypatch.setattr(tether, 'live_presences', AsyncMock(return_value=[presence]))
    user = SimpleNamespace(id='actor', settings=SimpleNamespace(ui={}))
    rows = await tether.get_terminal_connections(user)
    assert {row['config']['chat_uploads'] for row in rows} == {'default'}
    user.settings.ui = {'tetherTerminalUploads': {'tether:device:work': 'filesystem'}}
    rows = await tether.get_terminal_connections(user)
    assert {row['id']: row['config']['chat_uploads'] for row in rows} == {
        'tether:device': 'default',
        'tether:device:work': 'filesystem',
    }
    assert all(row['owner_id'] == user.id and not row['config'].get('auto_discover_skills') for row in rows)


@pytest.mark.asyncio
async def test_same_named_tether_models_keep_original_provider_id_for_each_owned_service():
    calls = []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def json(self):
            return {'data': [{'id': 'same-model'}]}

    class Client(Response):
        def __init__(self, user_id, service, timeout):
            self.user_id, self.service = user_id, service

        def get(self, url):
            calls.append((self.user_id, self.service, url))
            return Response()

    ns = {'asyncio': asyncio, 'quote': quote, 'RelayClient': Client, 'time': time, 'log': logging.getLogger(__name__)}
    load_functions('routers/openai.py', {'_get_tether_models', 'tether_model_id'}, ns)
    sources = [
        ({'connection_id': device, 'name': device}, {'service': service, 'name': service})
        for device, service in [('desk', 'one'), ('desk', 'two'), ('laptop', 'one')]
    ]
    models = await ns['_get_tether_models'](SimpleNamespace(id='actor'), sources)
    assert set(models) == {
        'tether-model:desk:one:same-model',
        'tether-model:desk:two:same-model',
        'tether-model:laptop:one:same-model',
    }
    assert all(item['provider_model_id'] == 'same-model' and item['owner_id'] == 'actor' for item in models.values())
    assert len({item['name'] for item in models.values()}) == 3
    assert len(calls) == 3 and {actor for actor, _, _ in calls} == {'actor'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure,status', [('owner', 503), ('revoked', 403), ('model-disabled', 403), ('service-gone', 503)]
)
async def test_relay_open_denies_changed_owner_login_category_or_service_before_allocating_stream(
    monkeypatch, failure, status
):
    from open_webui.utils import tether

    hub = tether.Hub()
    device = 'desktop'
    presence = {
        'connection_id': device,
        'connection': 'live',
        'user': 'other' if failure == 'owner' else 'actor',
        'session': 'login',
        'services': [] if failure == 'service-gone' else ['model'],
        'connections': [{'type': 'openai', 'service': 'model'}],
    }
    monkeypatch.setattr(tether, 'ENABLE_WEBSOCKET_SUPPORT', True)
    monkeypatch.setattr(hub, 'presence', AsyncMock(return_value=presence))
    monkeypatch.setattr(
        tether.Config,
        'get',
        AsyncMock(side_effect=lambda key, default=None: key != 'openai.enable' or failure != 'model-disabled'),
    )
    monkeypatch.setattr(
        tether.AuthSessions,
        'get',
        AsyncMock(
            return_value=SimpleNamespace(
                user_id='actor',
                refresh_token_id=None if failure == 'revoked' else 'current',
                expires_at=time.time() + 3600,
            )
        ),
    )
    with pytest.raises(HTTPException) as denied:
        await hub.open('actor', device, 'model')
    assert denied.value.status_code == status
    assert not hub.streams


@pytest.mark.asyncio
async def test_ambiguous_tether_get_tool_is_not_repeated_but_catalog_read_can_reconnect(monkeypatch):
    import aiohttp
    from open_webui.utils import tether

    sent = []

    async def send(self, method, url, **kwargs):
        sent.append((method, url))
        if len(sent) == 1:
            interrupted = tether.RelayInterrupted('Answer lost', disconnected=True)
            interrupted.before_response = True
            interrupted.device, interrupted.connection = 'desktop', 'old-socket'
            raise interrupted
        return SimpleNamespace(status=200, headers={}, json=AsyncMock(return_value={'ok': True}), close=AsyncMock())

    wait = AsyncMock(return_value=True)
    monkeypatch.setattr(tether.RelayClient, '_request', send)
    monkeypatch.setattr(tether.hub, 'wait_for_presence', wait)
    ns = {
        'ENABLE_TOOL_SERVERS': True,
        'OPENAPI_HTTP_METHODS': {'get'},
        'aiohttp': aiohttp,
        'get_terminal_client': tether.get_terminal_client,
        'RelayInterrupted': tether.RelayInterrupted,
        'partial': partial,
        'AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER': 5,
        'AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL': True,
        'AIOHTTP_CLIENT_ALLOW_REDIRECTS': False,
        'log': logging.getLogger(__name__),
    }
    load_functions('utils/tools.py', {'execute_tool_server'}, ns)
    url = 'tether://desktop'
    result, _ = await ns['execute_tool_server'](
        url,
        {},
        {},
        'approved_tool',
        {},
        {
            'user_id': 'actor',
            'service': 'api',
            'openapi': {'paths': {'/action': {'get': {'operationId': 'approved_tool'}}}},
        },
    )
    assert sent == [('GET', url + '/action')]
    assert result['outcome'] == 'unknown' and result['retryable'] is False
    wait.assert_not_awaited()

    sent.clear()
    async with tether.get_terminal_client(url, 'actor', service='api') as client:
        async with client.get(url + '/openapi.json') as response:
            assert await response.json() == {'ok': True}
    assert sent == [('GET', url + '/openapi.json')] * 2
    wait.assert_awaited_once_with('actor', 'desktop', 'api', 'old-socket')
