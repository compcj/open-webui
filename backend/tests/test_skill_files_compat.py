import asyncio
import io
import json
import sys
import types
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from open_webui.utils import skill_files as sf


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as file:
        for name, content in entries.items():
            file.writestr(name, content)
    return output.getvalue()


def test_import_legacy_json_uses_canonical_tree_and_preserves_unknown_meta():
    from test_skill_compat import legacy

    value = legacy()
    value['id'] = 'demo'
    package = sf.parse_import(json.dumps(value).encode(), 'skills.json')[0]
    assert len(package['files']) == 3
    assert package['meta']['unknown'] == {'keep': True}
    assert 'frontmatter' not in package['meta']['openclaw']


def test_multi_skill_zip_and_binary_export_roundtrip():
    raw = archive(
        {'repo/one/SKILL.md': '---\nname: one\n---\nbody', 'repo/one/a.bin': b'\x00\xff', 'repo/two/SKILL.md': 'two'}
    )
    packages = sf.parse_import(raw, 'skills.zip', discover_skills=True)
    assert len(packages) == 2
    assert sf.file_bytes(next(f for f in packages[0]['files'] if f['path'] == 'a.bin')) == b'\x00\xff'
    assert len(sf.parse_import(sf.zip_export(packages), 'skills.zip')) == 2


@pytest.mark.parametrize('entries', [{'../SKILL.md': 'bad'}, {'SKILL.md': 'ok', 'a': 'x', 'a/b': 'bad'}])
def test_archive_rejects_unsafe_and_conflicting_paths(entries):
    with pytest.raises(ValueError):
        sf.parse_import(archive(entries), 'skills.zip')


def test_archive_limits_are_checked_before_accepting_files(monkeypatch):
    monkeypatch.setattr(sf, 'MAX_FILE_BYTES', 2)
    with pytest.raises(ValueError, match='limits'):
        sf.parse_import(archive({'SKILL.md': 'oversize'}), 'skills.zip')


class Body:
    def __init__(self, raw):
        self.raw = raw

    async def iter_chunked(self, size):
        yield self.raw


class Response:
    def __init__(self, status=200, location='', raw=b'body'):
        self.status = status
        self.headers = {'Location': location}
        self.content_type = 'text/markdown'
        self.content = Body(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **options):
        assert options['allow_redirects'] is False
        self.urls.append(url)
        return next(self.responses)


def fake_network(monkeypatch, responses):
    checked = []
    session = Session(responses)
    module = types.ModuleType('open_webui.retrieval.web.utils')

    def validate(url):
        checked.append(url)
        if '127.0.0.1' in url:
            raise ValueError('private target')

    module.validate_url = validate
    module.get_ssrf_safe_session = lambda **kwargs: session
    env = types.ModuleType('open_webui.env')
    env.AIOHTTP_CLIENT_SESSION_SSL = True
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, env.__name__, env)
    return checked, session


def test_redirect_target_is_validated_before_connection(monkeypatch):
    checked, session = fake_network(monkeypatch, [Response(302, 'http://127.0.0.1/SKILL.md')])
    with pytest.raises(ValueError, match='private'):
        asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))
    assert checked == ['https://example.com/SKILL.md', 'http://127.0.0.1/SKILL.md']
    assert len(session.urls) == 1


def test_redirect_limit_and_download_limit(monkeypatch):
    fake_network(monkeypatch, [Response(302, '/SKILL.md')] * 6)
    with pytest.raises(ValueError, match='redirects'):
        asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))
    fake_network(monkeypatch, [Response(raw=b'long')])
    monkeypatch.setattr(sf, 'MAX_IMPORT_BYTES', 2)
    with pytest.raises(ValueError, match='200 MiB'):
        asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))


def test_downloader_rejects_embedded_credentials(monkeypatch):
    checked, session = fake_network(monkeypatch, [])
    with pytest.raises(ValueError, match='credentials'):
        asyncio.run(sf.download_skill_url('https://user:pass@example.com/SKILL.md'))
    assert not session.urls


