"""Native approval and input must use live terminal grants, while cleanup stays possible."""

import asyncio
import copy
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.models.groups import Groups
from open_webui.models.users import UserModel
from open_webui.utils import terminals
from open_webui.utils.agents import output as agent_output
from open_webui.utils.agents import transport as agent_transport
from open_webui.utils.agents.codex import CodexAppServer
from open_webui.utils.agents.output import Output
from open_webui.utils.agents.transport import TerminalTransport
from open_webui.utils.headers import bearer_auth_header
from open_webui.utils.tool_approval import ResolveToolCallForm, resolve_tool_call_output


@pytest_asyncio.fixture
async def agent_access(monkeypatch):
    actor = UserModel(
        id='actor',
        name='Actor',
        email='actor@example.test',
        role='user',
        last_active_at=1,
        created_at=1,
        updated_at=1,
    )
    connection = {
        'id': 'terminal',
        'url': 'http://synthetic-terminal',
        'key': 'synthetic-server-key',
        'auth_type': 'bearer',
        'server_type': 'orchestrator',
        'enabled': True,
        'config': {
            'access_grants': [{'principal_type': 'user', 'principal_id': actor.id, 'permission': 'read'}],
            'contexts': {'chat': {'context_id': 'chat_id'}},
        },
    }
    state = SimpleNamespace(
        config_error=None,
        sent=[],
        calls=[],
        tasks=[],
        retained=[],
        pending=asyncio.Event(),
        transaction_open=False,
        authorization_hook=lambda: None,
    )

    async def config_get(key, default=None):
        if key == 'terminal_server.connections':
            if state.config_error:
                raise state.config_error
            return [copy.deepcopy(connection)]
        return False if key == 'tether.enable' else default

    monkeypatch.setattr(Config, 'get', config_get)
    monkeypatch.setattr(Groups, 'get_groups_by_member_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(terminals, 'ENABLE_TOOL_SERVERS', True)
    # Header interpolation is unrelated to this boundary. Avoid importing its
    # retrieval/provider dependencies while retaining the real resolver and ACL.
    headers_module = ModuleType('open_webui.utils.tools')

    async def build_headers(connection, *args, **kwargs):
        state.authorization_hook()
        return bearer_auth_header(connection.get('key', '')), {}

    headers_module.build_tool_server_headers = build_headers
    monkeypatch.setitem(sys.modules, headers_module.__name__, headers_module)
    metadata = {'terminal_id': 'terminal', 'chat_id': 'chat', 'message_id': 'reply', 'task_id': 'task'}
    message = {
        'user_id': actor.id,
        'done': False,
        'output': [],
        'meta': {
            'agent_execution': {
                **metadata,
                'user_id': actor.id,
                'provider': 'codex',
                'model': 'model',
                'cwd': '/workspace',
                'terminal_identity': hashlib.sha256(connection['url'].encode()).hexdigest(),
                'terminal_context': 'chat:chat',
                'session_id': 'remote-session',
                'state': 'running',
            }
        },
    }

    @asynccontextmanager
    async def edit_message(chat_id, message_id):
        assert (chat_id, message_id) == ('chat', 'reply')
        draft = copy.deepcopy(message)
        state.transaction_open = True
        try:
            yield draft
        finally:
            state.transaction_open = False
        message.clear()
        message.update(draft)
        if any(item.get('status') == 'pending' for item in message['output']):
            state.pending.set()

    monkeypatch.setattr(Chats, 'edit_message_output', edit_message)
    monkeypatch.setattr(Chats, 'get_accessible_chat_by_id', AsyncMock(return_value=SimpleNamespace(user_id=actor.id)))
    monkeypatch.setattr(
        Chats, 'get_agent_approval_output', AsyncMock(side_effect=lambda *_: copy.deepcopy(message['output']))
    )
    monkeypatch.setattr(
        Chats,
        'get_agent_execution',
        AsyncMock(side_effect=lambda *_: copy.deepcopy(message['meta'].get('agent_execution'))),
    )
    monkeypatch.setattr(agent_output, 'get_event_emitter', AsyncMock(return_value=AsyncMock()))
    monkeypatch.setattr(agent_output, 'requires_tool_approval', AsyncMock(return_value=True))

    app = FastAPI()
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [], 'app': app})
    transport = await TerminalTransport.resolve(request, actor, metadata)
    transport.session_id = 'remote-session'

    async def send(raw):
        frame = json.loads(raw)
        state.sent.append(frame)
        if frame['type'] == 'input':
            transport.pending[frame['sequence']][1].set_result(None)

    transport.socket = SimpleNamespace(send_str=send)
    transport.ready.set()
    output = Output(metadata, actor, transport.raise_if_failed, {})
    output.state = {
        'provider': 'codex',
        'model': 'model',
        'terminal_id': 'terminal',
        'cwd': '/workspace',
        'terminal_identity': message['meta']['agent_execution']['terminal_identity'],
        'terminal_context': 'chat:chat',
    }

    @app.post('/approve')
    async def approve(form: ResolveToolCallForm, request: Request):
        result = await resolve_tool_call_output('chat', 'reply', form, actor, request=request)
        return {'resume': result['resume']}

    async def pending_approval(*, automatic=False):
        agent_output.requires_tool_approval.return_value = not automatic
        task = asyncio.create_task(output.approve('agent_tool', {'command': 'synthetic-command'}, {'id': 7}))
        state.tasks.append(task)
        if automatic:
            await asyncio.wait_for(asyncio.shield(task), 2)
        else:
            await asyncio.wait_for(state.pending.wait(), 2)
        return task

    async def approve_http(*, raise_app_exceptions=True):
        call_id = next(item['call_id'] for item in message['output'] if item['type'] == 'function_call')
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, raise_app_exceptions=raise_app_exceptions), base_url='http://test'
        ) as client:
            return await client.post('/approve', json={'call_id': call_id, 'action': 'approve'})

    async def accept():
        await CodexAppServer(transport).send({'id': 7, 'result': {'decision': 'accept'}})

    def revoke(kind='grant'):
        if kind == 'grant':
            connection['config']['access_grants'] = []
        elif kind == 'disabled':
            connection['enabled'] = False
        elif kind == 'context':
            connection['config']['contexts']['chat'] = False
        elif kind == 'global':
            monkeypatch.setattr(terminals, 'ENABLE_TOOL_SERVERS', False)
        elif kind == 'url':
            connection['url'] = 'http://another-terminal'
        elif kind == 'context_identity':
            connection['config']['contexts']['chat'] = {'context_id': 'default'}

    state.actor, state.connection, state.metadata = actor, connection, metadata
    state.message, state.transport, state.output = message, transport, output
    state.pending_approval, state.approve_http, state.accept, state.revoke = (
        pending_approval,
        approve_http,
        accept,
        revoke,
    )
    try:
        yield state
    finally:
        for task in state.tasks:
            task.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['grant', 'disabled', 'context', 'global'])
