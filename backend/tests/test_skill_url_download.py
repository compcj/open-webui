"""URL imports use real parser/router code with isolated, synthetic HTTP responses."""

import ast
import asyncio
import base64
import io
import json
import ssl
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Literal

import aiohttp
import pytest
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from yarl import URL

from open_webui.utils import skill_files as sf
from open_webui.utils.skills_runtime import (
    clawhub_download_url,
    clawhub_skill_api_url,
    parse_clawhub_ref,
    resolve_github_skill_url,
)

ROOT = Path(__file__).resolve().parents[1] / 'open_webui'
MARKDOWN = b'---\nname: synthetic-download\ndescription: Valid instructions\n---\nRun the task.\n'
CUSTOM_CA = ssl.create_default_context()


class Body:
    def __init__(self, raw):
        self.raw = raw

    async def read(self, size):
        return self.raw[:size]

    async def iter_chunked(self, size):
        for offset in range(0, len(self.raw), size):
            yield self.raw[offset : offset + size]


class Response:
    def __init__(self, raw=MARKDOWN, *, content_type='text/plain', status=200, location=''):
        self.status = status
        self.content_type = content_type
        self.headers = {'Content-Type': content_type, 'Location': location}
        self.content = Body(raw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **options):
        self.requests.append((url, options))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fake_network(monkeypatch, responses, configured_ssl=True):
    checked, session_options = [], []
    session = Session(responses)
    module = ModuleType('open_webui.retrieval.web.utils')
    env = ModuleType('open_webui.env')
    env.AIOHTTP_CLIENT_SESSION_SSL = configured_ssl

    def validate(url):
        checked.append(url)
        if '127.0.0.1' in url:
            raise ValueError('private target')

    def safe_session(**options):
        session_options.append(options)
        return session

    module.validate_url = validate
    module.get_ssrf_safe_session = safe_session
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(sys.modules, env.__name__, env)
    return SimpleNamespace(session=session, checked=checked, options=session_options, module=module)


def compile_nodes(path, names, scope):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if getattr(node, 'name', None) in names]
    exec(compile(tree, str(path), 'exec'), scope)
    return scope


def url_router():
    async def get_config(*args):
        return {}

    async def deny(*args, **kwargs):
        return False

    scope = dict(
        __name__='skill_url_router_under_test',
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
        clawhub_download_url=clawhub_download_url,
        clawhub_skill_api_url=clawhub_skill_api_url,
        download_skill_url=sf.download_skill_url,
        parse_import=sf.parse_import,
        skill_import_source=sf.skill_import_source,
        base64=base64,
        json=json,
        zipfile=zipfile,
    )
    return compile_nodes(
        ROOT / 'routers/skills.py', {'LoadSkillUrlForm', 'load_skill_from_url', 'resolve_clawhub_skill'}, scope
    )


def load_url(url, mode='packages', *, scope=None, actor=None):
    scope = scope or url_router()
    form = scope['LoadSkillUrlForm'](url=url, response_format=mode)
    return asyncio.run(
        scope['load_skill_from_url'](None, form, actor or SimpleNamespace(id='admin', role='admin'), None)
    )


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('content_type', ['text/plain', 'application/octet-stream', 'text/markdown', 'text/x-markdown'])
@pytest.mark.parametrize('path', ['/download?file=SKILL.md', '/SKILL.md'])
def test_direct_markdown_url_works_in_both_response_modes(monkeypatch, mode, content_type, path):
    network = fake_network(monkeypatch, [Response(content_type=content_type)])
    url = 'https://example.com' + path
    result = load_url(url, mode)
    if mode == 'descriptor':
        assert result['format'] == 'markdown'
        assert result['content'] == MARKDOWN.decode()
        assert result['fileName'] == (
            'SKILL.md' if content_type == 'text/markdown' or path == '/SKILL.md' else 'download'
        )
        assert result['source'] == {'type': 'url', 'url': url}
    else:
        assert result[0]['id'] == 'synthetic-download'
        assert result[0]['files'] == [{'path': 'SKILL.md', 'content': MARKDOWN.decode()}]
        assert result[0]['meta']['openclaw']['source'] == {'type': 'url', 'url': url}
    assert network.checked == [url]
    assert network.options == [{'trust_env': False, 'store_cookies': False}]
    assert network.session.requests[0][1]['allow_redirects'] is False


