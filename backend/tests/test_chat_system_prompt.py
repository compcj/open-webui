"""Initial chat preparation and provider copies must share one resolved system prompt."""

import ast
import copy
import logging
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import BACKEND, load_functions


@pytest.fixture
def runtime(monkeypatch):
    ns = {
        'deepcopy': copy.deepcopy,
        'Callable': Callable,
        'log': logging.getLogger(__name__),
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Config': SimpleNamespace(get=AsyncMock(return_value=True)),
        'check_model_access': AsyncMock(),
    }
    load_functions(
        ns,
        'utils/misc.py',
        {'get_output_text', 'get_content_from_message', 'update_message_content', 'add_or_update_system_message'},
    )
    load_functions(
        ns,
        'utils/payload.py',
        {
            '_overlay_params',
            'apply_system_prompt_to_body',
            'apply_model_params_to_body',
            'remove_open_webui_params',
            'apply_model_params_to_body_openai',
        },
    )
    ns['resolve_system_prompt'] = AsyncMock(side_effect=lambda system, metadata, user: system or '')
    router_tree = ast.parse((BACKEND / 'routers/openai.py').read_text(encoding='utf-8'))
    router = next(
        n for n in router_tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'generate_chat_completion'
    )
    end = next(
        i
        for i, node in enumerate(router.body)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'models' for t in node.targets)
    )
    wrapper = ast.parse('async def router_prepare(request, form_data, user, **kwargs): pass')
    wrapper.body[0].body = router.body[:end] + [ast.Return(value=ast.Name(id='payload', ctx=ast.Load()))]
    exec(compile(ast.fix_missing_locations(wrapper), 'router_prepare', 'exec'), ns)
    load_functions(
        ns, 'utils/chat.py', {'prepare_chat_system_prompt', 'generate_chat_completion', '_generate_chat_completion'}
    )

    async def retry(request, send):
        return await send()

    dispatch = ModuleType('open_webui.utils.context_compaction_dispatch')
    dispatch.run_with_context_retry = retry
    guard = ModuleType('open_webui.utils.context_compaction_v2')
    guard.internal_compaction = nullcontext
    monkeypatch.setitem(sys.modules, dispatch.__name__, dispatch)
    monkeypatch.setitem(sys.modules, guard.__name__, guard)
    ns.update(
        generate_openai_chat_completion=ns['router_prepare'],
        generate_function_chat_completion=ns['router_prepare'],
        generate_ollama_chat_completion=ns['router_prepare'],
        convert_chat_completions_payload_to_ollama=lambda value: value,
        convert_ollama_response_to_chat_completions=lambda value: value,
        JSONResponse=type('JSONResponse', (), {}),
        generate_direct_chat_completion=AsyncMock(side_effect=lambda request, form_data, **kwargs: form_data),
    )
    # Execute the main entry point's actual preparation/send sequence. Capture
    # its canonical form, which is also passed to build_chat_response_context.
    main_tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    process = next(n for n in ast.walk(main_tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'process_chat')
    body = next(
        n.body
        for n in ast.walk(process)
        if isinstance(n, ast.Try)
        and any(isinstance(c, ast.If) and isinstance(c.test, ast.Name) and c.test.id == 'paused' for c in n.body)
    )
    start = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == 'paused'
    )
    end = next(
        i
        for i, n in enumerate(body)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'response' for t in n.targets)
    )
    wrapper = ast.parse('async def first_send(request, form_data, user):\n    pass\n    return form_data, response')
    wrapper.body[0].body = body[start + 1 : end + 1] + wrapper.body[0].body[-1:]
    ns['chat_completion_handler'] = ns['generate_chat_completion']
    exec(compile(ast.fix_missing_locations(wrapper), 'main_first_send', 'exec'), ns)
    return ns


