import importlib.util
from pathlib import Path

HELPER_PATH = Path(__file__).resolve().parents[1] / 'open_webui' / 'utils' / 'responses_reasoning.py'


def load_remap():
    spec = importlib.util.spec_from_file_location('responses_reasoning', HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.remap_reasoning_effort_for_responses


remap_reasoning_effort_for_responses = load_remap()


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
