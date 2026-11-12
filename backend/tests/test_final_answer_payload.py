"""Final-answer requests keep the provider prefix while disabling execution."""

import copy

from open_webui.utils.tool_calling import FINAL_ANSWER_PROMPT, final_answer_payload


def test_final_answer_retains_tools_and_appends_only_a_user_message():
    payload = {
        'model': 'synthetic-model',
        'stream': True,
        'tool_choice': 'required',
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'test_tool',
                    'description': 'Synthetic tool',
                    'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}},
                },
            }
        ],
        'messages': [
            {'role': 'system', 'content': 'Frozen policy and citation rules'},
            {'role': 'user', 'content': 'Synthetic task'},
            {
                'role': 'assistant',
                'content': '',
                'tool_calls': [
                    {
                        'id': 'call-1',
                        'type': 'function',
                        'function': {'name': 'test_tool', 'arguments': '{}'},
                        'extra_content': {'google': {'thought_signature': 'opaque-synthetic-signature'}},
                    }
                ],
            },
            {'role': 'tool', 'tool_call_id': 'call-1', 'content': 'Frozen tool result and citation'},
        ],
    }
    original = copy.deepcopy(payload)

    result = final_answer_payload(payload)

    assert result['tools'] == original['tools']
    assert result['tool_choice'] == 'none'
    assert result['messages'] == [*original['messages'], {'role': 'user', 'content': FINAL_ANSWER_PROMPT}]
    assert result['model'] == original['model']
    assert result['stream'] is True
    assert result['messages'] is not payload['messages']
    assert payload == original


def test_final_answer_does_not_invent_a_tool_list_when_absent():
    result = final_answer_payload({'model': 'synthetic-model'})

    assert 'tools' not in result
    assert result['tool_choice'] == 'none'
    assert result['messages'] == [{'role': 'user', 'content': FINAL_ANSWER_PROMPT}]
