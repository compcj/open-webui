"""Exercise the real builtin page boundaries without importing application state."""

import ast
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from open_webui.utils.exact_tool_results import resize_exact_tool_result
from open_webui.utils.skill_files import (
    SkillFile,
    SkillFileOperation,
    bounded_skill_manifest,
    skill_content_page,
    skill_overview,
)


@pytest.fixture
def skill_tools(monkeypatch):
    content = 'Run {baseDir}/run.py\n' * 6000
    skill = SimpleNamespace(id='demo', version_id='v1', is_active=True)
    skill.model_dump = lambda: {'id': skill.id, 'version_id': skill.version_id}
    snapshots = {
        'v1': {
            'name': 'Demo',
            'content': content,
            'data': {'files': [{'path': 'SKILL.md', 'content': content}, {'path': 'run.py', 'content': 'print("ok")'}]},
        },
        'v2': {
            'name': 'Changed',
            'content': 'changed',
            'data': {'files': [{'path': 'SKILL.md', 'content': 'changed'}]},
        },
    }

    async def get_snapshot(resource, version_id):
        return copy.deepcopy(snapshots[version_id])

    async def prepare(request, actor, metadata, terminal_id, skills):
        root = f'/users/{actor["id"]}/.openwebui/skills/demo'
        return {'demo': {'content': skills[0]['content'].replace('{baseDir}', root)}}

    prepare = AsyncMock(side_effect=prepare)
    models = ModuleType('open_webui.models.skills')
    models.get_skill_snapshot = get_snapshot
    router = ModuleType('open_webui.routers.skills')
    router.authorized_skill = AsyncMock(return_value=skill)
    runtime = ModuleType('open_webui.utils.skills_runtime')
    runtime.openclaw_meta = lambda _: {'source': {'kind': 'test'}}
    runtime.prepare_skills_for_terminal = prepare
    terminal = ModuleType('open_webui.utils.terminals')
    terminal.load_terminal_skill = AsyncMock(return_value=None)
    for module in (models, router, runtime, terminal):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    path = Path(__file__).resolve().parents[1] / 'open_webui/tools/builtin.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {
        'view_skill',
        'read_skill_file',
        '_runtime_skill_content',
        'grep_skill_files',
        'create_skill',
        'update_skill_files',
    }
    tree.body = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    scope = dict(
        Request=object,
        SkillFile=SkillFile,
        SkillFileOperation=SkillFileOperation,
        asyncio=asyncio,
        skill_overview=skill_overview,
        JSONCodec=json,
        unquote=unquote,
        log=logging.getLogger(__name__),
        bounded_skill_manifest=bounded_skill_manifest,
        skill_content_page=skill_content_page,
    )
    exec(compile(tree, str(path), 'exec'), scope)
    return SimpleNamespace(**scope, skill=skill, content=content, prepare=prepare, router=router, snapshots=snapshots)


def context(actor='owner'):
    return dict(
        __request__=SimpleNamespace(state=SimpleNamespace()),
        __user__={'id': actor},
        __metadata__={'view_skill_ids': ['demo'], 'terminal_id': 'terminal', 'chat_id': 'shared'},
    )


@pytest.mark.parametrize('shorten_first', [False, True])
def test_runtime_skill_pages_reassemble_exact_prepared_root(skill_tools, shorten_first):
    ctx = context()

    async def run():
        first = await skill_tools.view_skill('demo', **ctx)
        if shorten_first:
            first = resize_exact_tool_result(first, 'skill_page', 700, len)
        page = json.loads(first)
        pieces = [page['content']]
        while page['next_offset'] is not None:
            offset = page['next_offset']
            page = json.loads(await skill_tools.read_skill_file('demo', 'SKILL.md', offset, 100000, **ctx))
            assert page['offset'] == offset
            pieces.append(page['content'])
        return ''.join(pieces)

    assert asyncio.run(run()) == skill_tools.content.replace('{baseDir}', '/users/owner/.openwebui/skills/demo')
    skill_tools.prepare.assert_awaited_once()


