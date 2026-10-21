"""Run production model fan-out with isolated providers and real V2 tool storage."""

import ast
import asyncio
import copy
import importlib
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from starlette.responses import Response
from test_tool_result_storage import database, edit


def fanout(monkeypatch, settings, payload_handler, provider):
    # Avoid importing main.py, whose initialization touches application data.
    # Execute its actual process_chat and fan-out, including errors and cleanup.
    path = Path(__file__).parents[1] / 'open_webui/main.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    endpoint = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    start = next(
        i
        for i, node in enumerate(endpoint.body)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat'
    )
    wrapper = ast.parse(
        'async def dispatch(request, form_data, user, metadata, model, message_ids, tasks=None): pass'
    ).body[0]
    wrapper.body = endpoint.body[start:]
    running = []

    async def create_task(redis, process, *, id, task_id):
        task = asyncio.create_task(process)
        running.append(task)
        return task_id, task

    config = ModuleType('open_webui.utils.context_compaction_config')
    config.load_compaction_settings = AsyncMock(return_value=settings)
    monkeypatch.setitem(sys.modules, config.__name__, config)
    writes = AsyncMock()
    namespace = {
        'asyncio': asyncio,
        'copy': copy,
        'uuid4': uuid4,
        'Request': Request,
        'Response': Response,
        'HTTPException': HTTPException,
        'log': logging.getLogger(__name__),
        'model_id': 'model-a',
        'model_info': None,
        'fallback_model': None,
        'model_controls': {},
        'request_params': {},
        'Models': SimpleNamespace(get_model_by_id=AsyncMock(return_value=None)),
        'Chats': SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=writes),
        'TASKS': SimpleNamespace(TITLE_GENERATION='title', TAGS_GENERATION='tags'),
        'is_saved_chat_id': lambda chat_id: chat_id == 'chat',
        'apply_model_controls': lambda params, *args, **kwargs: params,
        'process_chat_payload': payload_handler,
        'chat_completion_handler': provider,
        'build_chat_response_context': AsyncMock(return_value={}),
        'process_chat_response': AsyncMock(side_effect=lambda response, ctx: response),
        'create_task': create_task,
        'cleanup_task': AsyncMock(),
        'has_active_tasks': AsyncMock(return_value=True),
        'get_event_emitter': AsyncMock(return_value=None),
        'publish_chat_failed_event': AsyncMock(),
    }
    module = ast.Module(body=[wrapper], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), 'exec'), namespace)

    async def run(request, payload, metadata, entries):
        result = await namespace['dispatch'](
            request,
            payload,
            SimpleNamespace(id='owner', role='user'),
            metadata,
            request.app.state.MODELS['model-a'],
            entries,
        )
        replies = await asyncio.wait_for(asyncio.gather(*running), timeout=10)
        errors = [call.args[2]['error']['content'] for call in writes.call_args_list if 'error' in call.args[2]]
        return result, replies, errors

    return run


def inputs(model_ids):
    models = {model_id: {'id': model_id} for model_id in model_ids}
    metadata = {
        'chat_id': 'chat',
        'session_id': 'socket',
        'user_message_id': 'u',
        'chat_context': {'variables': {'task': 'original'}},
        'features': {'web_search': True},
        'params': {'function_calling': 'native'},
    }
    payload = {
        'model': model_ids[0],
        'metadata': metadata,
        'messages': [{'role': 'user', 'content': 'question'}],
        'params': {'options': {'seed': 1}},
        'features': metadata['features'],
    }
    request = Request(
        {
            'type': 'http',
            'app': SimpleNamespace(state=SimpleNamespace(MODELS=models, redis=None)),
            'headers': [(b'x-request-id', b'synthetic-request')],
            'state': {'metadata': metadata, 'model_id': model_ids[0], 'auth_type': 'jwt'},
        }
    )
    # Exercise cloning a Request whose State wrapper is already cached.
    request.state.token = object()
    entries = [{'model_id': model_id, 'message_id': f'reply-{i}'} for i, model_id in enumerate(model_ids)]
    return request, payload, metadata, entries


@pytest.mark.asyncio
@pytest.mark.parametrize('model_ids', [('model-a', 'model-b', 'model-c'), ('model-a', 'model-a')])
async def test_comparison_tools_prepare_and_replay_on_each_reply_branch(database, monkeypatch, model_ids):
    db = database
    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    sources = importlib.import_module('open_webui.utils.tool_result_sources')
    settings = v2.CompactionSettings(mode='v2', default_context_window=6000, recent_tokens=200, reminder_enabled=False)
    request, payload, metadata, entries = inputs(model_ids)
    for entry in entries:
        output = copy.deepcopy(db.raw['a']['output'])
        output[0]['output'] = f'evidence for {entry["message_id"]}'
        await edit(db, entry['message_id'], role='assistant', content='', parentId='u', output=output)
    original = copy.deepcopy(db.raw)
    arrived = 0
    ready = asyncio.Event()
    seen_requests = {}

    async def prepare(req, body, user, meta, model):
        nonlocal arrived
        reply_id = meta['message_id']
        output = db.raw[reply_id]['output']
        await sources.register_tool_results(req, meta, body['model'], output=output, output_ids=['out'])
        ref = {**sources.output_descriptors(output)['out'], 'message_id': reply_id}
        body['messages'] = [
            {'role': 'user', 'content': 'question', '_context_message_id': 'u'},
            {
                'role': 'assistant',
                '_context_message_id': reply_id,
                'tool_calls': [{'id': 'call', 'type': 'function', 'function': {'name': 'search', 'arguments': '{}'}}],
            },
            {
                'role': 'tool',
                'tool_call_id': 'call',
                'content': ref['text'],
                '_context_message_id': reply_id,
                '_tool_result_refs': [ref],
            },
        ]
        seen_requests[reply_id] = req
        arrived += 1
        if arrived == len(entries):
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return body, meta, [], False

    async def provider(req, body, user):
        first = await v2.prepare_v2_request(req, user, body, body['metadata'], body['model'], request.app.state.MODELS)
        replay = await v2.prepare_v2_request(req, user, body, body['metadata'], body['model'], request.app.state.MODELS)
        assert first == replay
        return first

    monkeypatch.setattr(v2, 'load_stored_models', AsyncMock(return_value={}))
    monkeypatch.setattr(v2, 'emit_event', AsyncMock())
    run = fanout(monkeypatch, settings, prepare, provider)
    result, replies, errors = await run(request, payload, metadata, entries)
    assert errors == []
    assert len(result['task_ids']) == len(entries)
    assert len(replies) == len(entries)
    assert db.raw == original
    for entry, reply in zip(entries, replies):
        reply_id, model_id = entry['message_id'], entry['model_id']
        text = f'evidence for {reply_id}'
        assert reply['messages'][-1]['content'] == text
        rows = await db.selections.list_selections('chat', model_id, reply_id)
        assert len(rows) == 1 and rows[0]['state'] == 'prepared'
        assert rows[0]['message_id'] == rows[0]['branch_id'] == reply_id
        assert rows[0]['text'] == text
        assert seen_requests[reply_id].state.context_compaction['metadata']['message_id'] == reply_id
    assert not hasattr(request.state, 'context_compaction')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['off', 'legacy', 'v2'])
