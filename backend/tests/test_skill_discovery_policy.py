import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import sys
import asyncio
import json
import logging
import re
from html import escape
from urllib.parse import unquote

import pytest
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

BACKEND = Path(__file__).parents[1] / 'open_webui'


def compile_nodes(path, names, ns):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    nodes = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names
    ]
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes],
                    type_ignores=[],
                )
            ),
            str(path),
            'exec',
        ),
        ns,
    )
    return tree


@pytest.fixture
def config_client():
    storage = {}
    events = []

    async def admin(request: Request):
        role = request.headers.get('x-role')
        if role != 'admin':
            raise HTTPException(status_code=401 if not role else 403)
        return SimpleNamespace(id='admin', role='admin')

    async def get(key, default=None):
        return storage.get(key, default)

    async def upsert(values):
        storage.update(values)

    async def publish(*args, **kwargs):
        events.append(kwargs)

    ns = dict(
        router=APIRouter(),
        BaseModel=BaseModel,
        Request=Request,
        Depends=Depends,
        get_admin_user=admin,
        Config=SimpleNamespace(get=get, upsert=upsert),
        publish_event=publish,
        EVENTS=SimpleNamespace(CONFIG_UPDATED='config.updated'),
    )
    compile_nodes('routers/configs.py', {'SkillsConfigForm', 'get_skills_config', 'set_skills_config'}, ns)
    app = FastAPI()
    app.include_router(ns['router'], prefix='/api/v1/configs')
    return TestClient(app), storage, events


@pytest.mark.parametrize('role,status', [(None, 401), ('user', 403), ('admin', 200)])
@pytest.mark.parametrize('method', ['get', 'post'])
def test_skills_config_requires_admin(config_client, role, status, method):
    client, storage, events = config_client
    headers = {'x-role': role} if role else {}
    kwargs = {'json': {'AUTO_DISCOVERY_SKILL_IDS': ['allowed']}} if method == 'post' else {}
    response = getattr(client, method)('/api/v1/configs/skills', headers=headers, **kwargs)
    assert response.status_code == status
    if status != 200:
        assert storage == {} and events == []


def test_skills_config_default_persistence_and_audit(config_client):
    client, storage, events = config_client
    assert client.get('/api/v1/configs/skills', headers={'x-role': 'admin'}).json() == {'AUTO_DISCOVERY_SKILL_IDS': []}
    result = client.post(
        '/api/v1/configs/skills', headers={'x-role': 'admin'}, json={'AUTO_DISCOVERY_SKILL_IDS': ['s1', 's2']}
    )
    assert result.json() == {'AUTO_DISCOVERY_SKILL_IDS': ['s1', 's2']}
    assert storage == {'skills.auto_discovery.ids': ['s1', 's2']}
    assert events[0]['subject_id'] == 'skills' and events[0]['subject_type'] == 'config'
    assert events[0]['data'] == result.json()
    assert client.get('/api/v1/configs/skills', headers={'x-role': 'admin'}).json() == result.json()


