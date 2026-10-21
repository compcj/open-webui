"""Imports use normal direct/call_tool approval and resolved-call replay deduplication."""

import copy
import json
import ast
import inspect
import re
from functools import partial
from functools import cache, update_wrapper
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, get_args, get_type_hints
from unittest.mock import AsyncMock

import pytest

from test_approval_execution_helper import runtime as execution_runtime
from test_knowledge_import import runtime as import_runtime, write_runtime
from test_streaming_tool_call_dedup import runtime as stream_runtime
from test_native_connections_integration import sender, CapturedSend
from test_tool_citation_replay import pipeline, citation_runtime, output_runtime, run_tool
from test_tool_result_storage import database, edit
from test_web_tool_citations import load_functions
from open_webui.utils.context_compaction_dispatch import apply_context_compaction, run_with_context_retry


def install(execution, imported, terminal):
    name = 'import_knowledge_terminal_file' if terminal else 'import_knowledge_url'
    function = partial(getattr(imported.module, name), **imported.context)
    execution.ns['get_updated_tool_function'] = AsyncMock(return_value=function)
    return name, {
        'call_tool': {'type': 'builtin', 'tool_id': 'builtin:call_tool'},
        name: {
            'type': 'builtin',
            'tool_id': f'builtin:{name}',
            'callable': function,
            'spec': {
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'knowledge_id': {'type': 'string'},
                        'path' if terminal else 'url': {'type': 'string'},
                        'filename': {'type': ['string', 'null']},
                    },
                    'required': ['knowledge_id', 'path' if terminal else 'url'],
                }
            },
        },
    }


