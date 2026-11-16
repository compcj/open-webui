import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

from test_web_tool_citations import load_functions

HELPER_PATH = Path(__file__).resolve().parents[1] / 'open_webui' / 'utils' / 'responses_reasoning.py'


def load_remap():
    spec = importlib.util.spec_from_file_location('responses_reasoning', HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.remap_reasoning_effort_for_responses


remap_reasoning_effort_for_responses = load_remap()


@pytest.mark.parametrize(
    'limits,expected',
    [
        ({'max_output_tokens': 32000}, 32000),
        ({'max_output_tokens': 32000, 'max_tokens': 16000}, 16000),
        ({'max_output_tokens': 32000, 'max_completion_tokens': 24000}, 24000),
        ({'max_output_tokens': 32000, 'max_tokens': 16000, 'max_completion_tokens': 24000}, 24000),
    ],
)
def test_v2_output_reserve_matches_responses_wire_limit(limits, expected):
    from open_webui.utils import context_compaction_v2 as v2

    namespace = {'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses}
    load_functions(namespace, 'routers/openai.py', {'convert_to_responses_payload'})
    payload = {'model': 'synthetic', 'messages': [{'role': 'user', 'content': 'Continue'}], **limits}
    models = {'synthetic': {'info': {'meta': {'context_window': 100000}}}}
    budget = v2.resolve_budget(v2.CompactionSettings(), 'synthetic', models, payload)
    wire = namespace['convert_to_responses_payload'](deepcopy(payload))

    assert wire['max_output_tokens'] == expected
    assert budget.output_reserve == wire['max_output_tokens']
    assert budget.input_budget == 95000 - expected
    assert budget.threshold == min(90000, budget.input_budget)
    assert 'max_tokens' not in wire and 'max_completion_tokens' not in wire


def test_remaps_reasoning_effort_to_nested_reasoning():
    payload = {
        'model': 'openai/gpt-5.6-sol',
        'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Hello'}]}],
        'reasoning_effort': 'medium',
    }

    result = remap_reasoning_effort_for_responses(payload)

    assert 'reasoning_effort' not in result
    assert result['reasoning'] == {'effort': 'medium'}


def test_merges_reasoning_effort_into_existing_reasoning():
    payload = {
        'model': 'openai/gpt-5.6-sol',
        'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Hello'}]}],
        'reasoning_effort': 'high',
        'reasoning': {'mode': 'pro'},
    }

    result = remap_reasoning_effort_for_responses(payload)

    assert 'reasoning_effort' not in result
    assert result['reasoning'] == {'mode': 'pro', 'effort': 'high'}


def test_strips_null_reasoning_effort_without_adding_reasoning():
    payload = {
        'model': 'openai/gpt-5.6-sol',
        'input': [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Hello'}]}],
        'reasoning_effort': None,
    }

    result = remap_reasoning_effort_for_responses(payload)

    assert 'reasoning_effort' not in result
    assert 'reasoning' not in result


def test_leaves_payload_unchanged_without_reasoning_effort():
    payload = {
        'model': 'openai/gpt-5.6-sol',
        'input': [
            {
                'type': 'message',
                'role': 'user',
                'content': [{'type': 'input_text', 'text': 'Hello'}],
            }
        ],
    }

    result = remap_reasoning_effort_for_responses(payload)

    assert 'reasoning_effort' not in result
    assert 'reasoning' not in result
    assert result['input'] == payload['input']
    assert result['model'] == 'openai/gpt-5.6-sol'


@pytest.mark.parametrize('leading_system', [False, True])
@pytest.mark.parametrize('content_parts', [False, True])
def test_trailing_budget_reminder_preserves_responses_instructions_and_input_prefix(leading_system, content_parts):
    namespace = {'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses}
    load_functions(namespace, 'routers/openai.py', {'convert_to_responses_payload'})
    convert = namespace['convert_to_responses_payload']
    messages = [
        {'role': 'user', 'content': 'Continue the task'},
        {
            'role': 'assistant',
            'tool_calls': [{'id': 'call_1', 'function': {'name': 'search', 'arguments': '{}'}}],
        },
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'Search results'},
    ]
    if leading_system:
        messages.insert(0, {'role': 'system', 'content': 'Stable application instructions'})
    payload = {'model': 'synthetic', 'messages': messages}
    baseline = convert(deepcopy(payload))
    for remaining in (1000, 800):
        text = f'Context budget reminder: {remaining} tokens remain.'
        content = [{'type': 'text', 'text': text}] if content_parts else text
        result = convert(
            {**deepcopy(payload), 'messages': [*deepcopy(messages), {'role': 'system', 'content': content}]}
        )
        assert result.get('instructions') == baseline.get('instructions')
        assert result['input'][:-1] == baseline['input']
        assert result['input'][-1] == {
            'type': 'message',
            'role': 'system',
            'content': [{'type': 'input_text', 'text': text}],
        }


def test_responses_replays_provider_batch_with_frozen_child_citation():
    from open_webui.utils.json_codec import JSONCodec
    from open_webui.utils.misc import merge_tool_call_batches

    namespace = {
        'JSONCodec': JSONCodec,
        'merge_tool_call_batches': merge_tool_call_batches,
        'remap_reasoning_effort_for_responses': remap_reasoning_effort_for_responses,
        'RESPONSES_ALLOWED_FIELDS': {
            'function_call': {'type', 'id', 'call_id', 'name', 'arguments'},
            'function_call_output': {'type', 'call_id', 'output'},
        },
    }
    load_functions(namespace, 'routers/openai.py', {'convert_to_responses_payload', '_normalize_stored_item'})
    stored = [
        {
            'type': 'function_call',
            'id': 'original-id',
            'call_id': 'child',
            'name': 'lookup',
            'arguments': '{}',
            'provider_batch': {
                'call_id': 'original-call',
                'size': 1,
                'index': 0,
                'tool_name': 'lookup',
                'function': {'name': 'call_tool', 'arguments': '{"calls":[{"name":"lookup"}]}'},
            },
        },
        {
            'type': 'function_call_output',
            'call_id': 'child',
            'status': 'completed',
            'output': [{'type': 'input_text', 'text': 'raw result'}],
            'citation_context': 'frozen citation [1]',
        },
    ]
    result = namespace['convert_to_responses_payload']({'messages': [{'role': 'assistant', 'output': stored}]})
    call, output = result['input']
    assert call['name'] == 'call_tool'
    assert call['arguments'] == stored[0]['provider_batch']['function']['arguments']
    assert call['call_id'] == output['call_id'] == 'original-call'
    assert 'raw result' in output['output'][0]['text']
    assert 'frozen citation [1]' in output['output'][0]['text']