@pytest.mark.parametrize('content_type', ['text/plain', 'application/octet-stream'])
@pytest.mark.parametrize('raw', [MARKDOWN, b'# Instructions\n\nRun the task without frontmatter.\n'])
def test_extensionless_url_helper_preserves_markdown_bytes(monkeypatch, content_type, raw):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    packages = asyncio.run(sf.load_skill_from_url('https://example.com/download'))
    assert sf.file_bytes(packages[0]['files'][0]) == raw


@pytest.mark.parametrize(
    'configured_ssl', [True, False, CUSTOM_CA], ids=['default-ca', 'verification-disabled', 'custom-ca']
)
def test_configured_ssl_is_sent_on_every_validated_redirect(monkeypatch, configured_ssl):
    network = fake_network(
        monkeypatch,
        [Response(status=302, location='https://cdn.example.com/download'), Response(content_type='text/markdown')],
        configured_ssl,
    )
    data, _, _ = asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))
    assert data == MARKDOWN
    assert network.checked == ['https://example.com/SKILL.md', 'https://cdn.example.com/download']
    assert [url for url, _ in network.session.requests] == network.checked
    assert all(options.get('ssl') is configured_ssl for _, options in network.session.requests)
    assert all(options['allow_redirects'] is False for _, options in network.session.requests)
    assert network.options == [{'trust_env': False, 'store_cookies': False}]


def test_custom_ca_reaches_real_ssrf_safe_connector(monkeypatch):
    network = fake_network(monkeypatch, [], CUSTOM_CA)
    checked_addresses = []
    scope = compile_nodes(
        ROOT / 'retrieval/web/utils.py',
        {'_SSRFSafeConnector', 'get_ssrf_safe_session'},
        dict(
            aiohttp=aiohttp,
            AIOHTTP_CLIENT_TIMEOUT=23,
            _assert_host_allowed=lambda host: None,
            _assert_addresses_allowed=checked_addresses.append,
        ),
    )
    monkeypatch.setattr(network.module, 'get_ssrf_safe_session', scope['get_ssrf_safe_session'])
    requested = []

    def get(session, url, **options):
        assert session.trust_env is False
        assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)
        assert isinstance(session.connector, scope['_SSRFSafeConnector'])
        assert session.timeout.total == 23
        request = aiohttp.ClientRequest('GET', URL(url), ssl=options.get('ssl', True))
        assert session.connector._get_ssl_context(request) is CUSTOM_CA
        requested.append(url)
        return Response(content_type='text/markdown')

    monkeypatch.setattr(aiohttp.ClientSession, 'get', get)
    assert asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))[0] == MARKDOWN
    assert requested == ['https://example.com/SKILL.md']


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('path', ['/download', '/SKILL.md'])
@pytest.mark.parametrize(
    'raw,content_type',
    [
        (b'<html><body>Web page</body></html>', 'text/html'),
        (b'<!DOCTYPE html>\n<html>Web page</html>', 'text/plain'),
        (b'\xef\xbb\xbf \n<HTML>Web page</HTML>', 'application/octet-stream'),
        (b'<html>Web page</html>', 'application/xhtml+xml'),
        (b'\x89PNG\r\n\x1a\n', 'application/octet-stream'),
        (b'\x00binary', 'text/plain'),
        (b'\x01\x02binary', 'application/octet-stream'),
        (b'%PDF-1.7\nprintable binary payload', 'application/pdf'),
        (b'%PDF-1.7\nprintable binary payload', 'application/octet-stream'),
        (b'', 'text/plain'),
    ],
    ids=[
        'html',
        'mislabelled-html',
        'bom-html',
        'xhtml',
        'binary',
        'nul',
        'controls',
        'pdf',
        'mislabelled-pdf',
        'empty',
    ],
)
def test_url_modes_reject_web_pages_and_unsupported_binary(monkeypatch, mode, path, raw, content_type):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    with pytest.raises(HTTPException) as error:
        load_url('https://example.com' + path, mode)
    assert error.value.status_code == 400


@pytest.mark.parametrize('location', ['http://127.0.0.1/SKILL.md', 'https://user:pass@cdn.example.com/SKILL.md'])
def test_redirects_cannot_reach_private_or_credentialed_targets(monkeypatch, location):
    network = fake_network(monkeypatch, [Response(status=302, location=location)], CUSTOM_CA)
    with pytest.raises(ValueError, match='private|credentials'):
        asyncio.run(sf.download_skill_url('https://example.com/SKILL.md'))
    assert len(network.session.requests) == 1


@pytest.mark.parametrize('failure', [TimeoutError(), aiohttp.ClientError('synthetic transport failure')])
def test_transport_failures_return_retryable_download_error(monkeypatch, failure):
    fake_network(monkeypatch, [failure], CUSTOM_CA)
    with pytest.raises(ValueError, match='Please try again'):
        asyncio.run(sf.download_skill_url('https://example.com/download'))


