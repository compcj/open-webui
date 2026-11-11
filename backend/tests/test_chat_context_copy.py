from copy import deepcopy

from open_webui.models.chat_messages import ChatMessages
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
                    'meta': {
                        'agent': 'signed-agent-session',
                        'agent_execution': {'task_id': 'remote-task'},
                        'tool_budget_accounting': {'model': {'selection_id': 'old'}},
                        'other': 'retained',
                    },
                    'output': [
                        {
                            'id': 'out',
                            'type': 'function_call_output',
                            'output': [{'type': 'input_text', 'text': 'raw evidence'}],
                            'tool_result': {'kind': 'native', 'batch_id': 'old'},
                            'agent_resolution': {'action': 'approve'},
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
    assert 'agent' not in message['meta'] and 'agent_execution' not in message['meta']
    assert 'agent_resolution' not in message['output'][0]
    assert message['meta']['other'] == 'retained'
    assert message['output'][0]['output'] == source['history']['messages']['a']['output'][0]['output']
    assert message['output'][0]['citation_context'] == 'frozen' and 'tool_result' not in message['output'][0]
    assert 'context_usage' not in copied and 'share_mode' not in copied
    assert 'contextSummary' in source['history']['messages']['a']
    assert source['history']['messages']['a']['meta']['agent'] == 'signed-agent-session'


def test_normalized_chat_message_keeps_unpaired_tool_status_and_raw_output():
    output = [
        {'type': 'function_call', 'call_id': 'same', 'status': 'completed', 'reasoning_details': {'signature': 'sig'}},
        {
            'type': 'function_call_output',
            'id': 'result',
            'call_id': 'another',
            'output': 'raw\r\n',
            'citation_context': 'frozen',
            'tool_result': {'batch_id': 'server-registered'},
        },
    ]
    message = ChatMessages._build_message('chat-assistant', 'chat', 'owner', {'output': output}, 1)
    assert message.output == output
    ChatMessages._apply_message_data(message, {'output': deepcopy(output)}, 2)
    assert message.output == output
