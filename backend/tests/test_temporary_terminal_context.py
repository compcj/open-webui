import asyncio
from test_terminal_file_routes import api
from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository


def context(api):
    repository = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = asyncio.run(repository.create('user-1'))
    api.app.state.temporary_contexts = repository
    headers = {
        'Authorization': 'owner',
        'X-Session-Id': 'temporary:' + doc['id'],
        'X-Temporary-Context-Id': doc['id'],
        'X-Temporary-Context-Epoch': doc['epoch'],
    }
    return repository, doc, headers


def test_terminal_scope_auth_and_epoch_isolation(api):
    repo, doc, headers = context(api)
    route = '/api/v1/terminals/terminal-1/files/view?path=/work/result.txt'
    result = api.client.get(route, headers=headers)
    assert result.status_code == 200
    assert api.state.calls[-1]['headers']['X-Terminal-Context-Id'] == f'temporary:{doc["id"]}:{doc["epoch"]}'
    api.state.calls.clear()
    assert api.client.get(route, headers={**headers, 'Authorization': 'other'}).status_code == 404
    assert api.client.get(route, headers={**headers, 'X-Temporary-Context-Epoch': 'old'}).status_code == 410
    asyncio.run(repo.close(doc['id'], 'user-1', doc['epoch']))
    assert api.client.get(route, headers=headers).status_code == 410
    assert not api.state.calls


def test_delivered_file_reference_keeps_original_scope_after_promotion_but_not_browse_access(api):
    repo, doc, headers = context(api)
    metadata = {'chat_id': 'temporary:' + doc['id'], 'temporary_context': {'id': doc['id'], 'epoch': doc['epoch']}}
    urls = api.urls(metadata=metadata, context_id=f'temporary:{doc["id"]}:{doc["epoch"]}')
    asyncio.run(repo.close(doc['id'], 'user-1', doc['epoch']))
    # A delivered, signed artifact is an existing reference. It does not reopen
    # the conversation or authorize arbitrary terminal paths after promotion.
    response = api.client.get(urls['download_url'], headers={'Authorization': 'owner'})
    assert response.status_code == 200
    assert api.state.calls[-1]['headers']['X-Session-Id'] == metadata['chat_id']
    assert api.client.get(urls['download_url'], headers={'Authorization': 'other'}).status_code == 403
    api.access.return_value = False
    assert api.client.get(urls['download_url'], headers={'Authorization': 'owner'}).status_code == 403
