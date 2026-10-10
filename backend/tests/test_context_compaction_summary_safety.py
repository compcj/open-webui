"""Internal V2 summary budgets and lossless context-error recovery."""

import asyncio
import copy
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.responses import JSONResponse

from open_webui.utils import context_compaction_v2 as v2
from open_webui.utils.context_compaction_dispatch import apply_context_compaction


@pytest.mark.parametrize('cap,expected', [(None, 5000), (2000, 5000), (8000, 8000), (12000, 12000)])
def test_history_summary_percentage_is_a_floor_without_8192_cap(cap, expected):
    settings = v2.CompactionSettings(summary_max_output_tokens=cap)
    models = {'summary': {'info': {'meta': {'context_window': 100000}}}}
    budget = v2.resolve_summary_budget(settings, 'summary', models, {}, fixed_tokens=0)
    assert budget.output_reserve == expected
    assert budget.input_budget == 95000 - expected
    # Ordinary chat output settings retain their existing semantics.
    assert v2.resolve_budget(settings, 'summary', models, {'max_tokens': 2000}).output_reserve == 2000


def test_summary_inheritance_num_ctx_fixed_handoff_and_tool_hard_cap():
    settings = v2.CompactionSettings(summary_model='summary', summary_output_percent=5)
    models = {
        'chat': {'info': {'meta': {'context_window': 100000}}},
        'summary': {'info': {'base_model_id': 'base', 'params': {'max_tokens': 2000}}},
        'base': {'owned_by': 'ollama', 'info': {'meta': {'context_window': 12000}, 'params': {'num_ctx': 8000}}},
    }
    budget = v2.resolve_summary_budget(settings, 'summary', models, {}, fixed_tokens=3100)
    assert budget.context_window == 8000
    assert budget.output_reserve == 1500  # min(max(400,2000), (7600-3100)//3)
    assert budget.input_budget == 6100
    tool = v2.resolve_summary_budget(
        settings, 'summary', models, {}, fixed_tokens=3100, purpose='tool', output_budget=117
    )
    assert tool.output_reserve == 117
    assert tool.input_budget == 7483


@pytest.mark.parametrize('invalid', [0, 101, True, '5', 1.5])
def test_summary_percent_is_a_strict_integer_in_range(invalid):
    with pytest.raises(ValueError):
        v2.CompactionSettings(summary_output_percent=invalid)


@pytest.fixture
def summary_runtime(monkeypatch):
    chat = ModuleType('open_webui.utils.chat')
    payload = ModuleType('open_webui.utils.payload')
    payload.resolve_system_prompt = AsyncMock(return_value='summary-model system rules')

    def apply(body, model, params):
        return {**body, **{k: v for k, v in params.items() if k != 'system'}}

    payload.apply_params_to_form_data = apply
    monkeypatch.setitem(sys.modules, 'open_webui.utils.chat', chat)
    monkeypatch.setitem(sys.modules, 'open_webui.utils.payload', payload)
    monkeypatch.setattr(v2, 'load_stored_models', AsyncMock(return_value={}))
    return chat