async def test_pending_native_approval_rechecks_terminal_policy(agent_access, change):
    await agent_access.pending_approval()
    agent_access.revoke(change)
    response = await agent_access.approve_http()
    assert response.status_code == 403
    assert agent_access.message['output'][0]['status'] == 'pending'
    assert 'agent_resolution' not in agent_access.message['output'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['url', 'context_identity'])
async def test_pending_approval_cannot_move_to_another_terminal_context(agent_access, change):
    await agent_access.pending_approval()
    agent_access.revoke(change)
    response = await agent_access.approve_http()
    assert response.status_code == 409
    assert 'agent_resolution' not in agent_access.message['output'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['http', 'redis'])
async def test_approval_authorization_outage_is_retryable_without_consuming_card(agent_access, failure):
    task = await agent_access.pending_approval()
    agent_access.config_error = (
        HTTPException(503, 'Authorization unavailable', headers={'Retry-After': '2'})
        if failure == 'http'
        else RedisConnectionError('synthetic unavailable')
    )
    response = await agent_access.approve_http()
    assert response.status_code == 503 and response.headers['retry-after'] == '2'
    assert 'agent_resolution' not in agent_access.message['output'][0]
    agent_access.config_error = None
    assert (await agent_access.approve_http()).status_code == 200
    assert (await asyncio.wait_for(task, 2))['action'] == 'approve'


@pytest.mark.asyncio
async def test_approval_execution_lookup_outage_is_retryable_without_consuming_card(agent_access):
    task = await agent_access.pending_approval()
    lookup = Chats.get_agent_execution
    read_execution = lookup.side_effect
    lookup.side_effect = OperationalError('synthetic execution lookup', {}, ConnectionError('synthetic unavailable'))

    response = await agent_access.approve_http(raise_app_exceptions=False)

    assert response.status_code == 503 and response.headers['retry-after'] == '2'
    assert agent_access.message['output'][0]['status'] == 'pending'
    assert 'agent_resolution' not in agent_access.message['output'][0]
    assert agent_access.sent == []
    lookup.side_effect = read_execution
    assert (await agent_access.approve_http()).status_code == 200
    assert (await asyncio.wait_for(task, 2))['action'] == 'approve'


