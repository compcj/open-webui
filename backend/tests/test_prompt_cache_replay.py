"""Cache metadata and reusable content survive the real provider boundaries."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_native_connections_integration import CapturedSend, sender
from test_web_tool_citations import load_functions
from open_webui.utils.responses_reasoning import remap_reasoning_effort_for_responses


@pytest.fixture
def content_runtime():
    namespace = {'JSONCodec': json}
    load_functions(
        namespace,
        'utils/misc.py',
        {
            'get_content_from_message',
            'get_output_text',
            'merge_system_messages',
            'strip_empty_content_blocks',
            'update_message_content',
        },
    )
    load_functions(namespace, 'utils/middleware.py', {'normalize_messages_for_model'})
    return namespace


@pytest.mark.parametrize('marker', ['cache_control', 'prompt_cache_breakpoint'])
def test_system_normalization_keeps_all_blocks_and_cache_boundaries(content_runtime, marker):
    value = {'type': 'ephemeral'} if marker == 'cache_control' else {'mode': 'explicit'}
    blocks = [
        {'type': 'text', 'text': 'Stable instructions'},
        {'type': 'text', 'text': 'Long reusable reference body', marker: value},
        {'type': 'text', 'text': 'Additional instructions'},
    ]
    form = {'messages': [{'role': 'system', 'content': copy.deepcopy(blocks)}, {'role': 'user', 'content': 'Question'}]}
    result = content_runtime['normalize_messages_for_model'](form)
    assert result['messages'][0]['content'] == blocks
    assert content_runtime['normalize_messages_for_model'](copy.deepcopy(result)) == result


def test_multiple_system_messages_keep_block_metadata_and_stable_order(content_runtime):
    cached = {'type': 'text', 'text': 'Cached reference', 'cache_control': {'type': 'ephemeral'}}
    messages = [
        {'role': 'system', 'content': 'Instructions'},
        {'role': 'system', 'content': [copy.deepcopy(cached)]},
        {'role': 'user', 'content': 'Question'},
    ]
    original = copy.deepcopy(messages)
    merged = content_runtime['merge_system_messages'](messages)
    assert [message['role'] for message in merged] == ['system', 'user']
    assert isinstance(merged[0]['content'], list)
    assert cached in merged[0]['content']
    text = ''.join(part.get('text', '') for part in merged[0]['content'])
    assert text == 'Instructions\nCached reference'
    assert messages == original
    assert content_runtime['merge_system_messages'](copy.deepcopy(merged)) == merged


@pytest.mark.parametrize('marked', [False, True])
def test_system_block_messages_keep_separators_without_changing_marked_block(content_runtime, marked):
    second = {'type': 'text', 'text': 'Second'}
    if marked:
        second['cache_control'] = {'type': 'ephemeral'}
    merged = content_runtime['merge_system_messages'](
        [
            {'role': 'system', 'content': [{'type': 'text', 'text': 'First'}]},
            {'role': 'system', 'content': [copy.deepcopy(second)]},
        ]
    )
    assert ''.join(part['text'] for part in merged[0]['content']) == 'First\nSecond'
    assert second in merged[0]['content']
    assert content_runtime['normalize_messages_for_model']({'messages': copy.deepcopy(merged)})['messages'] == merged


def test_mixed_system_strings_have_one_separator(content_runtime):
    merged = content_runtime['merge_system_messages'](
        [
            {'role': 'system', 'content': 'First'},
            {'role': 'system', 'content': 'Second'},
            {'role': 'system', 'content': [{'type': 'text', 'text': 'Third'}]},
        ]
    )
    assert ''.join(part['text'] for part in merged[0]['content']) == 'First\nSecond\nThird'


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_router_extra_body_is_opt_in_expands_before_budget_and_keeps_authoritative_fields(sender, enabled):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    row = ns['Models'].get_model_by_id.return_value
    row.meta.extra_body_compatibility = enabled
    form = {
        'model': 'connection.native-test',
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'prompt_cache_key': 'top-key',
        'metadata': {'chat_id': 'trusted'},
        'extra_body': {
            'prompt_cache_key': 'nested-key',
            'session_id': '00123',
            'metadata': {'chat_id': 'untrusted'},
            '_native_transport': {'api_type': 'anthropic'},
        },
    }
    original = copy.deepcopy(form)
    with pytest.raises(CapturedSend):
        await ns['generate_chat_completion'](request, form, SimpleNamespace(role='admin'))
    wire = json.loads(captured['send']['data'])
    assert {key: value for key, value in form.items() if key != 'metadata'} == {
        key: value for key, value in original.items() if key != 'metadata'
    }
    assert form['metadata']['chat_id'] == 'trusted'
    assert wire['prompt_cache_key'] == 'top-key'
    assert 'metadata' not in wire
    assert '_native_transport' not in wire
    if enabled:
        assert 'extra_body' not in wire
        assert captured['budget']['session_id'] == wire['session_id'] == '00123'
    else:
        assert wire['extra_body'] == original['extra_body']


@pytest.mark.asyncio
async def test_router_invalid_expansion_is_400_without_transport(sender):
    from fastapi import HTTPException

    request, ns, captured = sender
    ns['Models'].get_model_by_id.return_value.meta.extra_body_compatibility = True
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    with pytest.raises(HTTPException) as error:
        await ns['generate_chat_completion'](
            request,
            {'model': 'connection.native-test', 'messages': [], 'extra_body': 'bad'},
            SimpleNamespace(role='admin'),
        )
    assert error.value.status_code == 400
    assert 'budget' not in captured and 'send' not in captured


@pytest.mark.asyncio
async def test_router_disabled_reasoning_replay_removes_only_content_field(sender):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    ns['Models'].get_model_by_id.return_value.meta.reasoning_content_replay = False
    message = {
        'role': 'assistant',
        'content': 'Answer',
        'reasoning_content': 'raw',
        'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}],
    }
    form = {'model': 'connection.native-test', 'messages': [copy.deepcopy(message)]}
    with pytest.raises(CapturedSend):
        await ns['generate_chat_completion'](request, form, SimpleNamespace(role='admin'))
    sent = json.loads(captured['send']['data'])['messages'][0]
    assert 'reasoning_content' not in sent
    assert sent['reasoning_details'] == message['reasoning_details']
    assert form['messages'][0] == message


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_router_retains_explicit_model_upstream_metadata(sender, enabled):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    row = ns['Models'].get_model_by_id.return_value
    row.meta.extra_body_compatibility = enabled
    row.params = SimpleNamespace(model_dump=lambda: {'metadata': {'tag': 'upstream'}})
    ns['apply_model_params_to_body_openai'] = lambda params, body: {**params, **body}
    ns['apply_system_prompt_to_body'] = AsyncMock(side_effect=lambda system, body, metadata, user: body)
    form = {
        'model': 'connection.native-test',
        'messages': [{'role': 'user', 'content': 'Hi'}],
        'metadata': {'chat_id': 'internal'},
        'extra_body': {'metadata': {'tag': 'nested'}},
    }
    with pytest.raises(CapturedSend):
        await ns['generate_chat_completion'](request, form, SimpleNamespace(role='admin'))
    assert json.loads(captured['send']['data'])['metadata'] == {'tag': 'upstream'}


@pytest.mark.parametrize('append', [False, True])
def test_prompt_injection_is_once_outside_existing_cache_blocks(content_runtime, append):
    blocks = [
        {'type': 'text', 'text': 'First block'},
        {'type': 'text', 'text': 'Second block', 'cache_control': {'type': 'ephemeral'}},
    ]
    message = {'role': 'system', 'content': copy.deepcopy(blocks)}
    result = content_runtime['update_message_content'](message, 'Injected instructions', append=append)
    assert all(block in result['content'] for block in blocks)
    text = ''.join(part.get('text', '') for part in result['content'])
    assert text.count('Injected instructions') == 1
    assert text.endswith('\nInjected instructions') if append else text.startswith('Injected instructions\n')


@pytest.mark.asyncio
async def test_image_conversion_keeps_cache_metadata_and_original_input():
    namespace = {'get_image_base64_from_url': AsyncMock(return_value='data:image/png;base64,c3ludGhldGlj')}
    load_functions(namespace, 'utils/middleware.py', {'convert_url_images_to_base64'})
    block = {
        'type': 'image_url',
        'image_url': {'url': 'https://example.test/image.png', 'detail': 'low'},
        'cache_control': {'type': 'ephemeral'},
    }
    form = {'messages': [{'role': 'user', 'content': [copy.deepcopy(block)]}]}
    result = await namespace['convert_url_images_to_base64'](form)
    converted = result['messages'][0]['content'][0]
    assert converted['cache_control'] == block['cache_control']
    assert converted['image_url'] == {'url': 'data:image/png;base64,c3ludGhldGlj', 'detail': 'low'}


@pytest.mark.asyncio
@pytest.mark.parametrize('marker', ['cache_control', 'prompt_cache_breakpoint'])
async def test_tool_text_blocks_keep_each_cache_boundary_on_the_wire(sender, marker):
    request, namespace, captured = sender
    namespace['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    value = {'type': 'ephemeral'} if marker == 'cache_control' else {'mode': 'explicit'}
    blocks = [
        {'type': 'text', 'text': 'Reusable output', marker: value},
        {'type': 'text', 'text': 'Variable suffix'},
    ]
    form = {
        'model': 'connection.native-test',
        'messages': [{'role': 'tool', 'tool_call_id': 'call', 'content': blocks}],
    }
    with pytest.raises(CapturedSend):
        await namespace['generate_chat_completion'](request, form, SimpleNamespace(role='admin'))
    assert json.loads(captured['send']['data'])['messages'][0]['content'] == blocks


@pytest.mark.parametrize('role', ['system', 'user', 'assistant'])
def test_responses_conversion_keeps_explicit_text_breakpoints(role):
    namespace = {'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses}
    load_functions(namespace, 'routers/openai.py', {'convert_to_responses_payload'})
    marker = {'mode': 'explicit'}
    form = {
        'model': 'synthetic',
        'prompt_cache_options': {'mode': 'explicit'},
        'messages': [
            {'role': role, 'content': [{'type': 'text', 'text': 'Reusable input', 'prompt_cache_breakpoint': marker}]}
        ],
    }
    sent = namespace['convert_to_responses_payload'](form)
    assert sent['prompt_cache_options'] == {'mode': 'explicit'}
    assert sent['input'][0]['role'] == role
    assert sent['input'][0]['content'][0]['prompt_cache_breakpoint'] == marker
    assert sent['input'][0]['content'][0]['text'] == 'Reusable input'


def test_responses_assistant_call_keeps_cache_block_before_call():
    namespace = {'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses}
    load_functions(namespace, 'routers/openai.py', {'convert_to_responses_payload'})
    marker = {'mode': 'explicit'}
    form = {
        'messages': [
            {
                'role': 'assistant',
                'content': [{'type': 'text', 'text': 'Calling a tool', 'prompt_cache_breakpoint': marker}],
                'tool_calls': [{'id': 'call', 'function': {'name': 'read', 'arguments': '{}'}}],
            }
        ]
    }
    sent = namespace['convert_to_responses_payload'](form)
    assert sent['input'][0]['content'][0]['prompt_cache_breakpoint'] == marker
    assert sent['input'][1]['type'] == 'function_call'
