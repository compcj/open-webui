"""v0.12.2 skill imports/search retain fork exact-result contracts."""

import io
import json
import stat
import zipfile

import pytest

from open_webui.utils import skill_files as sf
from open_webui.utils.exact_tool_results import resize_exact_tool_result
from open_webui.utils.tool_result_sources import registered_tool_kind


def archive_with_link(link, *, regular=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('demo/SKILL.md', '---\nname: demo\n---\nbody')
        entry = zipfile.ZipInfo(link)
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, 'target')
        for name, content in (regular or {}).items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize('extension', ['.zip', '.skill'])
def test_special_entries_outside_skill_roots_are_skipped_for_single_import(extension):
    result = sf.parse_import(archive_with_link('unrelated/link'), 'demo' + extension)
    assert result[0]['files'][0]['path'] == 'SKILL.md'
    assert result[0]['meta']['openclaw']['source']['type'] == 'zip'


@pytest.mark.parametrize('link', ['demo/link', 'demo/sub/link', '../link'])
def test_special_entries_inside_roots_or_unsafe_paths_fail(link):
    with pytest.raises(ValueError):
        sf.parse_import(archive_with_link(link), 'demo.skill', discover_skills=True)


def test_directory_filter_does_not_bypass_global_archive_limits(monkeypatch):
    monkeypatch.setattr(sf, 'MAX_FILE_BYTES', 60)
    raw = archive_with_link('unrelated/link', regular={'repo/outside.txt': 'x' * 61})
    with pytest.raises(ValueError, match='limits'):
        sf.parse_import(raw, 'demo.skill', discover_skills=True, directory='skills/')


def test_search_excerpts_preserve_all_merged_match_locations_and_unicode_offsets():
    content = '# Heading\n中😀 hit and hit\n'
    result = json.loads(sf.grep_skill_content([{'path': 'SKILL.md', 'content': content}], 'hit'))
    assert result['total_matches'] == 2
    assert result['matches'][0]['locations'] == [
        {'offset': content.index('hit'), 'match_length': 3},
        {'offset': content.rindex('hit'), 'match_length': 3},
    ]
    assert content[result['matches'][0]['excerpt_offset'] :].startswith(result['matches'][0]['excerpt'])


def test_search_locations_keep_lengths_when_excerpts_are_omitted():
    content = ('match ' + 'x' * 1800 + '\n') * 40
    result = json.loads(sf.grep_skill_content([{'path': 'SKILL.md', 'content': content}], 'match', max_results=1))
    assert result['total_matches'] == 40
    assert result['omitted_matches_truncated'] is True
    assert all(item['match_length'] == 5 for item in result['omitted_matches'])
    assert len(json.dumps(result, ensure_ascii=False)) <= sf.SKILL_GREP_MAX_CHARS


def test_exact_search_registration_rejects_external_homonyms():
    name = 'grep_skill_files'
    assert registered_tool_kind(name, {name: {'type': 'builtin', 'tool_id': 'builtin:' + name}}) == 'skill_search'
    assert registered_tool_kind(name, {name: {'type': 'external', 'tool_id': 'builtin:' + name}}) == 'native'
    assert registered_tool_kind(name, {name: {'type': 'builtin', 'tool_id': 'external:' + name}}) == 'native'


def test_exact_search_reduces_only_complete_entries_and_reports_omissions():
    entries = [
        {
            'path': 'SKILL.md',
            'offset': i * 1000,
            'match_length': 3,
            'section': 'Header',
            'section_offset': 0,
            'excerpt_offset': i * 1000,
            'excerpt': '中😀 hit ' * 80,
            'locations': [{'offset': i * 1000, 'match_length': 3}],
        }
        for i in range(5)
    ]
    page = {
        'id': 'demo',
        'version_id': 'v1',
        'matches': entries,
        'total_matches': 6,
        'truncated': True,
        'omitted_matches': [{'path': 'notes.md', 'offset': 30, 'match_length': 3}],
        'omitted_matches_truncated': False,
    }
    raw = json.dumps(page, ensure_ascii=False)
    result = json.loads(resize_exact_tool_result(raw, 'skill_search', 1000, len))
    assert result['matches'] == entries[:1]
    assert result['id'] == 'demo' and result['version_id'] == 'v1'
    assert result['total_matches'] == 6 and result['truncated']
    assert result['omitted_match_count'] == 5
    assert result['omitted_matches_truncated']
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 1000
    with pytest.raises(ValueError, match='[Ss]kill search'):
        resize_exact_tool_result(raw, 'skill_search', 50, len)


@pytest.mark.parametrize('tools_enabled,size', [(True, 5000), (False, 100000)])
def test_overview_limits_and_exact_page_reassembly(tools_enabled, size):
    content = '中😀' * 60000
    overview = sf.skill_overview(content, [], tools_enabled=tools_enabled)
    assert overview['content'] == content[:size]
    assert overview['next_offset'] == size
    assert sf.skill_overview('x' * 20000, [])['next_offset'] is None


def test_archive_filter_rejects_links_inside_other_recognized_skill_roots():
    raw = archive_with_link('demo/link', regular={'repo/skills/chosen/SKILL.md': 'chosen'})
    with pytest.raises(ValueError, match='links or special'):
        sf.parse_import(raw, 'demo.skill', discover_skills=True, directory='skills/chosen/')