@pytest.mark.asyncio
async def test_allowed_native_approval_reaches_the_original_terminal(agent_access):
    task = await agent_access.pending_approval()
    assert (await agent_access.approve_http()).status_code == 200
    assert (await asyncio.wait_for(task, 2))['action'] == 'approve'
    await agent_access.accept()
    assert json.loads(agent_access.sent[0]['data']) == {'id': 7, 'result': {'decision': 'accept'}}


@pytest.mark.asyncio
async def test_approval_header_refresh_runs_outside_chat_write_transaction(agent_access):
    task = await agent_access.pending_approval()

    def assert_unlocked():
        assert not agent_access.transaction_open, 'Credential refresh cannot hold the chat write transaction'

    agent_access.authorization_hook = assert_unlocked
    assert (await agent_access.approve_http()).status_code == 200
    assert (await asyncio.wait_for(task, 2))['action'] == 'approve'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'field,value', [('task_id', 'replacement'), ('session_id', 'replacement'), ('state', 'stopping')]
)
async def test_execution_changed_during_authorization_cannot_resolve_old_approval(agent_access, field, value):
    await agent_access.pending_approval()

    def replace_execution():
        agent_access.message['meta']['agent_execution'][field] = value

    agent_access.authorization_hook = replace_execution
    response = await agent_access.approve_http()
    assert response.status_code == 409
    assert agent_access.message['meta']['agent_execution'][field] == value
    assert 'agent_resolution' not in agent_access.message['output'][0]


@pytest.mark.asyncio
async def test_native_approval_without_saved_execution_fails_closed(agent_access):
    await agent_access.pending_approval()
    agent_access.message['meta'].pop('agent_execution')
    assert (await agent_access.approve_http()).status_code == 403
    assert 'agent_resolution' not in agent_access.message['output'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
async def test_revoke_between_approval_and_send_blocks_native_input(agent_access, automatic):
    task = await agent_access.pending_approval(automatic=automatic)
    if not automatic:
        assert (await agent_access.approve_http()).status_code == 200
    assert (await asyncio.wait_for(task, 2))['action'] == 'approve'
    agent_access.revoke()
    with pytest.raises(HTTPException) as denied:
        await agent_access.accept()
    assert denied.value.status_code == 403
    assert agent_access.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['url', 'context_identity', 'global'])
async def test_input_rechecks_original_terminal_identity_and_global_switch(agent_access, change):
    agent_access.revoke(change)
    with pytest.raises(HTTPException) as denied:
        await agent_access.accept()
    assert denied.value.status_code == (403 if change == 'global' else 409)
    assert agent_access.sent == []


@pytest.mark.asyncio
async def test_input_waiting_for_send_lock_rechecks_access_after_lock_wait(agent_access):
    await agent_access.transport.lock.acquire()
    task = asyncio.create_task(agent_access.accept())
    agent_access.tasks.append(task)
    await asyncio.sleep(0)
    agent_access.revoke()
    agent_access.transport.lock.release()
    with pytest.raises(HTTPException) as denied:
        await task
    assert denied.value.status_code == 403 and agent_access.sent == []