def test_url_endpoint_compatibility_modes_share_parser_and_enforce_import_permission():
    import ast
    from types import SimpleNamespace
    from fastapi import APIRouter, Depends, HTTPException, Request
    from pydantic import BaseModel
    from sqlalchemy.ext.asyncio import AsyncSession
    from typing import Literal
    from open_webui.utils.skills_runtime import parse_clawhub_ref, resolve_github_skill_url

    source = Path(__file__).resolve().parents[1] / 'open_webui/routers/skills.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef))
        and node.name in ('LoadSkillUrlForm', 'load_skill_from_url')
    ]

    async def get_config(*args):
        return {}

    async def deny(*args, **kwargs):
        return False

    async def download(url):
        return b'---\nname: demo\n---\nbody', 'SKILL.md', 'text/markdown'

    ns = dict(
        __name__='skill_router_under_test',
        APIRouter=APIRouter,
        Depends=Depends,
        HTTPException=HTTPException,
        Request=Request,
        BaseModel=BaseModel,
        AsyncSession=AsyncSession,
        Literal=Literal,
        router=APIRouter(),
        get_verified_user=lambda: None,
        get_async_session=lambda: None,
        Config=SimpleNamespace(get=get_config),
        has_permission=deny,
        status=SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
        ERROR_MESSAGES=SimpleNamespace(UNAUTHORIZED='denied'),
        parse_clawhub_ref=parse_clawhub_ref,
        resolve_github_skill_url=resolve_github_skill_url,
        download_skill_url=download,
        parse_import=sf.parse_import,
        skill_import_source=sf.skill_import_source,
        base64=__import__('base64'),
        zipfile=zipfile,
    )
    exec(compile(tree, str(source), 'exec'), ns)

    async def exercise():
        endpoint = ns['load_skill_from_url']
        form = ns['LoadSkillUrlForm'](url='https://example.com/SKILL.md')
        descriptor = await endpoint(None, form, SimpleNamespace(id='admin', role='admin'), None)
        packages = await endpoint(
            None,
            form.model_copy(update={'response_format': 'packages'}),
            SimpleNamespace(id='admin', role='admin'),
            None,
        )
        assert descriptor['format'] == 'markdown'
        assert packages[0]['files'][0]['content'] == descriptor['content']
        with pytest.raises(HTTPException) as denied:
            await endpoint(None, form, SimpleNamespace(id='user', role='user'), None)
        assert denied.value.status_code == 401

        async def allow(*args, **kwargs):
            return True

        ns['has_permission'] = allow
        assert await endpoint(None, form, SimpleNamespace(id='importer', role='user'), None)

    asyncio.run(exercise())


@pytest.mark.parametrize('kind', ['markdown', 'zip', 'folder'])
def test_uploaded_openclaw_package_without_gating_metadata_reaches_terminal_prepare(monkeypatch, kind):
    import ast
    from fastapi import HTTPException, UploadFile
    from unittest.mock import AsyncMock
    from open_webui.utils import skills_runtime as runtime

    markdown = '---\nname: demo\ndescription: runnable\n---\nRun python {baseDir}/run.py\n'
    if kind == 'markdown':
        package = sf.parse_import(markdown.encode(), 'SKILL.md')[0]
    elif kind == 'zip':
        package = sf.parse_import(archive({'demo/SKILL.md': markdown, 'demo/run.py': 'print(1)'}), 'demo.zip')[0]
    else:
        path = Path(__file__).resolve().parents[1] / 'open_webui/routers/skills.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        tree.body = [
            node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'read_import'
        ]
        ns = dict(
            UploadFile=UploadFile,
            MAX_IMPORT_BYTES=sf.MAX_IMPORT_BYTES,
            parse_import=sf.parse_import,
            file_bytes=sf.file_bytes,
            json=json,
            zipfile=zipfile,
            HTTPException=HTTPException,
        )
        exec(compile(tree, str(path), 'exec'), ns)
        uploads = [
            UploadFile(io.BytesIO(markdown.encode()), filename='demo/SKILL.md'),
            UploadFile(io.BytesIO(b'print(1)'), filename='demo/run.py'),
        ]
        package = asyncio.run(ns['read_import'](uploads))[0]
    skill = {**package, 'content': markdown, 'data': {'files': package['files']}}
    oc = runtime.openclaw_meta(skill)
    assert oc and oc['source']['type'] == {'markdown': 'file', 'zip': 'zip', 'folder': 'folder'}[kind]
    sync = AsyncMock(return_value='/terminal/demo')
    monkeypatch.setattr(runtime, 'sync_skill_files', sync)
    prepared = asyncio.run(runtime.prepare_skills_for_terminal(None, {'id': 'owner'}, {}, 'terminal', [skill]))
    assert '/terminal/demo/run.py' in prepared['demo']['content']
    assert 'SKILL.md' in sync.await_args.args[-2]
    if kind != 'markdown':
        assert sync.await_args.args[-2]['run.py']['content'] == 'print(1)'


def test_native_json_and_plain_native_zip_do_not_gain_openclaw_marker():
    from open_webui.utils.skills_runtime import openclaw_meta

    named_root = '---\nname: native\ndescription: Native instructions\n---\nbody'
    packages = [
        sf.parse_import(
            json.dumps(
                {'id': 'native', 'name': 'Native', 'files': [{'path': 'SKILL.md', 'content': named_root}]}
            ).encode(),
            'skill.json',
        )[0],
        sf.parse_import(archive({'SKILL.md': 'plain native body'}), 'native.zip')[0],
    ]
    for package in packages:
        assert 'openclaw' not in package['meta']
        assert openclaw_meta({**package, 'data': {'files': package['files']}}) == {}
