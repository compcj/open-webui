"""Exercise production ordering of terminal AGENTS and per-turn attachment injection."""

import ast
import copy
import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import load_functions


@pytest.fixture
def runtime(monkeypatch):
    ns = {'log': logging.getLogger(__name__), 'is_saved_chat_id': lambda chat_id: chat_id == 'saved-chat'}
    load_functions(
        ns,
        'utils/misc.py',
        {'get_message_list', 'get_content_from_message', 'strip_empty_content_blocks', 'merge_system_messages'},
    )
    load_functions(ns, 'utils/terminals.py', {'add_terminal_agents_md'})
    tree = load_functions(ns, 'utils/middleware.py', {'add_file_context', 'normalize_messages_for_model'})
    agents_loader = AsyncMock(return_value='# AGENTS.md\n\nTerminal instructions')
    terminal = ModuleType('open_webui.utils.terminals')
    terminal.add_terminal_agents_md = ns['add_terminal_agents_md']
    terminal.get_terminal_agents_md = agents_loader
    monkeypatch.setitem(sys.modules, terminal.__name__, terminal)

    def calls(node, name):
        return any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == name
            for child in ast.walk(node)
        )

    payload = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    resolution = next(
        node for node in payload.body if isinstance(node, ast.If) and ast.unparse(node.test) == 'payload_tools is None'
    )
    steps = []
    for node in resolution.body:
        if isinstance(node, ast.If) and calls(node, 'add_terminal_agents_md'):
            steps.append(copy.deepcopy(node))
        elif isinstance(node, ast.If) and calls(node, 'add_file_context'):
            # Execute the actual branch's setup through attachment enrichment; unrelated
            # builtin selection is exercised separately in test_chat_tool_features.
            last = next(index for index, statement in enumerate(node.body) if calls(statement, 'add_file_context'))
            step = copy.deepcopy(node)
            step.body = step.body[: last + 1]
            steps.append(step)
    assert len(steps) == 2
    wrapper = ast.parse('async def prepare_context():\n    pass')
    wrapper.body[0].body = steps
    exec(compile(ast.fix_missing_locations(wrapper), 'production_terminal_attachment_order', 'exec'), ns)
    return SimpleNamespace(ns=ns, agents_loader=agents_loader)


def text(message):
    content = message['content']
    return content if isinstance(content, str) else '\n'.join(part.get('text', '') for part in content)


@pytest.mark.asyncio
@pytest.mark.parametrize('saved', [True, False])
@pytest.mark.parametrize('image_context', [False, True])
async def test_terminal_agents_preserve_attachment_pairing_and_image_context(runtime, saved, image_context):
    ns = runtime.ns
    first = {'role': 'user', 'content': 'first-user'}
    second = {'role': 'user', 'content': [{'type': 'text', 'text': 'second-user'}]}
    first_file = {'type': 'file', 'id': 'file-a', 'url': '/api/v1/files/file-a/content'}
    second_file = {'type': 'image', 'id': 'file-b', 'url': '/api/v1/files/file-b/content'}
    history = {
        'first': {**first, 'files': [first_file], 'parentId': None},
        'second': {**second, 'files': [second_file], 'parentId': 'first'},
    }
    messages = [{'role': 'system', 'content': 'System'}, copy.deepcopy(first)]
    if image_context:
        messages.append(
            {
                'role': 'user',
                'content': [{'type': 'image_url', 'image_url': {'url': '/api/v1/files/tool-image/content'}}],
                '_tool_image_context': True,
            }
        )
    messages.append(copy.deepcopy(second))
    if not saved:
        # Temporary request attachments have no stored chat row. Preserve their
        # explicit context and image payload exactly as sent by earlier processing.
        messages[1]['content'] = '<attached_files>file-a</attached_files>\nfirst-user'
        messages[-1]['content'].insert(0, {'type': 'text', 'text': '<attached_files>file-b</attached_files>'})
    lookup = AsyncMock(return_value=SimpleNamespace(chat={'history': {'messages': history, 'currentId': 'second'}}))
    ns.update(
        Chats=SimpleNamespace(get_chat_by_id_and_user_id=lookup),
        form_data={'messages': messages},
        metadata={'chat_id': 'saved-chat' if saved else 'local:temporary'},
        user=SimpleNamespace(id='owner'),
        terminal_id='t',
        terminal_capability=True,
        use_builtin_tools=True,
        request=object(),
        extra_params={},
    )
    await ns['prepare_context']()
    enriched = ns['form_data']['messages']
    agents = next(message for message in enriched if '# AGENTS.md' in text(message))
    first_request = next(message for message in enriched if 'first-user' in text(message))
    second_request = next(message for message in enriched if 'second-user' in text(message))
    assert '<attached_files>' not in text(agents)
    assert 'file-a' in text(first_request) and 'file-b' not in text(first_request)
    assert 'file-b' in text(second_request) and 'file-a' not in text(second_request)
    assert enriched.index(agents) < enriched.index(first_request)
    if image_context:
        tool_image = next(message for message in enriched if message.get('_tool_image_context'))
        assert text(tool_image) == ''
        assert tool_image['content'] == [
            {'type': 'image_url', 'image_url': {'url': '/api/v1/files/tool-image/content'}}
        ]
    provider = ns['normalize_messages_for_model'](ns['form_data'])
    assert all(set(message) == {'role', 'content'} for message in provider['messages'])
    runtime.agents_loader.assert_awaited_once()
    if saved:
        lookup.assert_awaited_once_with('saved-chat', 'owner')
    else:
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_agents_still_load_each_turn_when_builtin_tools_are_disabled(runtime):
    ns = runtime.ns
    ns.update(
        Chats=SimpleNamespace(get_chat_by_id_and_user_id=AsyncMock()),
        metadata={'chat_id': 'local:temporary'},
        user=SimpleNamespace(id='owner'),
        terminal_id='t',
        terminal_capability=True,
        use_builtin_tools=False,
        request=object(),
        extra_params={},
    )
    for instructions in ['Turn one instructions', 'Turn two instructions']:
        ns['form_data'] = {'messages': [{'role': 'user', 'content': 'Actual request'}]}
        runtime.agents_loader.return_value = '# AGENTS.md\n\n' + instructions
        await ns['prepare_context']()
        assert ns['form_data']['messages'] == [
            {'role': 'user', 'content': '# AGENTS.md\n\n' + instructions},
            {'role': 'user', 'content': 'Actual request'},
        ]
    assert runtime.agents_loader.await_count == 2
    ns['Chats'].get_chat_by_id_and_user_id.assert_not_awaited()