@pytest.fixture
def skill_block(monkeypatch):
    from types import ModuleType

    def fake(name, **values):
        mod = ModuleType(name)
        mod.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, mod)

    fake('open_webui.env', AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL=True, AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA=5)
    fake('open_webui.models.skills', Skills=SimpleNamespace())
    fake(
        'open_webui.utils.skills_runtime',
        openclaw_meta=lambda s: getattr(s, 'openclaw', {}),
        openclaw_frontmatter=lambda m: m.get('frontmatter', {}),
        prepare_skills_for_terminal=AsyncMock(return_value={}),
    )
    fake(
        'open_webui.utils.terminals',
        format_terminal_skill_context=lambda s: s['content'],
        format_terminal_skill_manifest_entry=lambda s: s['id'],
        get_terminal_request_info=AsyncMock(return_value=None),
        get_terminal_skill=AsyncMock(return_value=None),
        get_terminal_skills=AsyncMock(return_value=[]),
        terminal_skills_auto_discovery=AsyncMock(return_value=False),
    )
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    block = next(
        n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == 'skill_ids or use_builtin_tools'
    )
    wrapper = ast.parse('async def run():\n    pass')
    wrapper.body[0].body = [
        ast.Global(names=['available_skills', 'terminal_skills', 'form_data']),
        block,
        ast.Return(value=ast.Name(id='available_skills', ctx=ast.Load())),
    ]
    ns = dict(
        Config=SimpleNamespace(get=AsyncMock(return_value=['allowed', 'private'])),
        skill_ids=['selected'],
        use_builtin_tools=True,
        user=SimpleNamespace(id='user', role='user', model_dump=lambda: {'id': 'user'}),
        model={},
        request=object(),
        metadata={},
        extra_params={},
        terminal_id='terminal',
        mentioned_skill_ids=set(),
        available_skills=[],
        terminal_skills=[],
        view_skill_ids=[],
        form_data={'messages': []},
        event_emitter=None,
        log=logging.getLogger(__name__),
        unquote=unquote,
        add_or_update_system_message=lambda content, messages, **kwargs: [
            *messages,
            {'role': 'system', 'content': content},
        ],
    )
    exec(compile(ast.fix_missing_locations(wrapper), 'real_skill_injection', 'exec'), ns)
    skills = [
        SimpleNamespace(
            id=i, name=i, description=i, content='instructions', is_active=True, openclaw={'frontmatter': {}}
        )
        for i in ['allowed', 'unlisted', 'selected']
    ]

    async def get_skills(user_id=None, ids=None):
        assert user_id == 'user'
        return [s for s in skills if ids is None or s.id in ids]

    sys.modules['open_webui.models.skills'].Skills.get_skills = get_skills
    return ns, sys.modules['open_webui.utils.skills_runtime'], sys.modules['open_webui.utils.terminals']


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_auto_discovery_intersects_allowlist_and_acl_keeps_explicit_selection(skill_block, role):
    ns, runtime, terminal = skill_block
    ns['user'].role = role
    result = await ns['run']()
    assert {s.id for s in result} == {'allowed', 'selected'}
    assert set(ns['view_skill_ids']) == {'allowed', 'selected'}
    runtime.prepare_skills_for_terminal.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_auto_allowlist_still_prepares_only_mentioned_skill(skill_block):
    ns, runtime, terminal = skill_block
    ns['Config'].get.return_value = []
    ns['mentioned_skill_ids'] = {'selected'}
    await ns['run']()
    assert [s.id for s in ns['available_skills']] == ['selected']
    assert [s.id for s in runtime.prepare_skills_for_terminal.await_args.args[-1]] == ['selected']
    assert ns['view_skill_ids'] == []


@pytest.mark.asyncio
async def test_empty_terminal_listing_does_not_break_workspace_skills(skill_block):
    ns, runtime, terminal = skill_block
    terminal.get_terminal_skills.return_value = []
    await ns['run']()
    assert ns['available_skills']


def test_source_metadata_is_filtered_escaped_and_attribute_names_are_safe():
    ns = dict(
        escape=escape,
        re=re,
        filter_source_metadata=lambda metadata: {
            k: v for k, v in metadata.items() if k in {'page', 'bad key', 'name', 'id', 'url'}
        },
    )
    compile_nodes('utils/middleware.py', {'get_source_context'}, ns)
    context = ns['get_source_context'](
        [
            {
                'source': {'name': 'Doc', 'id': 'file'},
                'document': ['evidence'],
                'metadata': [
                    {
                        'source': 'file',
                        'page': '<2&"',
                        'bad key': 'unsafe',
                        'secret': 'hidden',
                        'id': '999',
                        'url': 'javascript:alert(1)',
                    }
                ],
            }
        ]
    )
    from xml.etree import ElementTree

    element = ElementTree.fromstring(context)
    assert element.attrib['page'] == '<2&"'
    assert 'bad key' not in context and 'secret' not in context
    assert element.attrib['id'] == '1'
    assert 'url' not in element.attrib