@pytest.mark.asyncio
async def test_input_authorization_outage_can_retry_without_sending(agent_access):
    agent_access.config_error = HTTPException(503, 'Authorization unavailable')
    with pytest.raises(HTTPException) as denied:
        await agent_access.accept()
    assert denied.value.status_code == 503 and agent_access.sent == []
    agent_access.config_error = None
    await agent_access.accept()
    assert len(agent_access.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('outage', [False, True])
async def test_denied_start_does_not_claim_an_unknown_remote_effect(agent_access, monkeypatch, outage):
    transport = agent_access.transport
    transport.session_id = None

    @asynccontextmanager
    async def request(*args, **kwargs):
        yield SimpleNamespace(status=200, json=AsyncMock(return_value={'id': 'unexpected-session'}))

    @asynccontextmanager
    async def network(*args, **kwargs):
        agent_access.calls.append('network')
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(agent_transport, 'get_terminal_client', network)
    if outage:
        agent_access.config_error = HTTPException(503, 'Authorization unavailable')
    else:
        agent_access.revoke()
    with pytest.raises(HTTPException) as denied:
        await transport.start('codex', {'request_id': 'reply'})
    assert denied.value.status_code == (503 if outage else 403)
    assert not transport.start_attempted and transport.session_id is None
    assert await transport.close() is True
    assert agent_access.calls == []


def carrier(monkeypatch, state, *, on_open=lambda: None, on_input=lambda frame: None, output=None):
    observed = asyncio.Event()

    async def send(raw):
        frame = json.loads(raw)
        state.sent.append(frame)
        if frame['type'] == 'input':
            on_input(frame)
            observed.set()
        elif frame['type'] == 'ack':
            observed.set()

    async def messages():
        if output is not None:
            yield SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT, data=json.dumps({'type': 'output', 'sequence': 1, 'data': output})
            )
        await asyncio.Future()

    @asynccontextmanager
    async def ws_connect(*args, **kwargs):
        on_open()

        class Socket:
            send_str = staticmethod(send)

            def __aiter__(self):
                return messages()

        yield Socket()

    @asynccontextmanager
    async def terminal_client(*args, **kwargs):
        yield SimpleNamespace(ws_connect=ws_connect)

    monkeypatch.setattr(agent_transport, 'get_terminal_client', terminal_client)
    return observed


@pytest.mark.asyncio
@pytest.mark.parametrize('revoke_at', ['before_connect', 'after_connect', 'between_replayed_inputs'])
async def test_reconnect_pending_replay_rechecks_live_access(agent_access, monkeypatch, revoke_at):
    value = agent_access.transport
    futures = [asyncio.get_running_loop().create_future() for _ in range(2)]
    value.pending = {index + 1: (json.dumps({'id': index + 1}), future) for index, future in enumerate(futures)}
    observed = carrier(
        monkeypatch,
        agent_access,
        on_open=agent_access.revoke if revoke_at == 'after_connect' else lambda: None,
        on_input=(lambda _: agent_access.revoke()) if revoke_at == 'between_replayed_inputs' else lambda _: None,
    )
    if revoke_at == 'before_connect':
        agent_access.revoke()
    task = asyncio.create_task(value._pump())
    agent_access.tasks.append(task)
    try:
        await asyncio.wait({task}, timeout=0.2)
        inputs = [frame for frame in agent_access.sent if frame['type'] == 'input']
        assert len(inputs) == (1 if revoke_at == 'between_replayed_inputs' else 0)
        assert task.done() and isinstance(value.failure, HTTPException) and value.failure.status_code == 403
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for future in futures:
            if not future.done():
                future.cancel()
        await asyncio.gather(*futures, return_exceptions=True)


@pytest.mark.asyncio
async def test_revocation_preserves_received_output_and_executor_confirmed_delete(agent_access, monkeypatch):
    value = agent_access.transport
    value.pending_output = (1, ' {"method":"retained"} \n')

    async def retain(sequence, data):
        agent_access.retained.append((sequence, data))
        return 'stored'

    value.output_journal = retain

    @asynccontextmanager
    async def request(method, url, **kwargs):
        assert agent_access.retained == [(1, ' {"method":"retained"} \n')]
        agent_access.calls.append((method, url, kwargs['headers']['Authorization']))
        yield SimpleNamespace(status=200, json=AsyncMock(return_value={'status': True}))

    @asynccontextmanager
    async def client(*args, **kwargs):
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(agent_transport, 'get_terminal_client', client)
    agent_access.revoke()
    assert await value.close() is True
    assert value.completion_confirmed and value.cursor == 1
    assert agent_access.calls == [
        ('DELETE', 'http://synthetic-terminal/agents/sessions/remote-session', 'Bearer synthetic-server-key')
    ]


@pytest.mark.asyncio
async def test_read_only_drain_after_revocation_retains_output_without_input(agent_access, monkeypatch):
    value = agent_access.transport
    raw = ' {"method":"retained during drain"} \n'
    carrier(monkeypatch, agent_access, output=raw)
    agent_access.revoke()
    retained = AsyncMock(return_value='stored')
    assert await value.drain_available_output(retained, 0)
    retained.assert_awaited_once_with(1, raw)
    assert [frame['type'] for frame in agent_access.sent] == ['auth', 'ack']
