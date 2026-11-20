import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from open_webui.utils import browser


def chat_browser_registration():
    # Run the actual chat merge and registration, without importing the app or a database.
    source = Path(__file__).parents[1] / 'open_webui/utils/middleware.py'
    chat = next(
        node
        for node in ast.parse(source.read_text('utf-8')).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    direct_block = next(
        node
        for node in ast.walk(chat)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'direct_tool_servers'
    )
    registration = [
        node
        for node in ast.walk(chat)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'register_browser_open_tab'
    ]
    return compile(ast.Module(body=[direct_block, *registration], type_ignores=[]), str(source), 'exec')


REGISTRATION = chat_browser_registration()


def resolve_browser_tools(terminal_id='managed', direct=(), features=None, **overrides):
    tools = {
        name: {'type': 'terminal', 'tool_id': 'terminal:managed', 'spec': {'name': name}}
        for name in ('browser_tabs', 'browser_snapshot', 'browser_action', 'browser_screenshot')
        if terminal_id == 'managed'
    }
    config = {
        'features': {'terminal_browser_tab': True} if features is None else features,
        'terminal_id': terminal_id,
        'chat_id': 'chat-1',
        'session_id': 'socket-1',
        **overrides,
    }
    call = AsyncMock(return_value={'session_id': 'new-session', 'mode': 'chrome', 'token': 'private'})
    ns = dict(
        tools_dict=tools,
        direct_tool_servers=[
            {'is_terminal': True, 'url': url, 'specs': [{'name': 'browser_action'}, {'name': 'browser_tabs'}]}
            for url in direct
        ],
        terminal_connection_ids={'managed'},
        terminal_id=terminal_id,
        terminal_capability=True,
        metadata=config,
        event_caller=call,
        register_browser_open_tab=browser.register_browser_open_tab,
    )
    exec(REGISTRATION, ns)
    return tools, call


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', ['managed', 'https://personal.test'])
async def test_empty_workspace_can_open_a_tab_on_the_selected_terminal(selected):
    tools, call = resolve_browser_tools(selected, direct=['https://personal.test', 'https://other.test'])
    assert 'browser_open_tab' in tools
    action = tools['browser_action']
    assert (action.get('server') or {}).get('url', action.get('tool_id')) in {selected, f'terminal:{selected}'}
    opener = tools['browser_open_tab']
    assert opener['spec']['parameters']['properties'] == {}
    assert 'browser_open_tab' in tools['browser_tabs']['spec']['description']
    call.assert_not_awaited()
    assert await opener['callable']() == {'session_id': 'new-session', 'mode': 'chrome'}
    call.assert_awaited_once_with(
        {
            'type': 'request:open_browser_tab',
            'data': {'terminal_id': selected, 'session_id': 'socket-1'},
        }
    )


@pytest.mark.parametrize(
    'options',
    [
        {'features': {}},
        {'terminal_id': None},
        {'session_id': None},
        {'internal': True},
        {'automation_id': 'automation'},
        {'terminal_id': 'other', 'direct': ['https://personal.test']},
    ],
)
def test_browser_open_requires_an_interactive_client_and_its_selected_browser_tools(options):
    tools, call = resolve_browser_tools(**options)
    assert 'browser_open_tab' not in tools
    call.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_session_is_identified_without_claiming_it_can_be_controlled():
    tools, call = resolve_browser_tools()
    call.return_value = {'session_id': 'new-session', 'mode': 'proxy', 'token': 'private'}
    assert 'browser_open_tab' in tools
    result = await tools['browser_open_tab']['callable']()
    assert result['session_id'] == 'new-session' and result['mode'] == 'proxy'
    assert 'Chrome' in result['error'] and 'token' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [None, {'error': 'Client session disconnected'}, {'status': 'unconfirmed'}])
async def test_ambiguous_creation_is_never_reissued(result):
    tools, call = resolve_browser_tools()
    call.return_value = result
    with pytest.raises(RuntimeError, match='Check browser_tabs'):
        await tools['browser_open_tab']['callable']()
    call.assert_awaited_once()


def test_an_unrelated_browser_action_does_not_offer_a_workspace_tab():
    tools = {'browser_action': {'type': 'mcp', 'spec': {'name': 'browser_action'}}}
    browser.register_browser_open_tab(
        tools,
        {
            'terminal_id': 'managed',
            'chat_id': 'chat',
            'session_id': 'socket',
            'features': {'terminal_browser_tab': True},
        },
        AsyncMock(),
    )
    assert set(tools) == {'browser_action'}