def test_download_http_error_is_reported_as_bad_import(monkeypatch):
    fake_network(monkeypatch, [Response(status=404)])
    with pytest.raises(HTTPException) as error:
        load_url('https://example.com/download')
    assert error.value.status_code == 400
    assert 'accessible' in error.value.detail


def test_unprivileged_import_is_denied_before_download(monkeypatch):
    network = fake_network(monkeypatch, [])
    with pytest.raises(HTTPException) as error:
        load_url('https://example.com/download', actor=SimpleNamespace(id='user', role='user'))
    assert error.value.status_code == 401
    assert not network.checked and not network.options and not network.session.requests


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as file:
        for name, raw in entries.items():
            file.writestr(name, raw)
    return output.getvalue()


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
def test_json_download_keeps_native_skill_import(monkeypatch, mode):
    raw = json.dumps(
        {'id': 'native', 'name': 'Native', 'files': [{'path': 'SKILL.md', 'content': 'Native body'}]}
    ).encode()
    fake_network(monkeypatch, [Response(raw, content_type='application/json')])
    result = load_url('https://example.com/download', mode)
    if mode == 'packages':
        assert result[0]['id'] == 'native'
        assert result[0]['files'] == [{'path': 'SKILL.md', 'content': 'Native body'}]
    else:
        assert result['content'] == raw.decode()
        assert result['fileName'] == 'skills.json'


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize(
    'url,expected_ids,source_type',
    [
        ('https://example.com/download', ['one', 'two'], 'url'),
        ('https://github.com/owner/repo', ['one', 'two'], 'github'),
        ('https://github.com/owner/repo/tree/main/skills/two', ['two'], 'github'),
        ('https://skills.sh/owner/repo/two', ['two'], 'skillsh'),
    ],
)
def test_zip_import_keeps_multi_skill_and_selection_semantics(monkeypatch, mode, url, expected_ids, source_type):
    raw = archive(
        {
            'repo/skills/one/SKILL.md': b'---\nname: one\n---\nFirst body',
            'repo/skills/two/SKILL.md': b'---\nname: two\n---\nSecond body',
            'repo/skills/two/asset.bin': b'\x00\xff',
        }
    )
    network = fake_network(monkeypatch, [Response(raw, content_type='application/octet-stream')])
    result = load_url(url, mode)
    if mode == 'packages':
        assert [package['id'] for package in result] == expected_ids
        assert all(package['meta']['openclaw']['source']['type'] == source_type for package in result)
    else:
        assert result['format'] == 'zip'
        assert base64.b64decode(result['content']) == raw
        assert result['source']['type'] == source_type
        assert ('select' in result) is (len(expected_ids) == 1)
    if source_type == 'github' or source_type == 'skillsh':
        assert network.checked[0].startswith('https://codeload.github.com/owner/repo/zip/')


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
def test_clawhub_resolution_keeps_owner_version_and_archive(monkeypatch, mode):
    metadata = json.dumps({'latestVersion': {'version': '1.2.3'}, 'owner': {'handle': 'owner'}}).encode()
    raw = archive({'SKILL.md': MARKDOWN})
    network = fake_network(
        monkeypatch,
        [Response(metadata, content_type='application/json'), Response(raw, content_type='application/zip')],
        CUSTOM_CA,
    )
    url = 'https://clawhub.ai/skills/synthetic-download'
    result = load_url(url, mode)
    source = result[0]['meta']['openclaw']['source'] if mode == 'packages' else result['source']
    assert source == {'type': 'clawhub', 'slug': '@owner/synthetic-download', 'version': '1.2.3', 'url': url}
    assert network.checked == [
        clawhub_skill_api_url('synthetic-download'),
        clawhub_download_url('synthetic-download', '1.2.3', 'owner'),
    ]
    assert all(options.get('ssl') is CUSTOM_CA for _, options in network.session.requests)


