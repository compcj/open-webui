"""Real OpenAI routing + V2 budget resolution with synthetic model storage."""

import copy
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends

from open_webui.utils import context_compaction_dispatch as dispatch
from open_webui.utils import context_compaction_v2 as v2
from open_webui.utils.model_ids import strip_provider_model_prefix
from test_web_tool_citations import load_functions


@pytest.mark.asyncio
@pytest.mark.parametrize('preset', [False, True])
@pytest.mark.parametrize('tokens,compacts', [(89999, False), (90000, True)])
async def test_saved_100k_window_at_openai_send_boundary(monkeypatch, preset, tokens, compacts):
    selected_id = 'preset' if preset else 'connection.provider-model'
    base_id = 'connection.provider-model'
    saved = {
        'id': selected_id,
        'base_model_id': base_id if preset else None,
        'meta': {'context_window': 100000},
        'params': {},
    }
    row = SimpleNamespace(
        base_model_id=saved['base_model_id'],
        params=SimpleNamespace(model_dump=lambda: {}),
        model_dump=lambda: copy.deepcopy(saved),
    )
    storage = SimpleNamespace(get_model_by_id=AsyncMock(side_effect=lambda id: row if id == selected_id else None))
    module = ModuleType('open_webui.models.models')
    module.Models = storage
    monkeypatch.setitem(sys.modules, 'open_webui.models.models', module)
    # The shared pool may predate the model edit; the persisted metadata must win.
    public = {
        selected_id: {'id': selected_id, 'owned_by': 'openai', 'info': {**saved, 'meta': {'context_window': 262144}}}
    }
    payload = {
        'model': selected_id,
        'messages': [
            {'role': 'system', 'content': 'rules'},
            {'role': 'user', 'content': '', '_context_message_id': 'old'},
            {'role': 'assistant', 'content': 'answer', '_context_message_id': 'previous'},
            {'role': 'user', 'content': 'Continue', '_context_message_id': 'current'},
        ],
    }
    payload['messages'][1]['content'] = ' fact' * (tokens - v2.estimate_payload_tokens(payload))
    assert v2.estimate_payload_tokens(payload) == tokens
    raw = {}
    parent = None
    for message in payload['messages'][1:]:
        identity = message['_context_message_id']
        raw[identity] = {**message, 'id': identity, 'parentId': parent}
        parent = identity
    store = SimpleNamespace(
        read_snapshot=AsyncMock(return_value={'messages_map': raw, 'current_id': 'current'}),
        list_checkpoints=AsyncMock(return_value=[]),
        latest_version=AsyncMock(return_value=0),
        save_checkpoint=AsyncMock(return_value={'version': 1}),
    )
    monkeypatch.setattr(v2, 'get_checkpoint_store', lambda: store)
    monkeypatch.setattr(v2, 'emit_event', AsyncMock())
    summary = AsyncMock(return_value='Prior work and next steps.')
    monkeypatch.setattr(v2, 'generate_summary', summary)
    request = SimpleNamespace(
        state=SimpleNamespace(
            context_compaction={
                'mode': 'v2',
                'chat_id': 'chat',
                'user_message_id': 'current',
                'metadata': {},
                'settings': v2.CompactionSettings(mode='v2', reminder_enabled=False),
            }
        ),
        app=SimpleNamespace(
            state=SimpleNamespace(MODELS=public, OPENAI_MODELS={base_id: {'id': base_id, 'urlIdx': 0}})
        ),
    )
    namespace = dict(
        router=SimpleNamespace(post=lambda *a, **kw: lambda f: f),
        Depends=Depends,
        get_direct_chat_user=lambda: None,
        Config=SimpleNamespace(get=AsyncMock(return_value=True)),
        BYPASS_MODEL_ACCESS_CONTROL=False,
        Models=storage,
        check_model_access=AsyncMock(),
        get_openai_connection=AsyncMock(return_value=('https://provider.invalid/v1', '', {'prefix_id': 'connection'})),
        strip_provider_model_prefix=strip_provider_model_prefix,
        is_openai_new_model=lambda model: False,
        get_headers_and_cookies=AsyncMock(return_value=({}, {})),
    )
    load_functions(namespace, 'routers/openai.py', {'generate_chat_completion'})
    actual_hook = dispatch.apply_context_compaction
    sent = {}

    class CapturedSend(BaseException):
        pass

    async def capture(request, user, body, metadata, model_id):
        sent.update(await actual_hook(request, user, body, metadata, model_id))
        raise CapturedSend()

    monkeypatch.setattr(dispatch, 'apply_context_compaction', capture)
    with pytest.raises(CapturedSend):
        await namespace['generate_chat_completion'](request, payload, SimpleNamespace(role='admin'))
    usage = request.state.context_compaction['last_usage']
    assert usage['context_window'] == 100000
    assert usage['threshold'] == 90000
    assert usage['model'] == selected_id
    assert sent['model'] == 'provider-model'
    assert 'context_window' not in sent
    assert summary.await_count == int(compacts)
    assert store.save_checkpoint.await_count == int(compacts)