@pytest.mark.parametrize('direct', [False, True])
async def test_comparison_detaches_nested_payload_metadata_and_runtime_state(monkeypatch, mode, direct):
    from open_webui.utils.context_compaction_v2 import CompactionSettings

    request, payload, metadata, entries = inputs(('model-a', 'model-a' if direct else 'model-b'))
    direct_model = {'id': 'model-a', 'info': {'meta': {'context_window': 8192}}, 'params': {'temperature': 0.2}}
    if direct:
        metadata['direct'] = request.state.direct = True
        request.state.model = copy.deepcopy(direct_model)
    original_payload, original_metadata = copy.deepcopy((payload, metadata))
    arrived = 0
    ready = asyncio.Event()
    captured = []

    async def prepare(req, body, user, meta, model):
        nonlocal arrived
        reply_id = meta['message_id']
        if direct:
            assert req.state.model == direct_model
            req.state.model['params']['temperature'] = reply_id
        body['messages'][0]['content'] = reply_id
        body['params']['options']['seed'] = reply_id
        meta['features']['web_search'] = reply_id
        meta['params']['function_calling'] = reply_id
        meta['chat_context']['variables']['task'] = reply_id
        req.state.cached_queries = [reply_id]
        req.state.skill_runtime_content = {'skill': reply_id}
        req.state.context_compaction['context_limit_retried'] = reply_id == 'reply-0'
        captured.append((req, body, meta))
        arrived += 1
        if arrived == len(entries):
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return body, meta, [], False

    run = fanout(monkeypatch, CompactionSettings(mode=mode), prepare, AsyncMock(return_value={'ok': True}))
    _, replies, errors = await run(request, payload, metadata, entries)
    assert errors == [] and replies == [{'ok': True}, {'ok': True}]
    for req, body, meta in captured:
        reply_id = meta['message_id']
        assert body['messages'][0]['content'] == reply_id
        assert body['params']['options']['seed'] == reply_id
        assert meta['features']['web_search'] == meta['params']['function_calling'] == reply_id
        assert meta['chat_context']['variables']['task'] == reply_id
        assert req.state.cached_queries == [reply_id]
        assert req.state.skill_runtime_content == {'skill': reply_id}
        assert req.state.context_compaction['context_limit_retried'] == (reply_id == 'reply-0')
        assert req.state.metadata is meta
        assert req.state.model_id == body['model']
        assert req.app is request.app and req.receive is request.receive
        assert req.state.token is request.state.token and req.state.auth_type == 'jwt'
        assert req.headers == request.headers
        if direct:
            assert req.state.model['params']['temperature'] == reply_id
    assert payload == original_payload and metadata == original_metadata
    assert not hasattr(request.state, 'context_compaction')
    assert not hasattr(request.state, 'cached_queries')
    if direct:
        assert request.state.model == direct_model


@pytest.mark.asyncio
async def test_comparison_preserves_consumed_request_body_for_plugins(monkeypatch):
    from open_webui.utils.context_compaction_v2 import CompactionSettings

    original, payload, metadata, entries = inputs(('model-a', 'model-b'))
    raw_body = json.dumps({'model': 'model-a', 'messages': [{'role': 'user', 'content': 'question'}]}).encode()
    receive = AsyncMock(side_effect=[{'type': 'http.request', 'body': raw_body}])
    request = Request(original.scope, receive=receive)
    original_json = await request.json()
    captured = []

    async def prepare(req, body, user, meta, model):
        # Filters/tools receive __request__ after FastAPI consumed the HTTP body.
        assert await req.body() == raw_body
        parsed = await req.json()
        assert parsed == original_json
        parsed['messages'][0]['content'] = meta['message_id']
        captured.append((meta['message_id'], parsed))
        return body, meta, [], False

    run = fanout(monkeypatch, CompactionSettings(mode='v2'), prepare, AsyncMock(return_value={'ok': True}))
    _, replies, errors = await run(request, payload, metadata, entries)
    assert errors == [] and replies == [{'ok': True}, {'ok': True}]
    assert len(captured) == len(entries)
    assert all(parsed['messages'][0]['content'] == reply_id for reply_id, parsed in captured)
    assert original_json == json.loads(raw_body)
    receive.assert_awaited_once()
