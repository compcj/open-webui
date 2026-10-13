"""Authorize terminal reads and preserve safe fallback/pagination without app imports."""

import asyncio
import hashlib
import logging
import ntpath
import posixpath
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote, unquote

import pytest

from test_skill_discovery_policy import compile_nodes


@pytest.fixture
def runtime(monkeypatch):
    config = SimpleNamespace(get=AsyncMock(return_value=[]))
    access = AsyncMock(return_value=False)
    for name, values in {
        'open_webui.env': {
            'AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL': True,
            'AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA': 5,
        },
        'open_webui.models.config': {'Config': config},
        'open_webui.models.groups': {'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[]))},
        'open_webui.models.users': {'UserModel': SimpleNamespace},
        'open_webui.utils.access_control': {'has_connection_access': access},
        'open_webui.utils.tools': {'build_tool_server_headers': AsyncMock(return_value=({}, {}))},
        'open_webui.utils.headers': {
            'bearer_auth_header': lambda key: {'Authorization': f'Bearer {key}'} if key else {}
        },
    }.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    ns = {
        'asyncio': asyncio,
        'hashlib': hashlib,
        'log': logging.getLogger(__name__),
        'ntpath': ntpath,
        'posixpath': posixpath,
        'quote': quote,
        'ENABLE_TOOL_SERVERS': True,
        'MAX_AGENTS_MD_BYTES': 32768,
        'SKILL_CONTENT_MAX_CHARS': 100000,
        'terminal_context_config': lambda *args: {},
        'terminal_context_id': lambda *args: None,
        'terminal_context_available': lambda *args: True,
        'get_terminal_server_url': lambda connection: connection['url'],
        'TERMINAL_CONTEXT_HEADER': 'X-Terminal-Context-Id',
    }
    compile_nodes('utils/skill_files.py', {'skill_content_page'}, ns)
    ns['bounded_skill_manifest'] = lambda entries, key: {key: entries}
    compile_nodes(
        'utils/terminals.py',
        {
            'get_terminal_json',
            'get_terminal_request_info',
            '_get_terminal_request_info',
            'get_terminal_skills',
            'get_terminal_skill',
            'get_terminal_agents_md',
            'terminal_skills_auto_discovery',
        },
        ns,
    )
    return SimpleNamespace(ns=ns, config=config, access=access)


@pytest.mark.asyncio
async def test_personal_reads_are_executed_in_own_browser(runtime):
    call = AsyncMock(return_value={'data': {'content': 'instructions'}})
    result = await runtime.ns['get_terminal_json'](
        object(),
        SimpleNamespace(id='owner'),
        {'terminal_id': 'https://personal.test', 'session_id': 'own-socket'},
        '/files/cwd',
        {'__event_call__': call},
    )
    assert result == {'content': 'instructions'}
    call.assert_awaited_once_with(
        {
            'type': 'request:terminal',
            'data': {'terminal_id': 'https://personal.test', 'path': '/files/cwd', 'session_id': 'own-socket'},
        }
    )


@pytest.mark.asyncio
async def test_denied_admin_connection_never_falls_back_to_browser(runtime):
    runtime.config.get.return_value = [{'id': 'admin-terminal', 'url': 'https://admin.test'}]
    call = AsyncMock()
    assert (
        await runtime.ns['get_terminal_json'](
            object(),
            SimpleNamespace(id='owner'),
            {'terminal_id': 'admin-terminal'},
            '/skills',
            {'__event_call__': call},
        )
        is None
    )
    call.assert_not_awaited()
    runtime.access.assert_awaited_once()


@pytest.mark.asyncio
async def test_personal_metadata_cannot_authorize_automatic_discovery(runtime):
    assert (
        await runtime.ns['terminal_skills_auto_discovery'](
            {
                'terminal_id': 'https://personal.test',
                'auto_discover_skills': True,
                'tool_servers': [{'url': 'https://personal.test', 'config': {'auto_discover_skills': True}}],
            }
        )
        is False
    )
    runtime.config.get.return_value = [{'id': 'admin-terminal', 'config': {'auto_discover_skills': True}}]
    assert await runtime.ns['terminal_skills_auto_discovery']({'terminal_id': 'admin-terminal'}) is True


@pytest.mark.asyncio
async def test_listing_and_content_outage_fail_softly(runtime):
    runtime.ns['get_terminal_json'] = AsyncMock(side_effect=RuntimeError('terminal offline'))
    assert await runtime.ns['get_terminal_skills'](None, None, {'terminal_id': 't'}) == []
    assert await runtime.ns['get_terminal_skill'](None, None, {'terminal_id': 't'}, 'skill') is None
    assert await runtime.ns['get_terminal_agents_md'](None, None, {'terminal_id': 't'}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('home', ['/home/user', 'C:\\Users\\User'])
async def test_agents_read_is_independent_and_uses_host_path_style(runtime, home):
    reader = AsyncMock(side_effect=[{'home': home}, {'content': 'always inspect tests'}])
    runtime.ns['get_terminal_json'] = reader
    assert (
        await runtime.ns['get_terminal_agents_md'](None, None, {'terminal_id': 't'})
        == '# AGENTS.md\n\nalways inspect tests'
    )
    expected = (
        ntpath.join(home, 'AGENTS.md')
        if ntpath.isabs(home) and not posixpath.isabs(home)
        else posixpath.join(home, 'AGENTS.md')
    )
    assert unquote(reader.call_args.args[3]) == f'/files/read?path={expected}'


@pytest.mark.asyncio
async def test_skill_pagination_uses_upstream_read_and_rejects_changed_source(runtime):
    reader = AsyncMock(return_value={'name': 'skill', 'content': 'abcdefgh', 'location': 'C:\\skills\\skill\\SKILL.md'})
    runtime.ns['get_terminal_json'] = reader
    metadata = {'terminal_id': 't'}
    page = await runtime.ns['get_terminal_skill'](None, None, metadata, 'a/b', offset=2, max_chars=3)
    assert page['content'] == 'cde' and page['next_offset'] == 5
    assert page['directory'] == 'C:\\skills\\skill'
    assert reader.call_args.args[3] == '/skills/read?name=a%2Fb'
    reader.return_value['content'] = 'changed!'
    with pytest.raises(ValueError, match='Skill changed'):
        await runtime.ns['get_terminal_skill'](None, None, metadata, 'a/b', offset=5, refresh=False)


@pytest.mark.asyncio
async def test_legacy_request_wrapper_matches_current_header_builder_signature(runtime, monkeypatch):
    async def headers(connection, request, user, server_id='', metadata=None):
        return {}, {}

    monkeypatch.setattr(sys.modules['open_webui.utils.tools'], 'build_tool_server_headers', headers)
    runtime.config.get.return_value = [{'id': 'admin-terminal', 'url': 'https://admin.test'}]
    runtime.access.return_value = True
    info = await runtime.ns['get_terminal_request_info'](
        object(), SimpleNamespace(id='owner'), {'terminal_id': 'admin-terminal'}, {}
    )
    assert info == ('https://admin.test', {'Accept': 'application/json', 'X-User-Id': 'owner'}, {})