def test_search_uses_re2_errors_and_deadline(monkeypatch):
    from types import SimpleNamespace
    from open_webui.tools.knowledge_fs import MatchBudgetExceeded

    files = [{'path': 'SKILL.md', 'content': '中😀 abc abc'}]
    with pytest.raises(ValueError, match='RE2'):
        sf.grep_skill_content(files, '.*(?=abc)')
    readings = iter([100.0, 103.0])
    monkeypatch.setattr(sf, 'time', SimpleNamespace(monotonic=lambda: next(readings)))
    with pytest.raises(MatchBudgetExceeded, match='narrow'):
        sf.grep_skill_content(files, 'abc', case_insensitive=False)


@pytest.mark.parametrize('count', [0, 2])
def test_skill_export_requires_one_selected_skill(count):
    import ast
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from sqlalchemy.ext.asyncio import AsyncSession

    path = Path(__file__).resolve().parents[1] / 'open_webui/routers/skills.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'export_skills']
    scope = dict(
        Request=Request,
        Query=Query,
        Depends=Depends,
        AsyncSession=AsyncSession,
        APIRouter=APIRouter,
        router=APIRouter(),
        get_verified_user=lambda: None,
        get_async_session=lambda: None,
        HTTPException=HTTPException,
    )
    exec(compile(tree, str(path), 'exec'), scope)
    with pytest.raises(HTTPException) as error:
        asyncio.run(scope['export_skills'](None, 'skill', ['demo'] * count, None, SimpleNamespace(role='admin'), None))
    assert error.value.status_code == 400


def test_skill_export_uses_requested_historical_snapshot_and_roundtrips_binary():
    import ast
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
    from sqlalchemy.ext.asyncio import AsyncSession

    path = Path(__file__).resolve().parents[1] / 'open_webui/routers/skills.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'export_skills']
    skill = SimpleNamespace(id='demo', is_active=True)
    snapshot = {
        'name': 'Old',
        'description': 'old',
        'meta': {},
        'data': {
            'files': [
                {'path': 'SKILL.md', 'content': 'old'},
                {'path': 'binary', 'content': 'AP8=', 'encoding': 'base64'},
            ]
        },
    }
    get_snapshot = AsyncMock(return_value=snapshot)
    scope = dict(
        Request=Request,
        Response=Response,
        Query=Query,
        Depends=Depends,
        AsyncSession=AsyncSession,
        router=APIRouter(),
        get_verified_user=lambda: None,
        get_async_session=lambda: None,
        HTTPException=HTTPException,
        authorized_skill=AsyncMock(return_value=skill),
        get_skill_snapshot=get_snapshot,
        zip_export=sf.zip_export,
    )
    exec(compile(tree, str(path), 'exec'), scope)
    response = asyncio.run(
        scope['export_skills'](None, 'skill', ['demo'], 'old-version', SimpleNamespace(role='admin'), None)
    )
    get_snapshot.assert_awaited_once_with(skill, 'old-version', None)
    assert response.headers['content-disposition'] == 'attachment; filename="demo.skill"'
    package = sf.parse_import(response.body, 'demo.skill')[0]
    assert package['files'][0]['content'] == 'old'
    assert sf.file_bytes(next(file for file in package['files'] if file['path'] == 'binary')) == b'\x00\xff'


def test_exact_search_keeps_at_least_one_location_when_no_excerpt_was_returned():
    page = {
        'id': 'demo',
        'matches': [],
        'total_matches': 2,
        'truncated': True,
        'omitted_matches': [{'path': 'long/' * 35 + 'file.md', 'offset': i, 'match_length': 1} for i in range(2)],
        'omitted_matches_truncated': False,
    }
    with pytest.raises(ValueError, match='skill search'):
        resize_exact_tool_result(json.dumps(page), 'skill_search', 350, len)


@pytest.mark.parametrize('source', ['heading', 'path', 'metadata'])
def test_search_reports_actionable_minimum_error_if_complete_locations_cannot_fit(source):
    content = '# ' + 'H' * 7000 + '\nhit\n' if source == 'heading' else 'hit'
    path = 'p' * 7000 + '.md' if source == 'path' else 'SKILL.md'
    metadata = {'id': 'm' * 7000} if source == 'metadata' else None
    with pytest.raises(ValueError, match='skill search.*narrow the pattern'):
        sf.grep_skill_content([{'path': path, 'content': content}], 'hit', result_metadata=metadata)


@pytest.mark.parametrize('budget', [1000, 50])
def test_exact_search_rejects_positive_total_without_any_complete_locations_even_if_json_fits(budget):
    page = {
        'matches': [],
        'total_matches': 1,
        'truncated': True,
        'omitted_matches': [],
        'omitted_matches_truncated': True,
    }
    with pytest.raises(ValueError, match='skill search.*narrow the pattern'):
        resize_exact_tool_result(json.dumps(page), 'skill_search', budget, len)


def test_exact_search_accepts_a_genuine_no_match_result():
    page = {'matches': [], 'total_matches': 0, 'truncated': False}
    assert json.loads(resize_exact_tool_result(json.dumps(page), 'skill_search', 1000, len)) == page
