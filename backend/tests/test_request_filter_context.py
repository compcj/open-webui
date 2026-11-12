import asyncio
import importlib
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_chat_system_prompt import context, runtime


def filter_context_helpers():
    return importlib.import_module('open_webui.utils.request_filter_context')


class RuntimeHandle:
    def __deepcopy__(self, memo):
        raise AssertionError('Runtime metadata must not be deep-copied.')


def test_snapshot_omits_runtime_metadata_and_detaches_nested_body():
    helpers = filter_context_helpers()
    payload = {
        'model': 'synthetic-model',
        'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Raw input'}]}],
        'tools': [{'type': 'function', 'function': {'name': 'read'}}],
        'metadata': {'runtime': RuntimeHandle()},
    }

    baseline = helpers.snapshot_request_filter_input(payload)
    payload['messages'][0]['content'][0]['text'] = 'Changed'
    payload['tools'][0]['function']['name'] = 'changed'

    assert 'metadata' not in baseline
    assert baseline['messages'][0]['content'][0]['text'] == 'Raw input'
    assert baseline['tools'][0]['function']['name'] == 'read'


def test_each_filter_input_detaches_canonical_history_and_shallow_copies_metadata():
    helpers = filter_context_helpers()
    baseline = {'model': 'synthetic-model', 'messages': [{'role': 'system', 'content': 'Policy'}]}
    messages = [*deepcopy(baseline['messages']), {'role': 'tool', 'tool_call_id': 'call', 'content': 'Raw result'}]
    metadata = {'runtime': RuntimeHandle(), 'chat_id': 'chat'}
    original = deepcopy(messages)

    first = helpers.copy_request_filter_input(baseline, messages=messages, metadata=metadata)
    first['messages'][0]['content'] += ' Filter injection'
    first['messages'][1]['content'] = 'Filtered result'
    first['model'] = 'routed-model'
    first['metadata']['chat_id'] = 'filtered-chat'
    second = helpers.copy_request_filter_input(baseline, messages=messages, metadata=metadata)

    assert first['metadata'] is not metadata
    assert first['metadata']['runtime'] is metadata['runtime']
    assert metadata['chat_id'] == 'chat'
    assert second['model'] == 'synthetic-model'
    assert second['messages'] == original == messages
    assert baseline['messages'] == [{'role': 'system', 'content': 'Policy'}]


def test_copy_retains_body_defaults_and_accepts_an_explicit_empty_message_list():
    helpers = filter_context_helpers()
    baseline = {
        'model': 'synthetic-model',
        'messages': [{'role': 'user', 'content': 'Raw input'}],
        'stream': True,
        'extra_body': {'provider_options': {'enabled': True}},
    }

    normal = helpers.copy_request_filter_input(baseline)
    empty = helpers.copy_request_filter_input(baseline, messages=[], metadata={})
    normal['extra_body']['provider_options']['enabled'] = False

    assert normal['messages'] == baseline['messages']
    assert normal['stream'] is True
    assert 'metadata' not in normal
    assert empty['messages'] == []
    assert empty['metadata'] == {}
    assert baseline['extra_body']['provider_options']['enabled'] is True


@pytest.mark.asyncio
async def test_fresh_filtered_bodies_reuse_one_frozen_dynamic_prompt(runtime):
    request, raw, user = context(runtime, system='Model {{CURRENT_TIME}}')
    original = deepcopy(raw)
    runtime['resolve_system_prompt'].side_effect = ['First resolved timestamp', 'Later resolved timestamp']

    sent = []
    for index in range(2):
        filtered = deepcopy(raw)
        filtered['messages'][0]['content'] = f'Intentional filter rewrite {index}'
        before = deepcopy(filtered)
        sent.append(await runtime['prepare_chat_system_prompt'](request, filtered, user))
        assert filtered == before

    assert [body['messages'][0]['content'] for body in sent] == [
        'First resolved timestamp\nIntentional filter rewrite 0',
        'First resolved timestamp\nIntentional filter rewrite 1',
    ]
    sent[0]['messages'][1]['content'] = 'Transport mutation'
    assert sent[1]['messages'][1]['content'] == original['messages'][1]['content']
    assert raw == original
    runtime['resolve_system_prompt'].assert_awaited_once()
    runtime['Models'].get_model_by_id.assert_awaited_once_with('test')