def test_direct_root_read_prepares_and_support_file_stays_raw(skill_tools):
    ctx = context()
    root = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', 0, 100, **ctx)))
    assert root['content'] == skill_tools.content.replace('{baseDir}', '/users/owner/.openwebui/skills/demo')[:100]
    support = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'run.py', **ctx)))
    assert support['content'] == 'print("ok")'
    skill_tools.prepare.assert_awaited_once()


def test_read_uses_pinned_version_view_refreshes_and_cache_is_request_local(skill_tools):
    ctx = context()
    asyncio.run(skill_tools.view_skill('demo', **ctx))
    skill_tools.skill.version_id = 'v2'
    old = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', 100, 100, **ctx)))
    assert old['version_id'] == 'v1'
    assert old['content'] == skill_tools.content.replace('{baseDir}', '/users/owner/.openwebui/skills/demo')[100:200]
    new = json.loads(asyncio.run(skill_tools.view_skill('demo', **ctx)))
    assert new['content'] == 'changed' and new['version_id'] == 'v2'
    assert skill_tools.prepare.await_count == 2
    assert set(ctx['__metadata__']) == {'view_skill_ids', 'terminal_id', 'chat_id', 'chat_context', 'loaded_skill_ids'}
    skill_tools.skill.version_id = 'v1'
    writer = json.loads(asyncio.run(skill_tools.view_skill('demo', **context('writer'))))
    assert '/users/writer/' in writer['content']
    assert '/users/owner/' not in writer['content']


def test_prepared_root_rechecks_current_access_and_runtime_gate(skill_tools):
    ctx = context()
    asyncio.run(skill_tools.view_skill('demo', **ctx))
    skill_tools.router.authorized_skill.side_effect = ValueError('Access revoked')
    denied = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', 100, 100, **ctx)))
    assert denied['error'] == 'Access revoked'
    skill_tools.router.authorized_skill.side_effect = None
    skill_tools.prepare.side_effect = None
    skill_tools.prepare.return_value = {'demo': {'skip_reason': 'Missing terminal dependency'}}
    gated = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', **context())))
    assert 'Missing terminal dependency' in gated['error']


@pytest.mark.parametrize('skill_id', ['unlisted', 'terminal:unlisted'])
def test_search_rejects_skills_outside_current_turn_before_loading(skill_tools, skill_id):
    result = json.loads(asyncio.run(skill_tools.grep_skill_files(skill_id, 'Run', **context())))
    assert result['error'] == 'Skill not available in this chat context'
    skill_tools.router.authorized_skill.assert_not_awaited()


def test_search_uses_pinned_prepared_root_and_exact_paging_offsets(skill_tools):
    ctx = context()
    overview = json.loads(asyncio.run(skill_tools.view_skill('demo', **ctx)))
    assert len(overview['content']) == 5000
    skill_tools.skill.version_id = 'v2'
    search = json.loads(asyncio.run(skill_tools.grep_skill_files('DEMO', '/users/owner/', **ctx)))
    assert search['version_id'] == 'v1'
    assert search['id'] == 'demo'
    assert search['total_matches'] == 6000
    match = search['matches'][0]
    page = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', match['offset'], 20, **ctx)))
    assert page['content'].startswith('/users/owner/')
    skill_tools.prepare.assert_awaited_once()


def test_search_rechecks_acl_and_runtime_gate(skill_tools):
    ctx = context()
    skill_tools.router.authorized_skill.side_effect = ValueError('Access revoked')
    denied = json.loads(asyncio.run(skill_tools.grep_skill_files('demo', 'Run', **ctx)))
    assert denied['error'] == 'Access revoked'
    skill_tools.router.authorized_skill.side_effect = None
    skill_tools.prepare.side_effect = None
    skill_tools.prepare.return_value = {'demo': {'skip_reason': 'Missing terminal dependency'}}
    gated = json.loads(asyncio.run(skill_tools.grep_skill_files('demo', 'Run', **context())))
    assert 'Missing terminal dependency' in gated['error']


def test_negative_page_offset_is_normalized_before_exact_metadata(skill_tools):
    page = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', -3, 10, **context())))
    assert page['offset'] == 0
    assert page['next_offset'] == 10


