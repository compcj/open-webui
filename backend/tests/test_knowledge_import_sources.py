"""Source adapters enforce streamed limits, deployed URL policy and terminal isolation."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


PATH = Path(__file__).parents[1] / 'open_webui/utils/knowledge_import_sources.py'


class Response:
    def __init__(self, data=b'raw bytes', mime='application/pdf', status=200, headers=None):
        self.data, self.status = data, status
        self.headers = {'Content-Type': mime, **(headers or {})}
        self.url = 'https://example.test/document.pdf'
        self.closed = False
        self.content = self

    async def iter_chunked(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start : start + size]

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError('remote failure with private details')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


@pytest.fixture
def source(monkeypatch):
    spec = importlib.util.spec_from_file_location('knowledge_import_sources_under_test', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = SimpleNamespace(
        module=module, response=Response(), validate=Mock(return_value=True), docs='全文' * 30_000, requests=[]
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            state.requests.append((url, kwargs))
            return state.response

    state.session = Mock(return_value=Session())
    state.loader = SimpleNamespace(aload=AsyncMock(side_effect=lambda: [SimpleNamespace(page_content=state.docs)]))
    state.web_loader = Mock(return_value=state.loader)
    for name, values in {
        'open_webui.env': {
            'AIOHTTP_CLIENT_ALLOW_REDIRECTS': False,
            'AIOHTTP_CLIENT_SESSION_SSL': True,
            'USER_AGENT': 'synthetic',
            'AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA': 10,
            'AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL': True,
        },
        'open_webui.retrieval.web.utils': {
            'validate_url': state.validate,
            'get_ssrf_safe_session': state.session,
            'get_web_loader': state.web_loader,
            'SafeWebBaseLoader': type('UnconfiguredBasicLoader', (), {}),
        },
        'open_webui.models.config': {'Config': SimpleNamespace(get_many=AsyncMock(return_value={'deployed': True}))},
        'open_webui.routers.retrieval': {'RETRIEVAL_CONFIG_KEYS': {'loader': 'web.loader.engine'}},
    }.items():
        fake = ModuleType(name)
        fake.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, fake)
    import aiohttp

    monkeypatch.setattr(aiohttp, 'ClientSession', state.session)
    return state


@pytest.mark.asyncio
async def test_basic_html_parser_reuses_the_bounded_response_without_refetch(source):
    class BasicLoader:
        def __init__(self):
            self.aload = AsyncMock(
                side_effect=AssertionError('Refetch could be arbitrarily larger than the bounded source')
            )

        def _document_from_html(self, html, url):
            assert html == '<html>café</html>'
            return SimpleNamespace(page_content='café')

    source.response = Response(b'<html>caf\xe9</html>', 'text/html; charset=iso-8859-1')
    source.response.charset = 'iso-8859-1'
    loader = BasicLoader()
    source.web_loader.return_value = loader
    sys.modules['open_webui.retrieval.web.utils'].SafeWebBaseLoader = BasicLoader
    result = await source.module.fetch_url_source(None, 'https://example.test/page', 100)
    assert result.file.read() == 'café'.encode()
    assert len(source.requests) == 1
    loader.aload.assert_not_awaited()
    result.file.close()


@pytest.mark.asyncio
async def test_url_uses_ssrf_session_without_webui_headers_or_cookies(source):
    result = await source.module.fetch_url_source(
        SimpleNamespace(headers={'Authorization': 'secret'}, cookies={'token': 'secret'}),
        'https://example.test/document.pdf',
        1024,
    )
    assert result.file.read() == b'raw bytes' and result.filename == 'document.pdf'
    assert source.session.call_args.kwargs == {'store_cookies': False}
    assert source.requests == [
        (
            'https://example.test/document.pdf',
            {'headers': {'User-Agent': 'synthetic'}, 'ssl': True, 'allow_redirects': False},
        )
    ]
    source.validate.assert_called_once_with('https://example.test/document.pdf')
    assert source.response.closed
    result.file.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('declared', [None, 'nonsense', '99999'])
async def test_declared_and_actual_stream_sizes_are_bounded(source, declared):
    source.response.data = b'x' * 100
    if declared:
        source.response.headers['Content-Length'] = declared
    with pytest.raises(source.module.ImportSourceError, match='size limit'):
        await source.module.fetch_url_source(None, 'https://example.test/document.pdf', 50)
    assert source.response.closed


@pytest.mark.asyncio
async def test_html_uses_deployed_loader_without_model_truncation(source):
    source.response = Response(b'<html><main>not a model fetch</main></html>', 'text/html')
    result = await source.module.fetch_url_source(None, 'https://Example.test/page?q=a', 1024 * 1024)
    assert result.filename == 'https___example_test_page.txt'
    assert result.file.read().decode() == source.docs
    assert result.size == len(source.docs.encode())
    source.web_loader.assert_called_once_with(['https://Example.test/page?q=a'], {'deployed': True})
    result.file.close()


@pytest.mark.asyncio
async def test_empty_loader_output_and_oversized_extracted_text_are_refused(source):
    source.response = Response(b'<html></html>', 'text/html')
    source.docs = ' '
    with pytest.raises(source.module.ImportSourceError, match='non-blank'):
        await source.module.fetch_url_source(None, 'https://example.test', 100)
    source.docs = '知' * 50
    with pytest.raises(source.module.ImportSourceError, match='size limit'):
        await source.module.fetch_url_source(None, 'https://example.test', 100)


@pytest.mark.asyncio
async def test_spool_rolls_to_disk_and_closes_on_stream_cancellation(source, monkeypatch):
    spool = source.module.tempfile.SpooledTemporaryFile(max_size=1, mode='w+b')
    monkeypatch.setattr(source.module.tempfile, 'SpooledTemporaryFile', lambda **kwargs: spool)
    source.response.data = b'x' * 100_000
    result = await source.module.fetch_url_source(None, 'https://example.test/document.pdf', None)
    assert spool._rolled and result.size == 100_000
    spool.close()


@pytest.mark.asyncio
async def test_stream_cancellation_closes_spool_and_response(source, monkeypatch):
    spool = source.module.tempfile.SpooledTemporaryFile(max_size=1, mode='w+b')
    monkeypatch.setattr(source.module.tempfile, 'SpooledTemporaryFile', lambda **kwargs: spool)

    async def cancelled(size):
        yield b'first chunk'
        raise asyncio.CancelledError

    source.response.content = SimpleNamespace(iter_chunked=cancelled)
    with pytest.raises(asyncio.CancelledError):
        await source.module.fetch_url_source(None, 'https://example.test/document.pdf', None)
    assert spool.closed and source.response.closed


@pytest.mark.asyncio
async def test_stream_timeout_closes_spool_and_response(source, monkeypatch):
    spool = source.module.tempfile.SpooledTemporaryFile(max_size=1, mode='w+b')
    monkeypatch.setattr(source.module.tempfile, 'SpooledTemporaryFile', lambda **kwargs: spool)

    entered = asyncio.Event()

    async def stalled(size):
        yield b'first chunk'
        entered.set()
        await asyncio.Event().wait()

    source.response.content = SimpleNamespace(iter_chunked=stalled)
    operation = asyncio.create_task(source.module.fetch_url_source(None, 'https://example.test/document.pdf', None))
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await operation
    assert spool.closed and source.response.closed


@pytest.mark.asyncio
async def test_terminal_download_refuses_changed_binding_before_network(source, monkeypatch):
    monkeypatch.setattr(
        source.module, 'terminal_access', AsyncMock(return_value=('https://changed.test', {}, {}, 'new'))
    )
    with pytest.raises(source.module.ImportSourceError, match='changed'):
        await source.module.fetch_terminal_source(
            None, {}, {}, '/a.pdf', 1024, binding=('https://original.test', {}, {}, 'old')
        )
    assert not source.requests


@pytest.mark.asyncio
async def test_terminal_uses_fixed_encoded_endpoint_and_refuses_redirect(source, monkeypatch):
    access = AsyncMock(
        return_value=(
            'https://terminal.test/p/policy',
            {'X-User-Id': 'actor', 'X-Terminal-Context-Id': 'chat:saved'},
            {'synthetic': 'cookie'},
            'chat:saved',
        )
    )
    monkeypatch.setattr(source.module, 'terminal_access', access)
    result = await source.module.fetch_terminal_source(
        None, {'id': 'actor'}, {'terminal_id': 'saved'}, '/home/a b.pdf', 1024
    )
    url, kwargs = source.requests[-1]
    assert url == 'https://terminal.test/p/policy/files/view?path=%2Fhome%2Fa%20b.pdf'
    assert kwargs['allow_redirects'] is False
    assert kwargs['headers']['X-User-Id'] == 'actor' and kwargs['headers']['X-Terminal-Context-Id'] == 'chat:saved'
    result.file.close()
    source.response.status = 302
    with pytest.raises(source.module.ImportSourceError, match='complete file'):
        await source.module.fetch_terminal_source(None, {'id': 'actor'}, {'terminal_id': 'saved'}, '/home/a.pdf', 1024)


@pytest.mark.asyncio
async def test_url_validation_precedes_network_and_embedded_credentials_are_denied(source):
    source.validate.side_effect = ValueError('denied by deployed policy')
    with pytest.raises(ValueError):
        await source.module.fetch_url_source(None, 'https://example.test/a', 1024)
    with pytest.raises(source.module.ImportSourceError):
        await source.module.fetch_url_source(None, 'https://user:password@example.test/a', 1024)
    assert not source.requests