@pytest.mark.asyncio
async def test_frozen_prompt_cache_is_keyed_by_each_filtered_model(runtime):
    request, raw, user = context(runtime)
    templates = {'first': 'First model prompt', 'second': 'Second model prompt'}

    async def lookup(model_id):
        return SimpleNamespace(params=SimpleNamespace(model_dump=lambda: {'system': templates[model_id]}))

    runtime['Models'].get_model_by_id = AsyncMock(side_effect=lookup)
    first = await runtime['prepare_chat_system_prompt'](request, {**raw, 'model': 'first'}, user)
    templates['first'] = 'Changed after first send'
    second = await runtime['prepare_chat_system_prompt'](request, {**raw, 'model': 'second'}, user)
    first_again = await runtime['prepare_chat_system_prompt'](request, {**raw, 'model': 'first'}, user)

    assert first['messages'][0]['content'] == first_again['messages'][0]['content']
    assert first_again['messages'][0]['content'] == 'First model prompt\nUser instructions'
    assert second['messages'][0]['content'] == 'Second model prompt\nUser instructions'
    assert [call.args[0] for call in runtime['Models'].get_model_by_id.await_args_list] == ['first', 'second']
    assert runtime['resolve_system_prompt'].await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', [False, True])
async def test_absent_model_prompts_remain_frozen_for_the_request(runtime, missing):
    request, raw, user = context(runtime, system='')
    if missing:
        runtime['Models'].get_model_by_id.return_value = None
    first = await runtime['prepare_chat_system_prompt'](request, raw, user)
    runtime['Models'].get_model_by_id.return_value = SimpleNamespace(
        params=SimpleNamespace(model_dump=lambda: {'system': 'Newly configured prompt'})
    )
    second = await runtime['prepare_chat_system_prompt'](request, raw, user)

    assert first == second == raw
    runtime['Models'].get_model_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_model_bypass_survives_cached_server_routing(runtime):
    request, raw, user = context(runtime, kind='direct')
    original = deepcopy(raw)

    server = await runtime['prepare_chat_system_prompt'](request, {**raw, 'model': 'server-model'}, user)
    direct = await runtime['prepare_chat_system_prompt'](request, raw, user)
    direct_again = await runtime['prepare_chat_system_prompt'](request, raw, user)

    assert server['messages'][0]['content'] == 'Model instructions\nUser instructions'
    assert direct == direct_again == raw == original
    runtime['Models'].get_model_by_id.assert_awaited_once_with('server-model')
    runtime['resolve_system_prompt'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('inherited_cache', [False, True])
async def test_parallel_replies_do_not_share_frozen_model_prompts(runtime, inherited_cache):
    first_request, raw, user = context(runtime, system='Template')
    second_request, _, _ = context(runtime, system='Template')
    first_request.state.metadata = {'reply': 'first'}
    second_request.state.metadata = {'reply': 'second'}
    if inherited_cache:
        shared = {}
        first_request.state._chat_system_prompt_cache = shared
        second_request.state._chat_system_prompt_cache = shared
    runtime['resolve_system_prompt'].side_effect = lambda system, metadata, user: metadata['reply']

    first, second = await asyncio.gather(
        runtime['prepare_chat_system_prompt'](first_request, raw, user),
        runtime['prepare_chat_system_prompt'](second_request, raw, user),
    )
    first_again = await runtime['prepare_chat_system_prompt'](first_request, raw, user)

    assert first['messages'][0]['content'] == first_again['messages'][0]['content'] == 'first\nUser instructions'
    assert second['messages'][0]['content'] == 'second\nUser instructions'
    assert runtime['resolve_system_prompt'].await_count == 2
    if inherited_cache:
        assert shared == {}


@pytest.mark.asyncio
async def test_cached_prompt_does_not_skip_model_access_on_later_sends(runtime):
    request, raw, user = context(runtime)
    user.role = 'user'

    previous_checks = 0
    for _ in range(2):
        prepared = await runtime['prepare_chat_system_prompt'](request, raw, user)
        await runtime['generate_chat_completion'](request, prepared, user, bypass_system_prompt=True)
        assert runtime['check_model_access'].await_count > previous_checks
        previous_checks = runtime['check_model_access'].await_count
