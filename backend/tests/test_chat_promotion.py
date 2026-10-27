from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from starlette.requests import Request
from open_webui.utils.chat_promotion import creation_identity, begin_temporary_promotion
from open_webui.utils.context_store_errors import ContextConflict
from open_webui.utils.temporary_context_store import MemoryTemporaryBackend, TemporaryContextRepository
from open_webui.utils.temporary_contexts import sync_temporary_history
from test_temporary_context_graph import raw_graph


def request(headers, repository=None):
    return Request(
        {
            'type': 'http',
            'headers': [(key.lower().encode(), str(value).encode()) for key, value in headers.items()],
            'app': SimpleNamespace(state=SimpleNamespace(temporary_contexts=repository)),
        }
    )


def test_creation_key_is_actor_bound_and_ignores_only_transient_timestamp():
    req = request({'Idempotency-Key': 'ca776e08-053e-4c42-92b3-74448e9b04c5'})
    form = SimpleNamespace(chat={'history': raw_graph(), 'timestamp': 1}, variables={}, folder_id=None)
    owner = SimpleNamespace(id='owner')
    first = creation_identity(req, owner, form)
    form.chat['timestamp'] = 2
    assert creation_identity(req, owner, form) == first
    assert creation_identity(req, SimpleNamespace(id='other'), form)[0] != first[0]
    form.chat['history']['messages']['a']['content'] = 'edited'
    assert creation_identity(req, owner, form)[1] != first[1]


@pytest.mark.asyncio
async def test_promotion_exclusively_reserves_idle_history():
    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')
    current = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 0, raw_graph())
    req = request(
        {
            'X-Temporary-Context-Id': doc['id'],
            'X-Temporary-Context-Epoch': doc['epoch'],
            'X-Temporary-Context-Revision': current['revision'],
        },
        repo,
    )
    reservation = await begin_temporary_promotion(req, SimpleNamespace(id='owner'))
    assert reservation
    with pytest.raises(ContextConflict):
        await repo.register_tasks(
            doc['id'],
            'owner',
            doc['epoch'],
            [dict(task_id='new', reply_id='new', branch_id='a', model_id='model', worker_nonce='w')],
            expected_revision=current['revision'],
        )


@pytest.mark.asyncio
async def test_promotion_preserves_server_originals_missing_from_stale_page():
    repo = TemporaryContextRepository(MemoryTemporaryBackend())
    doc = await repo.create('owner')
    graph = raw_graph()
    graph['messages']['a']['content'] = 'complete buffered original'
    graph['messages']['a']['output'] = [
        {
            'type': 'function_call',
            'id': 'call',
            'call_id': 'call',
            'name': 'real',
            'arguments': '{}',
            'provider_function': {'name': 'call_tool', 'arguments': '{}'},
        }
    ]
    current = await sync_temporary_history(repo, 'owner', doc['id'], doc['epoch'], 0, graph)
    req = request(
        {
            'X-Temporary-Context-Id': doc['id'],
            'X-Temporary-Context-Epoch': doc['epoch'],
            'X-Temporary-Context-Revision': current['revision'],
        },
        repo,
    )
    form = SimpleNamespace(chat={'history': raw_graph(), 'messages': [], 'title': 'saved'})
    await begin_temporary_promotion(req, SimpleNamespace(id='owner'), form)
    assert form.chat['history']['messages']['a']['content'] == 'complete buffered original'
    assert form.chat['messages'][-1]['output'][0]['provider_function']['name'] == 'call_tool'
