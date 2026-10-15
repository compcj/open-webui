"""Synthetic strict-provider discovery, saved approval, execution and reload boundaries."""

import ast
import copy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import BACKEND, load_functions, runtime as citation_runtime
from test_image_tool_context import runtime as output_runtime, install_approval_store
from test_tool_citation_replay import pipeline
from test_streaming_tool_call_dedup import runtime as stream_runtime
from test_tool_dispatch_v0122 import wrapped


@pytest.mark.asyncio
async def test_search_dispatch_saved_approval_execution_and_json_reload(pipeline, stream_runtime, monkeypatch):
    ns = pipeline.ns
    search_module = ModuleType('open_webui.utils.tool_search')
    search_module.__dict__.update(JSONCodec=json, add_or_update_system_message=ns['add_or_update_system_message'])
    tree = ast.parse((BACKEND / 'utils/tool_search.py').read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if not isinstance(n, ast.ImportFrom) or not n.module.startswith('open_webui.')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'real_tool_search', 'exec'), search_module.__dict__)
    search_module.Config = SimpleNamespace(
        get=AsyncMock(return_value=True),
        get_many=AsyncMock(
            return_value={
                'chat.tool_search.defer_threshold': 1,
                'chat.tool_search.always_loaded': [],
                'chat.tool_search.defer_builtin_tools': True,
            }
        ),
    )
    monkeypatch.setitem(sys.modules, search_module.__name__, search_module)
    builtin_module = ModuleType('open_webui.tools.builtin')
    builtin_module.JSONCodec = json
    load_functions(builtin_module.__dict__, 'tools/builtin.py', {'search_tools'})
    monkeypatch.setitem(sys.modules, builtin_module.__name__, builtin_module)

    async def bind(function, extras, introspection):
        async def invoke(**kwargs):
            return await function(**kwargs, **extras)

        return invoke

    tool_module = ModuleType('open_webui.utils.tools')
    tool_module.get_async_tool_function_and_apply_extra_params = bind
    tool_module.get_builtin_function_introspection = lambda function: {}
    tool_module.get_builtin_tool_spec = lambda function: {'name': function.__name__, 'parameters': {'type': 'object'}}
    monkeypatch.setitem(sys.modules, tool_module.__name__, tool_module)
    target = AsyncMock(return_value='Fetched evidence from https://example.test/chain')
    tools = {
        'fetch_url': {
            'type': 'builtin',
            'tool_id': 'builtin:fetch_url',
            'callable': target,
            'spec': {
                'name': 'fetch_url',
                'description': 'Fetch page evidence',
                'parameters': {
                    'type': 'object',
                    'properties': {'url': {'type': 'string'}},
                    'required': ['url'],
                    'additionalProperties': False,
                },
            },
        }
    }
    metadata = {
        'tools': tools,
        'chat_id': 'chat',
        'message_id': 'reply',
        'params': {'tool_approval_mode': 'ask'},
        '_tool_schema_compatibility': 'gemini',
    }
    form = copy.deepcopy(ns['form_data'])
    deferred = await search_module.apply_tool_search(form, metadata, tools)
    assert deferred == {'fetch_url'}
    before = copy.deepcopy(form)
    found = json.loads(await tools['search_tools']['callable'](query='fetch_url'))
    assert found == {'tools': [tools['fetch_url']['spec']]}
    assert form == before
    assert tools['call_tool']['spec']['strict'] is True
    provider = wrapped(params={'url': 'https://example.test/chain'}, name=found['tools'][0]['name'])
    original = copy.deepcopy(provider['function'])
    provider['_gemini_schema_validation'] = True
    stored = {'role': 'assistant', 'model': 'synthetic', 'output': []}

    async def save(chat_id, message_id, update, **kwargs):
        assert (chat_id, message_id) == ('chat', 'reply')
        stored.update(copy.deepcopy(update))

    live = stream_runtime.ns
    live.update(
        metadata=metadata,
        form_data=form,
        save_to_chat=True,
        tool_calls=[[provider]],
        is_saved_chat_id=lambda value: value == 'chat',
        event_emitter=ns['event_emitter'],
        Chats=SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=save),
    )
    load_functions(live, 'utils/middleware.py', {'pause_for_tool_approval'})
    await live['execute_queued_calls']()
    target.assert_not_awaited()
    saved_call = stored['output'][0]
    assert (saved_call['name'], saved_call['status']) == ('fetch_url', 'pending')
    assert saved_call['provider_function'] == original
    assert saved_call['tool_schema_compatibility'] == 'gemini'

    # Simulate a process/client refresh: approval operates on serialized server output.
    message = json.loads(json.dumps(stored))
    install_approval_store(ns, message)
    ns['Chats'].get_accessible_chat_by_id = AsyncMock(return_value=SimpleNamespace(user_id='owner'))
    ns['Chats'].get_message_by_id_and_message_id = AsyncMock(return_value=message)
    message['user_id'] = 'owner'
    load_functions(ns, 'utils/tool_approval.py', {'resolve_tool_call_output'})
    ns['Chats'].get_chat_by_id = AsyncMock(return_value=SimpleNamespace(user_id='owner'))
    ns['get_event_emitter'] = AsyncMock(return_value=ns['event_emitter'])
    result = await ns['resolve_tool_call_output'](
        'chat', 'reply', SimpleNamespace(call_id=provider['id'], action='approve'), SimpleNamespace(id='owner')
    )
    assert result['resume'] == 'tools'
    load_functions(ns, 'utils/middleware.py', {'execute_tool_call', 'execute_tool_call_for_output'})
    ns.update(
        ast=ast,
        is_saved_chat_id=lambda value: value == 'chat',
        get_updated_tool_function=AsyncMock(side_effect=lambda **kw: kw['function']),
        build_terminal_file_tool_result=lambda *args: None,
        terminal_event_handler=AsyncMock(),
        get_event_emitter_and_caller=AsyncMock(return_value=(ns['event_emitter'], None)),
    )
    for _ in range(2):
        await ns['resume_tool_calls'](None, form, ns['user'], ns['model'], metadata, message)
    target.assert_awaited_once_with(url='https://example.test/chain')
    call_result = next(item for item in message['output'] if item['type'] == 'function_call_output')
    frozen = call_result['citation_context']
    assert frozen
    sources = [event for event in pipeline.events if event['type'] == 'source']
    assert len(sources) == 1
    reloaded = json.loads(json.dumps(message))
    prior = copy.deepcopy(reloaded)
    replay = ns['process_messages_with_output']([reloaded])
    call = next(row for row in replay if row.get('tool_calls'))['tool_calls'][0]
    assert call['function'] == original
    assert frozen in next(row for row in replay if row.get('role') == 'tool')['content']
    assert reloaded == prior
    live['output'] = reloaded['output']
    live['tool_calls'] = [[copy.deepcopy(provider)]]
    await live['execute_queued_calls']()
    target.assert_awaited_once()
    assert len([event for event in pipeline.events if event['type'] == 'source']) == 1