def test_local_import_without_extension_still_requires_supported_file_type():
    with pytest.raises(ValueError, match='Use JSON, ZIP, or Markdown'):
        sf.parse_import(MARKDOWN, 'download')


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('path', ['/download', '/SKILL.md'])
@pytest.mark.parametrize(
    'raw,content_type',
    [
        (
            b'<!-- generated -->\n<!DOCTYPE html>\n<html><head><title>Error</title></head><body>404</body></html>',
            'text/plain',
        ),
        (
            b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            b'<html xmlns="http://www.w3.org/1999/xhtml"><body>Error</body></html>',
            'application/octet-stream',
        ),
        (
            b'\xef\xbb\xbf <!-- generated -->\r\n<?xml version="1.0"?>\r\n<!-- second -->\r\n<HTML>404</HTML>',
            'text/plain',
        ),
    ],
    ids=['comment-preamble', 'xml-preamble', 'mixed-preambles'],
)
def test_html_documents_with_preambles_are_rejected(monkeypatch, mode, path, raw, content_type):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    with pytest.raises(HTTPException) as error:
        load_url('https://example.com' + path, mode)
    assert error.value.status_code == 400
    assert 'web page' in error.value.detail


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('content_type', ['text/plain', 'application/octet-stream'])
@pytest.mark.parametrize(
    'raw',
    [
        b'<!-- Example: <!DOCTYPE html><html>an example</html> -->\n# Instructions\nRun the task.\n',
        b'<!-- generated -->\n```html\n<!DOCTYPE html>\n<html>an example</html>\n```\nExplain the example.\n',
        b'```xml\n<?xml version="1.0"?>\n<!-- generated -->\n<html>an example</html>\n```\nExplain the example.\n',
    ],
    ids=['markdown-comment', 'comment-before-fence', 'xml-in-fence'],
)
def test_markdown_comments_and_fenced_html_examples_are_preserved(monkeypatch, mode, content_type, raw):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    result = load_url('https://example.com/download', mode)
    if mode == 'descriptor':
        assert result['format'] == 'markdown'
        assert result['content'].encode() == raw
    else:
        assert sf.file_bytes(result[0]['files'][0]) == raw


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('path', ['/download', '/SKILL.md'])
@pytest.mark.parametrize('content_type', ['text/plain', 'application/octet-stream'])
@pytest.mark.parametrize(
    'raw',
    [
        b'    <html><body>Example</body></html>\n\nExplain the example.\n',
        b'<!-- generated -->\n\n    <!DOCTYPE html>\n    <html>Example</html>\n\nExplain the example.\n',
        b'\t<html><body>Example</body></html>\n\nExplain the example.\n',
        b'<!-- generated -->\n\n\t<!DOCTYPE html>\n\t<html>Example</html>\n\nExplain the example.\n',
        b'\xef\xbb\xbf \r\n\t\r\n<!-- first -->\r\n<?xml version="1.0"?>\r\n'
        b'<?xml-stylesheet href="style.css"?>\r\n<!-- second -->\r\n\r\n'
        b' \t<!DOCTYPE html>\r\n \t<html>Example</html>\r\n\r\nExplain the example.\r\n',
        b'<!-- first --> <?render?>\n\n    <html>Example</html>\n\nExplain the example.\n',
    ],
    ids=['four-spaces', 'comment-four-spaces', 'tab', 'comment-tab', 'mixed-prefix-space-tab', 'same-line-prefix'],
)
def test_indented_markdown_html_code_is_preserved(monkeypatch, mode, path, content_type, raw):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    result = load_url('https://example.com' + path, mode)
    if mode == 'descriptor':
        assert result['format'] == 'markdown'
        assert result['content'].encode() == raw
    else:
        assert sf.file_bytes(result[0]['files'][0]) == raw


@pytest.mark.parametrize('mode', ['descriptor', 'packages'])
@pytest.mark.parametrize('path', ['/download', '/SKILL.md'])
@pytest.mark.parametrize('content_type', ['text/plain', 'application/octet-stream'])
@pytest.mark.parametrize(
    'raw',
    [
        b'<?xml version="1.0"?>\n<?xml-stylesheet type="text/css" href="style.css"?>\n'
        b'<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml"><body>Error</body></html>',
        b'<?render mode="html"?>\n<!-- generated -->\n\n<?style?>\n<!DOCTYPE html>\n<html>Error</html>',
        b'\xef\xbb\xbf \r\n    \r\n\t\r\n  <!-- first --> <?xml version="1.0"?>    <?render:style?>\r\n'
        b'   <!DOCTYPE html>\r\n<html>Error</html>',
        b'<!-- generated -->    <html>Error</html>',
    ],
    ids=['stylesheet-pi', 'generic-pi', 'blank-lines-three-spaces', 'same-line-html'],
)
def test_html_after_general_processing_instructions_is_rejected(monkeypatch, mode, path, content_type, raw):
    fake_network(monkeypatch, [Response(raw, content_type=content_type)])
    with pytest.raises(HTTPException) as error:
        load_url('https://example.com' + path, mode)
    assert error.value.status_code == 400
    assert 'web page' in error.value.detail
