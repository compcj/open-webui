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
from open_webui.utils.skill_files import bounded_skill_manifest, skill_content_page


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
    for module in (models, router, runtime):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    path = Path(__file__).resolve().parents[1] / 'open_webui/tools/builtin.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'view_skill', 'read_skill_file', '_runtime_skill_content'}
    tree.body = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    scope = dict(
        Request=object,
        JSONCodec=json,
        unquote=unquote,
        log=logging.getLogger(__name__),
        bounded_skill_manifest=bounded_skill_manifest,
        skill_content_page=skill_content_page,
    )
    exec(compile(tree, str(path), 'exec'), scope)
    return SimpleNamespace(**scope, skill=skill, content=content, prepare=prepare, router=router)


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