def test_injected_prepared_root_is_shared_by_overview_search_and_pages(skill_tools):
    ctx = context()
    content = '中😀 prepared /terminal/run.py'
    key = ('owner', 'terminal', 'shared', None, 'demo', 'v1')
    ctx['__request__'].state.skill_runtime_content = {key: content}
    overview = json.loads(asyncio.run(skill_tools.view_skill('demo', **ctx)))
    search = json.loads(asyncio.run(skill_tools.grep_skill_files('demo', 'prepared', **ctx)))
    page = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'SKILL.md', **ctx)))
    assert overview['content'] == page['content'] == content
    assert search['matches'][0]['offset'] == content.index('prepared')
    skill_tools.prepare.assert_not_awaited()


def test_binary_read_keeps_pinned_version_and_historical_navigation(skill_tools):
    ctx = context()
    skill_tools.snapshots['v1']['data']['files'].append({'path': 'a.bin', 'content': 'AP8=', 'encoding': 'base64'})
    asyncio.run(skill_tools.view_skill('demo', **ctx))
    skill_tools.skill.version_id = 'v2'
    result = json.loads(asyncio.run(skill_tools.read_skill_file('demo', 'a.bin', **ctx)))
    assert result['version_id'] == 'v1'
    assert 'version_id=v1' in result['url']


def test_skill_authoring_results_keep_versions_and_updates_use_pinned_expectation(skill_tools, monkeypatch):
    models = sys.modules['open_webui.models.skills']
    models.SkillForm = SimpleNamespace
    models.Skills = SimpleNamespace(
        update_skill_by_id=AsyncMock(return_value=SimpleNamespace(id='demo', version_id='v3', name='Demo'))
    )
    skill_tools.router.create_new_skill = AsyncMock(return_value=SimpleNamespace(id='new', version_id='v2'))
    access = ModuleType('open_webui.utils.access_control')
    access.has_permission = AsyncMock(return_value=True)
    monkeypatch.setitem(sys.modules, access.__name__, access)
    events = ModuleType('open_webui.events')
    events.EVENTS = SimpleNamespace(SKILL_UPDATED='skill.updated')
    events.publish_event = AsyncMock()
    monkeypatch.setitem(sys.modules, events.__name__, events)
    ctx = context()
    ctx['__user__']['role'] = 'admin'
    created = json.loads(
        asyncio.run(
            skill_tools.create_skill('new', 'New', 'body', __request__=ctx['__request__'], __user__=ctx['__user__'])
        )
    )
    assert created['version_id'] == 'v2'
    ctx['__metadata__']['chat_context'] = {'skill_versions': {'demo': 'v1'}}
    skill_tools.skill.version_id = 'v2'
    updated = json.loads(
        asyncio.run(
            skill_tools.update_skill_files('demo', [{'op': 'put', 'path': 'SKILL.md', 'content': 'next'}], **ctx)
        )
    )
    assert updated['version_id'] == 'v3'
    assert models.Skills.update_skill_by_id.await_args.args[1]['expected_version_id'] == 'v1'


@pytest.mark.parametrize(
    'available',
    [{'loaded_skill_ids': ['demo']}, {'skill_ids': ['demo'], 'chat_context': {'skill_versions': {'demo': 'v1'}}}],
)
def test_view_can_refresh_current_turn_loaded_or_selected_skill(skill_tools, available):
    ctx = context()
    ctx['__metadata__'] = available
    result = json.loads(asyncio.run(skill_tools.view_skill('demo', **ctx)))
    assert result['version_id'] == 'v1' and result['offset'] == 0