def context(ns, *, kind='openai', system='Model instructions'):
    row = SimpleNamespace(base_model_id=None, params=SimpleNamespace(model_dump=lambda: {'system': system}))
    ns['Models'] = SimpleNamespace(get_model_by_id=AsyncMock(return_value=row))
    model = {'id': 'test', 'owned_by': kind, **({'pipe': True} if kind == 'pipe' else {})}
    request = SimpleNamespace(
        state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(MODELS={'test': model}))
    )
    if kind == 'direct':
        request.state.direct = True
        request.state.model = model
    form = {
        'model': 'test',
        'messages': [
            {'role': 'system', 'content': 'User instructions'},
            {'role': 'user', 'content': 'Read the fixture'},
        ],
    }
    return request, form, SimpleNamespace(id='actor', role='admin')


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['openai', 'ollama', 'pipe'])
@pytest.mark.parametrize('system', ['Model instructions', ''])
async def test_initial_and_multiple_tool_sends_keep_the_same_system(runtime, kind, system):
    request, form, user = context(runtime, kind=kind, system=system)
    canonical, first = await runtime['first_send'](request, form, user)
    expected = f'{system}\nUser instructions' if system else 'User instructions'
    assert first['messages'][0]['content'] == expected
    assert canonical['messages'][0]['content'] == expected
    previous = copy.deepcopy(first['messages'])
    for index in range(3):
        canonical['messages'].extend(
            [
                {
                    'role': 'assistant',
                    'tool_calls': [
                        {'id': str(index), 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}
                    ],
                },
                {'role': 'tool', 'tool_call_id': str(index), 'content': f'Fixture {index}'},
            ]
        )
        snapshot = copy.deepcopy(canonical)
        sent = await runtime['generate_chat_completion'](request, canonical, user, bypass_system_prompt=True)
        assert sent['messages'][: len(previous)] == previous
        assert sent['messages'][0]['content'] == expected
        assert canonical == snapshot
        previous = copy.deepcopy(sent['messages'])
    assert runtime['resolve_system_prompt'].await_count == 1


@pytest.mark.asyncio
async def test_dynamic_model_prompt_is_resolved_once_and_isolated_from_later_settings(runtime):
    request, form, user = context(runtime, system='Model {{CURRENT_TIME}}')
    runtime['resolve_system_prompt'].side_effect = ['Resolved first timestamp', 'Resolved second timestamp']
    canonical, first = await runtime['first_send'](request, form, user)
    runtime['Models'].get_model_by_id.return_value.params.model_dump = lambda: {
        'system': 'Changed during tool execution'
    }
    followup = await runtime['generate_chat_completion'](request, canonical, user, bypass_system_prompt=True)
    assert followup['messages'] == first['messages']
    runtime['resolve_system_prompt'].assert_awaited_once()


@pytest.mark.asyncio
async def test_browser_direct_does_not_load_a_colliding_server_model(runtime):
    request, form, user = context(runtime, kind='direct')
    original = copy.deepcopy(form)
    canonical, first = await runtime['first_send'](request, form, user)
    assert canonical == original == first
    runtime['Models'].get_model_by_id.assert_not_awaited()
    runtime['resolve_system_prompt'].assert_not_awaited()


@pytest.mark.asyncio
async def test_standalone_and_explicit_bypass_keep_their_original_semantics(runtime):
    request, form, user = context(runtime)
    original = copy.deepcopy(form)
    first = await runtime['generate_chat_completion'](request, form, user)
    assert first['messages'][0]['content'] == 'Model instructions\nUser instructions'
    bypassed = await runtime['generate_chat_completion'](request, form, user, bypass_system_prompt=True)
    assert bypassed['messages'] == original['messages']
    assert form == original


@pytest.mark.asyncio
async def test_prompt_resolution_keeps_dispatch_metadata_precedence(runtime):
    request, form, user = context(runtime)
    form['metadata'] = {'chat_variables': {'source': 'filter'}, 'payload_only': True}
    request.state.metadata = {'chat_variables': {'source': 'canonical'}}
    original = copy.deepcopy(form)
    await runtime['first_send'](request, form, user)
    assert runtime['resolve_system_prompt'].await_args.args[1] == {
        'chat_variables': {'source': 'canonical'},
        'payload_only': True,
    }
    assert form == original