@pytest.mark.asyncio
async def test_final_guard_enforces_output_after_provider_params_without_contextvar(summary_runtime):
    seen = []
    parent_state = {'mode': 'v2', 'calibration': {'tokens': 1}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={'chat_id': 'chat'}, context_compaction=parent_state))
    models = {'summary': {'info': {'meta': {'context_window': 100000}, 'params': {'max_completion_tokens': 8000}}}}
    req.app = SimpleNamespace(state=SimpleNamespace(MODELS=models))

    async def send(summary_req, form_data, **kwargs):
        assert summary_req.state is not req.state
        assert summary_req.state.context_compaction is not parent_state
        form_data['max_tokens'] = 200000
        form_data['max_completion_tokens'] = 200000
        form_data['options'] = {'num_predict': 200000}
        # Simulate a provider hook outside the ambient internal ContextVar.
        token = v2._INTERNAL.set(False)
        try:
            final = await apply_context_compaction(
                summary_req, None, form_data, {'task': 'context_compaction_v2'}, 'summary'
            )
        finally:
            v2._INTERNAL.reset(token)
        assert final['max_completion_tokens'] == 8000
        assert 'max_tokens' not in final and 'num_predict' not in final.get('options', {})
        seen.append(final)
        summary_req.state.metadata['leak'] = True
        return {'choices': [{'message': {'content': 'handoff'}}]}

    summary_runtime.generate_chat_completion = send
    result = await v2.generate_summary(
        req, None, 'summary', models, [{'role': 'user', 'content': 'facts ' * 100}], '', v2.CompactionSettings()
    )
    assert result == 'handoff'
    assert req.state.metadata == {'chat_id': 'chat'}
    assert parent_state == {'mode': 'v2', 'calibration': {'tokens': 1}}
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_context_http_error_rebuilds_same_cursor_and_reuses_tightened_limit(summary_runtime, monkeypatch):
    source = [{'role': 'user', 'content': 'abcdefghijklmnpqrstuvwxyz0123456789' * 100}]
    expected = ''.join(v2.summary_units(source))
    attempts, successful = [], []
    # Deterministic token accounting makes exact cursor coverage observable.
    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(str(value or '')))
    models = {'summary': {'info': {'meta': {'context_window': 1500}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))

    async def send(summary_req, form_data, **kwargs):
        fragment = form_data['messages'][-1]['content'].split('Conversation data fragment (may continue):\n', 1)[1]
        attempts.append(fragment)
        if len(attempts) == 1:
            return JSONResponse({'error': {'code': 'context_length_exceeded'}}, status_code=400)
        successful.append(fragment)
        return {'choices': [{'message': {'content': 'ok'}}]}

    summary_runtime.generate_chat_completion = send
    await v2.generate_summary(req, None, 'summary', models, source, '', v2.CompactionSettings(recent_tokens=1))
    assert len(attempts[1]) < len(attempts[0])
    assert attempts[0].startswith(attempts[1])
    assert ''.join(successful) == expected
    assert all(len(fragment) <= len(attempts[1]) for fragment in successful[1:])


@pytest.mark.asyncio
async def test_tool_purpose_is_explicit_and_hard_budget_caps_provider_body(summary_runtime):
    seen = []
    models = {'summary': {'info': {'meta': {'context_window': 100000}, 'params': {'max_tokens': 8000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))

    async def send(request, form_data, **kwargs):
        seen.append(form_data)
        return {'choices': [{'message': {'content': 'ok'}}]}

    summary_runtime.generate_chat_completion = send
    settings = v2.CompactionSettings(
        summary_prompt='custom', tool_summary_prompt='different', summary_max_output_tokens=2000
    )
    await v2.generate_summary(
        req,
        None,
        'summary',
        models,
        [{'role': 'tool', 'content': 'data ' * 100}],
        '',
        settings,
        purpose='tool',
        output_budget=127,
    )
    assert seen[0]['max_tokens'] == 127


@pytest.mark.asyncio
async def test_empty_oversized_and_cancellation_are_bounded(summary_runtime):
    models = {'summary': {'info': {'meta': {'context_window': 1000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    settings = v2.CompactionSettings(
        recent_tokens=1, summary_max_output_tokens=1, summary_output_percent=1, max_retries=1
    )
    for content in ('', 'word ' * 100):
        send = AsyncMock(return_value={'choices': [{'message': {'content': content}}]})
        summary_runtime.generate_chat_completion = send
        with pytest.raises(v2.CompactionFailure):
            await v2.generate_summary(
                req, None, 'summary', models, [{'role': 'user', 'content': 'facts ' * 100}], '', settings
            )
        assert send.await_count == 2
    send = AsyncMock(side_effect=asyncio.CancelledError)
    summary_runtime.generate_chat_completion = send
    with pytest.raises(asyncio.CancelledError):
        await v2.generate_summary(req, None, 'summary', models, [{'role': 'user', 'content': 'facts'}], '', settings)
    assert send.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'initial_key,final_key', [('max_completion_tokens', 'max_tokens'), ('max_tokens', 'max_completion_tokens')]
)
async def test_guard_preserves_provider_output_parameter_conversion(summary_runtime, initial_key, final_key):
    models = {'summary': {'info': {'params': {initial_key: 8000}, 'meta': {'context_window': 100000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))

    async def send(summary_req, form_data, **kwargs):
        form_data[final_key] = form_data.pop(initial_key)
        final = await apply_context_compaction(summary_req, None, form_data, {}, 'summary')
        assert final[final_key] == 8000
        assert initial_key not in final
        return {'choices': [{'message': {'content': 'ok'}}]}

    summary_runtime.generate_chat_completion = send
    await v2.generate_summary(
        req, None, 'summary', models, [{'role': 'user', 'content': 'facts'}], '', v2.CompactionSettings()
    )


@pytest.mark.asyncio
async def test_real_request_detaches_runtime_compaction_objects_before_copy(summary_runtime):
    from starlette.requests import Request

    class RuntimeObject:
        def __deepcopy__(self, memo):
            raise AssertionError('Runtime compaction state must not be copied into internal summaries')

    models = {'summary': {'info': {'meta': {'context_window': 100000}}}}
    req = Request(
        {
            'type': 'http',
            'app': SimpleNamespace(state=SimpleNamespace(MODELS=models)),
            'state': {
                'metadata': {'chat_id': 'chat'},
                'model': {'params': {'temperature': 0.2}},
                'context_compaction': {'mode': 'v2', 'runtime': RuntimeObject()},
            },
        }
    )

    async def send(summary_req, form_data, **kwargs):
        assert summary_req.scope['state'] is not req.scope['state']
        assert summary_req.state.context_compaction is None
        summary_req.state.model['params']['temperature'] = 0.8
        return {'choices': [{'message': {'content': 'ok'}}]}

    summary_runtime.generate_chat_completion = send
    await v2.generate_summary(
        req, None, 'summary', models, [{'role': 'user', 'content': 'facts'}], '', v2.CompactionSettings()
    )
    assert req.state.model['params']['temperature'] == 0.2
    assert req.state.context_compaction['mode'] == 'v2'


@pytest.mark.asyncio
async def test_no_reduction_handoff_fails_even_when_within_actual_output_cap(summary_runtime):
    models = {'summary': {'info': {'meta': {'context_window': 100000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    summary_runtime.generate_chat_completion = AsyncMock(
        return_value={'choices': [{'message': {'content': 'long handoff ' * 100}}]}
    )
    with pytest.raises(v2.CompactionFailure, match='did not reduce'):
        await v2.generate_summary(
            req, None, 'summary', models, [{'role': 'user', 'content': 'tiny'}], '', v2.CompactionSettings()
        )


@pytest.mark.asyncio
async def test_task_metadata_cannot_authorize_a_summary_guard(monkeypatch):
    prepare = AsyncMock(side_effect=lambda request, user, payload, *args, **kwargs: payload)
    monkeypatch.setattr(v2, 'prepare_v2_request', prepare)
    models = {'summary': {}}
    req = SimpleNamespace(
        state=SimpleNamespace(context_compaction={'mode': 'v2'}),
        app=SimpleNamespace(state=SimpleNamespace(MODELS=models)),
    )
    payload = {'model': 'summary', 'max_tokens': 7777, 'messages': [{'role': 'user', 'content': 'hello'}]}
    result = await apply_context_compaction(
        req, None, payload, {'task': 'context_compaction_v2', 'context_compaction_summary': True}, 'summary'
    )
    assert result['max_tokens'] == 7777
    prepare.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_chunk_recounts_fixed_system_previous_handoff_and_output(summary_runtime, monkeypatch):
    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(str(value or '')))
    models = {'summary': {'info': {'meta': {'context_window': 3000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    source = [{'role': 'user', 'content': 'abcdefghijklmnpqrstuvwxyz' * 250}]
    bodies = []

    async def send(summary_req, form_data, **kwargs):
        bodies.append(copy.deepcopy(form_data))
        final = await apply_context_compaction(summary_req, None, form_data, {}, 'summary')
        assert v2.estimate_payload_tokens(final) + final['max_tokens'] <= 2850
        return {'choices': [{'message': {'content': 's' * (600 if len(bodies) == 1 else 200)}}]}

    summary_runtime.generate_chat_completion = send
    cfg = v2.CompactionSettings(recent_tokens=1, summary_prompt='summarize', summary_max_output_tokens=1800)
    await v2.generate_summary(req, None, 'summary', models, source, '', cfg)
    assert len(bodies) > 2
    assert bodies[1]['max_tokens'] < bodies[0]['max_tokens']
    for body in bodies:
        probe = copy.deepcopy(body)
        content = probe['messages'][-1]['content']
        probe['messages'][-1]['content'] = (
            content.split('Conversation data fragment (may continue):\n')[0]
            + 'Conversation data fragment (may continue):\n'
        )
        fixed = v2.estimate_payload_tokens(probe)
        assert body['max_tokens'] == min(1800, (2850 - fixed) // 3)


@pytest.mark.asyncio
async def test_fixed_prompt_or_media_failure_preserves_original_source(summary_runtime):
    models = {'summary': {'info': {'meta': {'context_window': 1000}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    send = AsyncMock(return_value={'choices': [{'message': {'content': 'ok'}}]})
    summary_runtime.generate_chat_completion = send
    source = [
        {'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.test/image.png'}}]}
    ]
    original = copy.deepcopy(source)
    with pytest.raises(v2.CompactionFailure, match='media block'):
        await v2.generate_summary(req, None, 'summary', models, source, '', v2.CompactionSettings())
    assert source == original
    send.reset_mock()
    with pytest.raises(v2.CompactionFailure, match='previous handoff'):
        await v2.generate_summary(
            req,
            None,
            'summary',
            models,
            [{'role': 'user', 'content': 'facts'}],
            'many tokens ' * 5000,
            v2.CompactionSettings(),
        )
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('error_kind', ['exception', 'http'])
async def test_final_guard_context_error_shrinks_and_exhaustion_is_bounded(summary_runtime, monkeypatch, error_kind):
    from fastapi import HTTPException

    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(str(value or '')))
    models = {'summary': {'info': {'meta': {'context_window': 1500}}}}
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    fragments = []

    async def send(summary_req, form_data, **kwargs):
        fragment = form_data['messages'][-1]['content'].split('Conversation data fragment (may continue):\n')[1]
        fragments.append(fragment)
        if error_kind == 'exception':
            raise HTTPException(400, detail={'code': 'context_length_exceeded'})
        form_data['messages'].insert(0, {'role': 'system', 'content': 'late provider instructions ' * 100})
        return await apply_context_compaction(summary_req, None, form_data, {}, 'summary')

    summary_runtime.generate_chat_completion = send
    with pytest.raises(v2.CompactionFailure):
        await v2.generate_summary(
            req,
            None,
            'summary',
            models,
            [{'role': 'user', 'content': 'facts ' * 1000}],
            '',
            v2.CompactionSettings(max_retries=2),
        )
    assert len(fragments) == 3
    assert len(fragments[0]) > len(fragments[1]) > len(fragments[2])
    assert fragments[0].startswith(fragments[1]) and fragments[1].startswith(fragments[2])


@pytest.mark.asyncio
async def test_final_ollama_num_ctx_recomputes_safe_output_and_rebuilds_fragment(summary_runtime, monkeypatch):
    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(str(value or '')))
    models = {
        'summary': {'owned_by': 'ollama', 'info': {'meta': {'context_window': 10000}, 'params': {'num_predict': 2000}}}
    }
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    attempts, success = [], []
    source = [{'role': 'user', 'content': 'abcdefghijklmnpqrstuvwxyz' * 100}]
    expected = ''.join(v2.summary_units(source))

    async def send(summary_req, form_data, **kwargs):
        fragment = form_data['messages'][-1]['content'].split('Conversation data fragment (may continue):\n')[1]
        attempts.append(fragment)
        form_data.setdefault('options', {})['num_ctx'] = 1500
        final = await apply_context_compaction(summary_req, None, form_data, {}, 'summary')
        assert final['options']['num_predict'] < 500
        assert v2.estimate_payload_tokens(final) + final['options']['num_predict'] <= 1425
        success.append(fragment)
        return {'choices': [{'message': {'content': 'ok'}}]}

    summary_runtime.generate_chat_completion = send
    cfg = v2.CompactionSettings(summary_prompt='summarize', max_retries=2)
    await v2.generate_summary(req, None, 'summary', models, source, '', cfg)
    assert len(attempts[0]) > len(attempts[1]) > len(attempts[2])
    assert ''.join(success) == expected


@pytest.mark.asyncio
async def test_output_validation_uses_cap_lowered_by_final_guard(summary_runtime, monkeypatch):
    monkeypatch.setattr(v2, 'estimate_text', lambda value: len(str(value or '')))
    models = {
        'summary': {'owned_by': 'ollama', 'info': {'meta': {'context_window': 10000}, 'params': {'num_predict': 2000}}}
    }
    req = SimpleNamespace(state=SimpleNamespace(metadata={}), app=SimpleNamespace(state=SimpleNamespace(MODELS=models)))
    outputs = []

    async def send(summary_req, form_data, **kwargs):
        form_data.setdefault('options', {})['num_ctx'] = 1500
        final = await apply_context_compaction(summary_req, None, form_data, {}, 'summary')
        cap = final['options']['num_predict']
        outputs.append(cap)
        return {'choices': [{'message': {'content': 's' * (cap + 1)}}]}

    summary_runtime.generate_chat_completion = send
    with pytest.raises(v2.CompactionFailure, match='output allowance'):
        await v2.generate_summary(
            req,
            None,
            'summary',
            models,
            [{'role': 'user', 'content': 'facts'}],
            '',
            v2.CompactionSettings(summary_prompt='summarize', max_retries=1),
        )
    assert len(outputs) == 2 and all(cap < 500 for cap in outputs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'model_id,raw_id,expected_key',
    [
        ('gpt-5', None, 'max_completion_tokens'),
        ('connection.gpt-5.2', 'gpt-5.2', 'max_completion_tokens'),
        ('connection.o3-mini', 'o3-mini', 'max_completion_tokens'),
        ('gpt-4o', None, 'max_tokens'),
        ('connection.gpt-4o', 'gpt-4o', 'max_tokens'),
        ('gpt-5-alias', 'provider-chat-model', 'max_tokens'),
    ],
)
async def test_actual_direct_summary_route_uses_provider_output_key_without_private_inheritance(
    monkeypatch, model_id, raw_id, expected_key
):
    import logging
    import uuid
    from copy import deepcopy
    from test_web_tool_citations import load_functions

    # Execute the real chat dispatcher and direct socket route, avoiding imports
    # of provider/database application modules and capturing only synthetic RPC.
    captured = []

    async def event_call(event):
        captured.append(deepcopy(event['data']['form_data']))
        return {'choices': [{'message': {'content': 'handoff'}}]}

    chat = ModuleType('open_webui.utils.chat')
    namespace = {
        'asyncio': asyncio,
        'deepcopy': deepcopy,
        'log': logging.getLogger(__name__),
        'logging': logging,
        'uuid': uuid,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'get_event_call': AsyncMock(return_value=event_call),
    }
    load_functions(
        namespace,
        'utils/chat.py',
        {'generate_chat_completion', '_generate_chat_completion', 'generate_direct_chat_completion'},
    )
    chat.generate_chat_completion = namespace['generate_chat_completion']
    monkeypatch.setitem(sys.modules, 'open_webui.utils.chat', chat)

    payload = ModuleType('open_webui.utils.payload')
    payload_namespace = {}
    load_functions(payload_namespace, 'utils/payload.py', {'apply_params_to_form_data'})
    payload.apply_params_to_form_data = payload_namespace['apply_params_to_form_data']
    payload.resolve_system_prompt = AsyncMock(return_value='')
    monkeypatch.setitem(sys.modules, 'open_webui.utils.payload', payload)
    models_module = ModuleType('open_webui.models.models')
    private_lookup = AsyncMock(side_effect=AssertionError('Direct descriptors must not resolve private storage'))
    models_module.Models = SimpleNamespace(get_model_by_id=private_lookup)
    monkeypatch.setitem(sys.modules, 'open_webui.models.models', models_module)

    descriptor = {
        'id': model_id,
        'owned_by': 'openai',
        'direct': True,
        'urlIdx': 0,
        'info': {'base_model_id': 'private-base', 'params': {}, 'meta': {'context_window': 100000}},
    }
    if raw_id:
        descriptor['openai'] = {'id': raw_id}
    private_models = {'private-base': {'info': {'params': {'system': 'PRIVATE', 'max_completion_tokens': 90000}}}}
    models = {**private_models, model_id: descriptor}
    req = SimpleNamespace(
        state=SimpleNamespace(
            direct=True,
            model=descriptor,
            metadata={'user_id': 'user', 'session_id': 'owned-socket', 'chat_id': 'chat'},
        ),
        app=SimpleNamespace(state=SimpleNamespace(MODELS=private_models)),
    )
    await v2.generate_summary(
        req,
        SimpleNamespace(role='admin'),
        model_id,
        models,
        [{'role': 'user', 'content': 'conversation facts ' * 100}],
        '',
        v2.CompactionSettings(),
    )
    assert len(captured) == 1
    body = captured[0]
    assert body[expected_key] == 5000
    conflicting_key = 'max_tokens' if expected_key == 'max_completion_tokens' else 'max_completion_tokens'
    assert conflicting_key not in body
    assert body['model'] == model_id  # Browser removes its own connection prefix.
    assert 'PRIVATE' not in str(body)
    assert req.state.model is descriptor
    assert req.state.metadata == {'user_id': 'user', 'session_id': 'owned-socket', 'chat_id': 'chat'}
    private_lookup.assert_not_awaited()