def call(name, wrapped, terminal):
    arguments = {
        'knowledge_id': 'kb',
        'path' if terminal else 'url': '/home/source.pdf' if terminal else 'https://example.test/source.pdf',
    }
    return {
        'id': 'import-once',
        'function': {
            'name': 'call_tool' if wrapped else name,
            'arguments': json.dumps({'tool_name': name, 'params': json.dumps(arguments)} if wrapped else arguments),
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
async def test_import_approval_preserves_provider_call_and_never_exposes_document(
    execution_runtime, import_runtime, wrapped, terminal
):
    name, tools = install(execution_runtime, import_runtime, terminal)
    tool_call = call(name, wrapped, terminal)
    original = copy.deepcopy(tool_call['function'])
    metadata = {'tools': tools, 'params': {'tool_approval_mode': 'ask'}}
    denied = await execution_runtime.ns['execute_tool_call']({}, metadata, AsyncMock(return_value=False), tool_call)
    assert denied[1].startswith('Error: tool call was not approved')
    import_runtime.fetch.assert_not_awaited()
    result = await execution_runtime.ns['execute_tool_call']({}, metadata, AsyncMock(return_value=True), tool_call)
    assert json.loads(result[1])['status'] == 'success'
    assert '知识' not in result[1] and 'PDF-synthetic-original' not in result[1]
    assert len(result[1]) < 700
    import_runtime.upload.assert_awaited_once()
    if wrapped:
        assert tool_call['provider_function'] == original


@pytest.mark.asyncio
@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
async def test_resolved_import_replay_never_refetches_or_persists(
    execution_runtime, import_runtime, stream_runtime, wrapped, terminal
):
    name, tools = install(execution_runtime, import_runtime, terminal)
    stream = stream_runtime.ns
    stream['metadata'] = {'tools': tools}
    stream['execute_tool_call'] = AsyncMock(side_effect=execution_runtime.ns['execute_tool_call'])
    tool_call = call(name, wrapped, terminal)
    original = copy.deepcopy(tool_call)
    stream['tool_calls'] = [[tool_call]]
    await stream['execute_queued_calls']()
    import_runtime.upload.assert_awaited_once()
    stream['output'].append(
        {
            'type': 'function_call_output',
            'call_id': tool_call['id'],
            'status': 'completed',
            'output': [{'type': 'input_text', 'text': '{"status":"success","file_id":"new-file"}'}],
        }
    )
    stream['prior_output'] = copy.deepcopy(stream['output'])
    stream['tool_calls'] = [[original]]
    await stream['execute_queued_calls']()
    import_runtime.fetch.assert_awaited_once()
    import_runtime.upload.assert_awaited_once()
    stream['execute_tool_call'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', [False, True])
async def test_approved_resume_rechecks_live_collection_acl(execution_runtime, import_runtime, terminal):
    name, tools = install(execution_runtime, import_runtime, terminal)
    import_runtime.base.user_id = 'another-owner'
    import_runtime.grant.return_value = True
    queued = call(name, True, terminal)
    import_runtime.grant.return_value = False
    result = await execution_runtime.ns['execute_tool_call'](
        {}, {'chat_id': 'saved', 'tools': tools, 'params': {'tool_approval_mode': 'ask'}}, None, queued
    )
    assert 'error' in json.loads(result[1])
    import_runtime.fetch.assert_not_awaited()


def actual_spec(function):
    from langchain_core.utils.function_calling import convert_to_openai_function
    from pydantic import BaseModel, Field, create_model

    ns = dict(
        __name__=__name__,
        inspect=inspect,
        Callable=Callable,
        Awaitable=Awaitable,
        get_type_hints=get_type_hints,
        get_args=get_args,
        partial=partial,
        update_wrapper=update_wrapper,
        cache=cache,
        re=re,
        copy=copy,
        Any=Any,
        BaseModel=BaseModel,
        Field=Field,
        create_model=create_model,
        JSONCodec=json,
        convert_pydantic_model_to_openai_function_spec=convert_to_openai_function,
    )
    load_functions(
        ns,
        'utils/tools.py',
        {
            'get_builtin_function_introspection',
            'build_builtin_tool_spec_json',
            'get_builtin_tool_spec',
            'convert_function_to_pydantic_model',
            'parse_description',
            'parse_docstring',
            'clean_properties',
            'clean_openai_tool_schema',
        },
    )
    return ns['get_builtin_tool_spec'](function)


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['chat_completions', 'responses', 'gemini', 'anthropic'])
@pytest.mark.parametrize('mode', ['off', 'legacy', 'v2'])
@pytest.mark.parametrize('terminal', [False, True])
async def test_compact_import_receipt_and_schema_reach_actual_provider_wire_without_source_or_reexecution(
    database,
    execution_runtime,
    import_runtime,
    pipeline,
    sender,
    monkeypatch,
    protocol,
    mode,
    terminal,
):
    import importlib
    import sys
    from starlette.responses import JSONResponse

    v2 = importlib.import_module('open_webui.utils.context_compaction_v2')
    name, tools = install(execution_runtime, import_runtime, terminal)
    queued = call(name, True, terminal)
    result = await execution_runtime.ns['execute_tool_call']({}, {'tools': tools}, None, queued)
    receipt = result[1]
    assert json.loads(receipt)['status'] == 'success'
    ns = pipeline.ns
    ns['tool'] = tools[name]
    params = {
        'knowledge_id': 'kb',
        'path' if terminal else 'url': '/home/source.pdf' if terminal else 'https://example.test/source.pdf',
    }
    call_id = await run_tool(pipeline, name, params, receipt)
    ns['output'][0]['provider_function'] = queued['provider_function']
    await ns['assemble_results']()
    assert 'citation_context' not in ns['output'][-1]
    await edit(database, 'a', output=copy.deepcopy(ns['output']))
    ns['form_data']['tools'] = [{'type': 'function', 'function': actual_spec(getattr(import_runtime.module, name))}]
    await ns['dispatch_followup']()
    continuation = copy.deepcopy(ns['generate_chat_completion'].await_args.args[1])
    continuation['model'] = 'connection.native-test'
    request, router, captured = sender
    router['get_openai_connection'] = AsyncMock(
        return_value=(
            'https://gateway.example.test/custom/v1',
            'synthetic',
            {'api_type': protocol, 'prefix_id': 'connection'},
        )
    )
    load_functions(router, 'utils/responses_reasoning.py', {'remap_reasoning_effort_for_responses'})
    load_functions(router, 'routers/openai.py', {'_normalize_stored_item', 'convert_to_responses_payload'})
    router['copy'] = copy
    request.state.context_compaction = {
        'mode': mode,
        'chat_id': 'chat',
        'user_message_id': 'u',
        'metadata': {'chat_id': 'chat', 'message_id': 'a'},
        'settings': v2.CompactionSettings(
            mode=mode, default_context_window=32_000, recent_tokens=200, reminder_enabled=False
        ),
    }
    request.app.state.MODELS = {'connection.native-test': {}}
    monkeypatch.setattr(
        sys.modules['open_webui.utils.context_compaction_dispatch'],
        'apply_context_compaction',
        apply_context_compaction,
    )
    monkeypatch.setattr(v2, 'load_stored_models', AsyncMock(return_value={}))
    monkeypatch.setattr(v2, 'emit_event', AsyncMock())
    monkeypatch.setattr(
        v2,
        'generate_summary',
        AsyncMock(side_effect=AssertionError('Compact receipts should fit without summarization')),
    )
    wires = []

    async def send():
        with pytest.raises(CapturedSend):
            await router['generate_chat_completion'](
                request, copy.deepcopy(continuation), SimpleNamespace(id='owner', role='admin')
            )
        wire = json.loads(captured['send']['data'])
        wires.append(wire)
        if mode == 'v2' and len(wires) == 1:
            return JSONResponse({'error': {'code': 'context_length_exceeded'}}, status_code=400)
        return {'ok': True}

    assert await run_with_context_retry(request, send) == {'ok': True}
    assert len(wires) == (2 if mode == 'v2' else 1)
    for wire in wires:
        serialized = json.dumps(wire, ensure_ascii=False)
        assert 'new-file' in serialized and name in serialized
        assert 'call_tool' in serialized  # Original provider wrapper survives continuation projection.
        assert '知识' not in serialized and 'PDF-synthetic-original' not in serialized and 'base64' not in serialized
        assert '<tool_sources>' not in serialized and '__knowledge_write_context__' not in serialized
        assert '__metadata__' not in serialized and '__user__' not in serialized
        assert 'provider_function' not in serialized
    import_runtime.fetch.assert_awaited_once()
    import_runtime.upload.assert_awaited_once()