@pytest.fixture
def terminal_runtime(monkeypatch):
    from types import ModuleType

    config = ModuleType('open_webui.models.config')
    config.Config = SimpleNamespace(get=AsyncMock(return_value=[]))
    monkeypatch.setitem(sys.modules, config.__name__, config)
    env = ModuleType('open_webui.env')
    env.AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL = True
    env.AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = 5
    monkeypatch.setitem(sys.modules, env.__name__, env)
    ns = dict(
        asyncio=asyncio,
        log=logging.getLogger(__name__),
        _get_terminal_request_info=AsyncMock(side_effect=TimeoutError('unavailable')),
    )
    compile_nodes(
        'utils/terminals.py',
        {'get_terminal_request_info', 'get_terminal_skills', 'get_terminal_skill', 'terminal_skills_auto_discovery'},
        ns,
    )
    return ns, config.Config


@pytest.mark.asyncio
async def test_terminal_resolution_listing_and_content_fail_softly(terminal_runtime):
    ns, config = terminal_runtime
    assert await ns['get_terminal_request_info'](object(), {}, {'terminal_id': 't'}) is None
    assert await ns['get_terminal_skills'](object(), {}, {'terminal_id': 't'}) == []
    assert await ns['get_terminal_skill'](object(), {}, {'terminal_id': 't'}, 'skill') is None
    ns['get_terminal_request_info'] = AsyncMock(side_effect=TimeoutError('unavailable'))
    assert await ns['get_terminal_skills'](object(), {}, {'terminal_id': 't'}) == []
    assert await ns['get_terminal_skill'](object(), {}, {'terminal_id': 't'}, 'skill') is None


@pytest.mark.asyncio
async def test_only_stored_terminal_connection_grants_discovery(terminal_runtime):
    ns, config = terminal_runtime
    direct = {
        'terminal_id': 'https://personal.test',
        'tool_servers': [{'url': 'https://personal.test', 'config': {'auto_discover_skills': True}}],
    }
    assert await ns['terminal_skills_auto_discovery'](direct) is False
    config.get.return_value = [{'id': 't', 'config': {'auto_discover_skills': True}}]
    assert await ns['terminal_skills_auto_discovery']({'terminal_id': 't'}) is True
    assert await ns['terminal_skills_auto_discovery']({'terminal_id': 'other'}) is False
    config.get.return_value = [{'id': 't'}]
    assert await ns['terminal_skills_auto_discovery']({'terminal_id': 't'}) is False
    config.get.side_effect = TimeoutError('unavailable')
    assert await ns['terminal_skills_auto_discovery']({'terminal_id': 't'}) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic', [False, True])
async def test_terminal_skills_explicit_selection_works_without_discovery(skill_block, automatic):
    ns, runtime, terminal = skill_block
    ns['skill_ids'] = ['terminal:chosen']
    terminal.get_terminal_skills.return_value = [
        dict(id='terminal:chosen', name='chosen'),
        dict(id='terminal:other', name='other'),
    ]
    terminal.terminal_skills_auto_discovery.return_value = automatic
    await ns['run']()
    assert {s['id'] for s in ns['terminal_skills']} == (
        {'terminal:chosen', 'terminal:other'} if automatic else {'terminal:chosen'}
    )


@pytest.fixture
def view_runtime(monkeypatch):
    from types import ModuleType

    skill = SimpleNamespace(id='allowed', user_id='owner', is_active=True, name='Skill', content='default instructions')

    def fake(name, **values):
        mod = ModuleType(name)
        mod.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    fake('open_webui.models.skills', Skills=SimpleNamespace(get_skill_by_id=AsyncMock(return_value=skill)))
    acl = fake(
        'open_webui.models.access_grants', AccessGrants=SimpleNamespace(has_access=AsyncMock(return_value=False))
    )
    prepare = AsyncMock(side_effect=TimeoutError('terminal unavailable'))
    fake('open_webui.utils.skills_runtime', openclaw_meta=lambda s: {'files': {}}, prepare_skills_for_terminal=prepare)
    terminal = fake(
        'open_webui.utils.terminals', get_terminal_skill=AsyncMock(return_value={'content': 'terminal instructions'})
    )
    ns = dict(
        JSONCodec=json,
        log=logging.getLogger(__name__),
        unquote=unquote,
        Groups=SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
    )
    compile_nodes('tools/builtin.py', {'view_skill'}, ns)
    return ns['view_skill'], prepare, acl.AccessGrants, terminal


