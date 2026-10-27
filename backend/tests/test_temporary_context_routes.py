"""Exercise the actual new HTTP router with real cache state and isolated auth."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository
from open_webui.utils.context_store_errors import ContextUnavailable
from test_temporary_context_graph import raw_graph


@pytest.fixture
def api(monkeypatch):
    async def verified(request: Request):
        return SimpleNamespace(id=request.headers.get('X-Test-Actor', 'owner'), role='admin')

    auth = ModuleType('open_webui.utils.auth')
    auth.get_verified_user = verified
    monkeypatch.setitem(sys.modules, auth.__name__, auth)
    config = ModuleType('open_webui.models.config')
    config.Config = SimpleNamespace(get=AsyncMock(return_value=True))
    monkeypatch.setitem(sys.modules, config.__name__, config)
    file_module = ModuleType('open_webui.models.files')
    file_module.Files = SimpleNamespace(get_file_by_id=AsyncMock(return_value=None))
    monkeypatch.setitem(sys.modules, file_module.__name__, file_module)
    access = ModuleType('open_webui.utils.access_control.files')
    access.has_access_to_file = AsyncMock(return_value=False)
    monkeypatch.setitem(sys.modules, access.__name__, access)
    path = Path(__file__).parents[1] / 'open_webui/routers/temporary_contexts.py'
    spec = importlib.util.spec_from_file_location('temporary_router_test', path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'authenticate_socket', AsyncMock())
    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    app = FastAPI()
    app.state.temporary_contexts = repo
    app.state.redis = None
    app.include_router(module.router, prefix='/api/v1/temporary-contexts')
    return SimpleNamespace(client=TestClient(app), repo=repo, router=module)


def create(api):
    response = api.client.post('/api/v1/temporary-contexts/', json={})
    assert response.status_code == 200
    return response.json()


def test_http_sync_revision_state_and_foreign_admin_are_bound(api):
    doc = create(api)
    base = f'/api/v1/temporary-contexts/{doc["id"]}'
    sync = {'epoch': doc['epoch'], 'expected_revision': 0, 'history': raw_graph()}
    response = api.client.post(base + '/sync', json=sync)
    assert response.status_code == 200 and response.json()['revision'] == 1
    stale = api.client.post(base + '/sync', json=sync)
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'temporary_context_conflict'
    state = api.client.get(base + '/state', params={'epoch': doc['epoch']}).json()
    assert state['history']['messages']['a']['content'] == 'answer'
    assert state['tasks'] == [] and state['uncertain'] is False
    assert (
        api.client.get(
            base + '/state', params={'epoch': doc['epoch']}, headers={'X-Test-Actor': 'other-admin'}
        ).status_code
        == 404
    )
    assert api.client.get(base + '/state', params={'epoch': 'old'}).status_code == 410


def test_http_lease_does_not_change_raw_revision_and_checks_live_session(api):
    doc = create(api)
    base = f'/api/v1/temporary-contexts/{doc["id"]}'
    response = api.client.post(base + '/lease', json={'epoch': doc['epoch'], 'session_id': 'socket'})
    assert response.status_code == 200 and response.json()['revision'] == 0
    api.router.authenticate_socket.assert_awaited_once_with('owner', doc['id'], 'socket')


def test_http_invalid_provenance_shape_and_unknown_files_are_rejected(api):
    doc = create(api)
    base = f'/api/v1/temporary-contexts/{doc["id"]}'
    history = raw_graph()
    history['messages']['a']['tool_results'] = 'forged'
    response = api.client.post(base + '/sync', json={'epoch': doc['epoch'], 'expected_revision': 0, 'history': history})
    assert response.status_code == 400
    response = api.client.post(
        base + '/sync',
        json={
            'epoch': doc['epoch'],
            'expected_revision': 0,
            'history': raw_graph(),
            'files': [{'id': 'unavailable-file', 'type': 'file'}],
        },
    )
    assert response.status_code == 403


def test_http_storage_failure_stays_503_and_does_not_create_fallback(api, monkeypatch):
    monkeypatch.setattr(api.repo.backend, 'commit', AsyncMock(side_effect=ContextUnavailable()))
    response = api.client.post('/api/v1/temporary-contexts/', json={})
    assert response.status_code == 503
    assert response.json()['detail']['retryable'] is True
    assert not api.repo.backend._records


def test_replacement_requires_confirmed_old_epoch_and_never_guesses_from_missing(api):
    doc = create(api)
    response = api.client.post(
        '/api/v1/temporary-contexts/', json={'replaces': {'id': doc['id'], 'epoch': doc['epoch']}}
    )
    assert response.status_code == 409
    response = api.client.post('/api/v1/temporary-contexts/', json={'replaces': {'id': 'absent', 'epoch': 'absent'}})
    assert response.status_code == 410 and response.json()['detail']['rebuild_allowed'] is False


def test_rpc_claim_http_is_strict_owned_and_once_only(api, monkeypatch):
    import asyncio
    from test_temporary_rpc import bound, claim_fields
    from open_webui.utils import temporary_context_runtime as runtime

    async def prepare():
        repo, doc, metadata = await bound(monkeypatch)
        envelope = await runtime.temporary_rpc_envelope(metadata, {'type': 'execute:python', 'data': {}}, timeout=60)
        return repo, doc, envelope

    repo, doc, envelope = asyncio.run(prepare())
    api.client.app.state.temporary_contexts = repo
    url = f'/api/v1/temporary-contexts/{doc["id"]}/rpc/{envelope["rpc_id"]}/claim'
    body = {'epoch': doc['epoch'], **claim_fields(envelope)}
    assert api.client.post(url, json=body, headers={'X-Test-Actor': 'foreign-admin'}).status_code == 404
    assert api.client.post(url, json={**body, 'epoch': 'old'}).status_code == 410
    assert api.client.post(url, json={**body, 'socket_generation': True}).status_code == 400
    assert api.client.post(url, json={**body, 'payload': 'not-permitted'}).status_code == 400
    response = api.client.post(url, json=body)
    assert response.status_code == 200
    assert response.json()['claimed'] is True
    assert response.json()['remaining_ms'] > 0
    assert api.client.post(url, json=body).status_code == 409


def test_http_rejects_nonfinite_json_and_overlong_identity(api):
    for number in ('NaN', 'Infinity', '1e999'):
        response = api.client.post(
            '/api/v1/temporary-contexts/',
            content='{"value":' + number + '}',
            headers={'Content-Type': 'application/json'},
        )
        assert response.status_code == 400
    response = api.client.get('/api/v1/temporary-contexts/' + ('a' * 129) + '/state', params={'epoch': 'e'})
    assert response.status_code == 400
