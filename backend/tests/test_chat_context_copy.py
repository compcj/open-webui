from open_webui.utils.chat_context_copy import copy_chat_without_context_state


def test_copy_keeps_raw_outputs_and_frozen_citations_without_reusable_state():
    source = {
        'history': {
            'messages': {
                'a': {
                    'id': 'a',
                    'role': 'assistant',
                    'content': 'raw reply',
                    'contextSummary': 'private summary',
                    'meta': {'tool_budget_accounting': {'model': {'selection_id': 'old'}}, 'other': 'retained'},
                    'output': [
                        {
                            'id': 'out',
                            'type': 'function_call_output',
                            'output': [{'type': 'input_text', 'text': 'raw evidence'}],
                            'tool_result': {'kind': 'native', 'batch_id': 'old'},
                            'citation_context': 'frozen',
                            'citation_sources': [{'id': 'source'}],
                        }
                    ],
                }
            }
        },
        'context_usage': {'checkpoint_id': 'old'},
        'share_mode': 'continue',
    }
    copied = copy_chat_without_context_state(source)
    message = copied['history']['messages']['a']
    assert 'contextSummary' not in message and 'tool_budget_accounting' not in message['meta']
    assert message['meta']['other'] == 'retained'
    assert message['output'][0]['output'] == source['history']['messages']['a']['output'][0]['output']
    assert message['output'][0]['citation_context'] == 'frozen' and 'tool_result' not in message['output'][0]
    assert 'context_usage' not in copied and 'share_mode' not in copied
    assert 'contextSummary' in source['history']['messages']['a']