def test_terminal_search_pages_share_fingerprint_and_reject_changed_source(skill_tools):
    import hashlib
    import ntpath
    import posixpath
    from urllib.parse import quote
    from open_webui.utils.skill_files import SKILL_CONTENT_MAX_CHARS

    source = Path(__file__).resolve().parents[1] / 'open_webui/utils/terminals.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in {'get_terminal_skill', 'load_terminal_skill'}
    ]
    content = '中😀 instructions hit'
    reader = AsyncMock(return_value={'content': content})
    scope = dict(
        asyncio=asyncio,
        hashlib=hashlib,
        ntpath=ntpath,
        posixpath=posixpath,
        quote=quote,
        log=logging.getLogger(__name__),
        SKILL_CONTENT_MAX_CHARS=SKILL_CONTENT_MAX_CHARS,
        skill_content_page=skill_content_page,
        bounded_skill_manifest=bounded_skill_manifest,
        skill_overview=skill_overview,
        get_terminal_json=reader,
    )
    exec(compile(tree, str(source), 'exec'), scope)
    terminal = sys.modules['open_webui.utils.terminals']
    terminal.load_terminal_skill = scope['load_terminal_skill']
    terminal.get_terminal_skill = scope['get_terminal_skill']
    ctx = context()
    ctx['__metadata__']['view_skill_ids'] = ['terminal:demo']
    first = json.loads(asyncio.run(skill_tools.view_skill('terminal:demo', **ctx)))
    search = json.loads(asyncio.run(skill_tools.grep_skill_files('terminal:demo', 'hit', **ctx)))
    match = search['matches'][0]
    page = json.loads(asyncio.run(skill_tools.read_skill_file('terminal:demo', 'SKILL.md', match['offset'], **ctx)))
    assert first['fingerprint'] == search['fingerprint'] == page['fingerprint']
    assert page['content'] == 'hit' and match['offset'] == content.index('hit')
    reader.return_value = {'content': 'changed hit'}
    failed = json.loads(asyncio.run(skill_tools.grep_skill_files('terminal:demo', 'hit', **ctx)))
    assert 'Skill changed while reading' in failed['error']


@pytest.mark.parametrize('operation', ['view', 'read', 'grep'])
def test_old_snapshot_pin_and_tool_availability_do_not_authorize_current_turn(skill_tools, operation):
    ctx = context()
    ctx['__metadata__'] = {'chat_context': {'skill_versions': {'demo': 'v1'}}, '__skill_ids__': ['demo']}
    if operation == 'view':
        call = skill_tools.view_skill('demo', **ctx)
    elif operation == 'read':
        call = skill_tools.read_skill_file('demo', 'SKILL.md', **ctx)
    else:
        call = skill_tools.grep_skill_files('demo', 'Run', **ctx)
    result = json.loads(asyncio.run(call))
    assert result['error'] == 'Skill not available in this chat context'
    skill_tools.router.authorized_skill.assert_not_awaited()


@pytest.mark.parametrize('operation', ['view', 'read', 'grep'])
def test_explicit_selection_authorizes_skill_with_pinned_snapshot(skill_tools, operation):
    ctx = context()
    ctx['__metadata__'] = {'skill_ids': ['demo'], 'chat_context': {'skill_versions': {'demo': 'v1'}}}
    if operation == 'view':
        call = skill_tools.view_skill('demo', **ctx)
    elif operation == 'read':
        call = skill_tools.read_skill_file('demo', 'SKILL.md', **ctx)
    else:
        call = skill_tools.grep_skill_files('demo', 'Run', **ctx)
    result = json.loads(asyncio.run(call))
    assert result['version_id'] == 'v1' and 'error' not in result
    skill_tools.router.authorized_skill.assert_awaited_once()


def test_oversized_search_heading_returns_bounded_actionable_error_through_exact_path(skill_tools):
    from open_webui.utils.skill_files import SKILL_GREP_MAX_CHARS
    from open_webui.utils.tool_result_sources import registered_tool_kind

    content = '# ' + 'H' * 7000 + '\nhit\n'
    skill_tools.snapshots['v1']['content'] = content
    skill_tools.snapshots['v1']['data']['files'][0]['content'] = content
    raw = asyncio.run(skill_tools.grep_skill_files('demo', 'hit', **context()))
    page = json.loads(raw)
    assert 'narrow the pattern' in page.get('error', '')
    assert len(raw) <= SKILL_GREP_MAX_CHARS
    kind = registered_tool_kind(
        'grep_skill_files',
        {
            'grep_skill_files': {'type': 'builtin', 'tool_id': 'builtin:grep_skill_files'},
        },
    )
    assert kind == 'skill_search'
    assert json.loads(resize_exact_tool_result(raw, kind, 1000, len)) == page