@pytest.mark.asyncio
@pytest.mark.parametrize('skill_id', ['unlisted', 'terminal:unlisted'])
async def test_view_skill_cannot_load_outside_authorized_turn_context(view_runtime, skill_id):
    view, prepare, acl, terminal = view_runtime
    result = json.loads(
        await view(skill_id, object(), {'id': 'owner', 'role': 'admin'}, {'view_skill_ids': ['allowed']})
    )
    assert result.get('error') == 'Skill not available in this chat context'
    prepare.assert_not_awaited()
    terminal.get_terminal_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_view_skill_rechecks_resource_acl_even_when_listed(view_runtime):
    view, prepare, acl, terminal = view_runtime
    result = json.loads(
        await view('allowed', object(), {'id': 'other', 'role': 'user'}, {'view_skill_ids': ['allowed']})
    )
    assert result.get('error') == 'Access denied'
    acl.has_access.assert_awaited_once()


@pytest.mark.asyncio
async def test_view_skill_terminal_prepare_outage_returns_default_instructions(view_runtime):
    view, prepare, acl, terminal = view_runtime
    result = json.loads(
        await view(
            'allowed', object(), {'id': 'owner', 'role': 'user'}, {'view_skill_ids': ['allowed'], 'terminal_id': 't'}
        )
    )
    assert result == {'name': 'Skill', 'content': 'default instructions'}
    prepare.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_terminal_skills_are_fetched_fresh_and_names_are_encoded(terminal_runtime):
    from aiohttp import web

    ns, config = terminal_runtime
    requests = []

    async def listing(request):
        requests.append((request.raw_path, request.headers.get('X-User-Id')))
        return web.json_response([{'id': 'terminal:one', 'name': 'one'}, {'id': 'workspace', 'name': 'bad'}, None])

    async def content(request):
        requests.append((request.raw_path, request.headers.get('X-User-Id')))
        return web.json_response(
            {
                'name': 'one',
                'content': 'instructions',
                'location': '/home/.agents/skills/one/SKILL.md',
                'resources': ['run.py'],
            }
        )

    app = web.Application()
    app.router.add_get('/skills', listing)
    app.router.add_get('/skills/{name}', content)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]
    ns['get_terminal_request_info'] = AsyncMock(return_value=(f'http://127.0.0.1:{port}', {'X-User-Id': 'owner'}, {}))
    try:
        first = await ns['get_terminal_skills'](object(), {}, {'terminal_id': 'one'})
        second = await ns['get_terminal_skills'](object(), {}, {'terminal_id': 'one'})
        assert first == second == [{'id': 'terminal:one', 'name': 'one'}]
        loaded = await ns['get_terminal_skill'](object(), {}, {'terminal_id': 'one'}, 'one/variant')
        assert loaded['directory'] == '/home/.agents/skills/one'
        assert loaded['resources'] == ['run.py']
        assert requests == [('/skills', 'owner'), ('/skills', 'owner'), ('/skills/one%2Fvariant', 'owner')]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_home_agents_md_reloads_per_turn_without_skill_discovery(terminal_runtime):
    import posixpath
    from aiohttp import web

    ns, config = terminal_runtime
    ns.update(posixpath=posixpath, MAX_AGENTS_MD_BYTES=32 * 1024)
    compile_nodes('utils/terminals.py', {'get_terminal_agents_md'}, ns)
    state = {'content': 'first turn'}

    async def home(request):
        return web.json_response({'home': '/home/user'})

    async def read(request):
        assert request.query['path'] == '/home/user/AGENTS.md'
        return web.json_response({'content': state['content']})

    app = web.Application()
    app.router.add_get('/files/cwd', home)
    app.router.add_get('/files/read', read)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]
    ns['get_terminal_request_info'] = AsyncMock(return_value=(f'http://127.0.0.1:{port}', {}, {}))
    try:
        assert await ns['get_terminal_agents_md'](object(), {}, {'terminal_id': 'one'}) == '# AGENTS.md\n\nfirst turn'
        state['content'] = 'next turn'
        assert await ns['get_terminal_agents_md'](object(), {}, {'terminal_id': 'one'}) == '# AGENTS.md\n\nnext turn'
        config.get.assert_not_awaited()
    finally:
        await runner.cleanup()
