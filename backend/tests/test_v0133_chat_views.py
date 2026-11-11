import pytest
from pydantic import ValidationError

from open_webui.models.chat_views import ChatViewStateForm


def layout(chat_id='side-chat', **fields):
    return {
        'state': {
            'schemaVersion': 1,
            'selectedTerminalId': None,
            'panels': {
                'tabs': [{'id': 'tab', 'kind': 'side', 'label': 'Side chat', 'chatId': chat_id, **fields}],
                'activeId': 'tab',
                'history': ['tab'],
                'groups': [{'id': 'group', 'tabIds': ['tab'], 'activeId': 'tab'}],
                'activeGroupId': 'group',
                'layout': {'type': 'group', 'groupId': 'group'},
            },
        }
    }


def test_view_accepts_saved_side_chat_reference():
    view = ChatViewStateForm.model_validate(layout())
    assert view.state.panels.tabs[0].chatId == 'side-chat'


@pytest.mark.parametrize('chat_id', ['temporary:socket:scope', 'local:socket', 'channel:shared', '', None])
def test_view_rejects_non_saved_side_chat_reference(chat_id):
    with pytest.raises(ValidationError, match='saved chat'):
        ChatViewStateForm.model_validate(layout(chat_id))


@pytest.mark.parametrize('field', ['history', 'temporary_context', 'previewTicket', 'url'])
def test_view_rejects_embedded_history_and_capabilities(field):
    with pytest.raises(ValidationError, match='Extra inputs are not permitted'):
        ChatViewStateForm.model_validate(layout(**{field: 'synthetic'}))
