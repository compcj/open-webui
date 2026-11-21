"""Local HTTP, real SQLite/file/KB routes, and deployed local PDF/Word/text parsers."""

import asyncio
import io
import ipaddress
import json
import logging
import os
import socket
import subprocess
import sys
import typing
import urllib.parse
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from test_knowledge_write_integration import ROOT, isolated_scenario, load_source


def test_http_sources_with_real_persistence_and_document_parsers(tmp_path):
    data = tmp_path / 'knowledge-import'
    data.mkdir()
    env = {
        **os.environ,
        'PYTHONPATH': str(ROOT / 'backend'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUTF8': '1',
        'ENABLE_DB_MIGRATIONS': 'false',
        'WEBUI_SECRET_KEY': 'synthetic-import-test-key',
        'DATA_DIR': str(data),
        'STATIC_DIR': str(data / 'static'),
        'DATABASE_URL': 'sqlite:///' + (data / 'test.db').as_posix(),
        'KNOWLEDGE_WRITE_TEST_DATA': str(data),
        'OFFLINE_MODE': 'true',
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--isolated-import-scenario'],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert 'knowledge direct import HTTP/SQLite integration passed' in result.stdout


async def import_scenario(ns, request, owner, base, data, vectors):
    import aiohttp
    import http.cookiejar
    import requests
    import urllib3.connection
    import urllib3.connectionpool
    import validators
    from aiohttp import web
    from fastapi import HTTPException
    from langchain_core.document_loaders import BaseLoader
    from langchain_core.documents import Document
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from requests.adapters import HTTPAdapter
    from open_webui.models.config import Config
    from open_webui.models.files import FileForm, Files
    from open_webui.models.knowledge import Knowledges
    from open_webui.retrieval.loaders.local import DocxLoader, TextLoader
    from open_webui.retrieval.loaders.pdf import PDFLoader
    from open_webui.tools.knowledge_import import import_knowledge_terminal_file, import_knowledge_url

    # Load the real header helpers without importing optional OAuth/application
    # configuration now pulled in by the upstream headers module.
    header_ns = {'Any': typing.Any}
    load_source(header_ns, 'utils/headers.py', {'normalize_bearer_token', 'bearer_auth_header'})
    bearer_auth_header = header_ns['bearer_auth_header']
    import docx

    await Config.upsert(
        {
            'rag.bypass_embedding_and_retrieval': False,
            'web.loader.engine': 'safe_web',
            'web.loader.timeout': 5,
            'web.loader.ssl_verification': True,
            'web.loader.concurrent_requests': 2,
            'web.search.trust_env': False,
        }
    )
    vectors.fail_collection = None
    # Compile the production SSRF and basic web loader implementations without importing
    # optional external web engines or global application configuration.
    web_ns = {
        **vars(typing),
        'asyncio': asyncio,
        'aiohttp': aiohttp,
        'requests': requests,
        'urllib3': urllib3,
        'urllib': urllib,
        'http': http,
        'socket': socket,
        'ipaddress': ipaddress,
        'validators': validators,
        'HTTPAdapter': HTTPAdapter,
        'BaseLoader': BaseLoader,
        'Document': Document,
        'log': logging.getLogger(__name__),
        'ENABLE_LOCAL_WEB_FETCH': True,
        'WEB_FETCH_FILTER_LIST': [],
        'AIOHTTP_CLIENT_TIMEOUT': 10,
        'AIOHTTP_CLIENT_ALLOW_REDIRECTS': False,
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'USER_AGENT': 'synthetic-import-test',
        'WEB_LOADER_HEADERS': {'User-Agent': 'synthetic-import-test'},
        'DEFAULT_CONFIG': {},
        'ERROR_MESSAGES': ns['ERROR_MESSAGES'],
        'is_host_allowed': lambda *args: True,
        'is_host_blocked': lambda *args: False,
    }
    load_source(
        web_ns,
        'retrieval/web/utils.py',
        {
            'resolve_hostname',
            '_embedded_ipv4',
            '_assert_host_allowed',
            '_assert_addresses_allowed',
            'validate_url',
            'safe_validate_urls',
            '_ssrf_safe_new_conn',
            '_SafeHTTPConn',
            '_SafeHTTPSConn',
            '_SafeHTTPPool',
            '_SafeHTTPSPool',
            '_SSRFSafeAdapter',
            '_SSRFSafeConnector',
            'get_ssrf_safe_session',
            'get_ssrf_safe_requests_session',
            'extract_metadata',
            'SafeWebBaseLoader',
            'get_web_loader',
        },
    )
    web_module = ModuleType('open_webui.retrieval.web.utils')
    web_module.__dict__.update(web_ns)
    sys.modules['open_webui.retrieval.web.utils'] = web_module
    config_keys = [
        'rag.bypass_embedding_and_retrieval',
        'web.loader.engine',
        'web.loader.timeout',
        'web.loader.ssl_verification',
        'web.loader.concurrent_requests',
        'web.search.trust_env',
    ]
    ns['RETRIEVAL_CONFIG_KEYS'] = {key: key for key in config_keys}

    active_db = []
    real_process = ns['process_file']

    async def process(*args, **kwargs):
        active_db.append(kwargs['db'])
        try:
            return await real_process(*args, **kwargs)
        finally:
            active_db.pop()

    ns['process_file'] = process

    class Loader:
        async def aload(self, filename, content_type, path):
            assert not active_db[-1].in_transaction(), 'Parser IO must not hold a database transaction'
            loader = (
                PDFLoader(path)
                if filename.endswith('.pdf')
                else DocxLoader(path)
                if filename.endswith('.docx')
                else TextLoader(path, encoding='utf-8')
            )
            return await asyncio.to_thread(loader.load)

    ns['build_loader_from_config'] = lambda config: Loader()
    retrieval_router = ModuleType('open_webui.routers.retrieval')
    retrieval_router.__dict__.update(ns)
    sys.modules[retrieval_router.__name__] = retrieval_router

    long_text = '完整 Unicode 文档 😀 after 知识\n' * 5000
    pdf_output = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 72 720 Td (Synthetic PDF complete after source) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(pdf_output)
    pdf_bytes = pdf_output.getvalue()
    word_output = io.BytesIO()
    word_document = docx.Document()
    word_document.add_paragraph('Word complete after 知识 😀')
    word_document.save(word_output)
    word_bytes = word_output.getvalue()
    requests_seen = []
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.automations import Automations

    sys.modules['open_webui.config'].BYPASS_ADMIN_ACCESS_CONTROL = False
    Automations.get_by_id = AsyncMock(return_value=SimpleNamespace(user_id=owner.id))
    terminal_id = 'template:saved-terminal:sandbox'
    grant = {'principal_type': 'user', 'principal_id': owner.id, 'permission': 'read'}

    async def set_terminal_grant(permitted):
        await AccessGrants.set_access_grants('terminal', terminal_id, [grant] if permitted else [])

    await set_terminal_grant(True)
    terminal_key = 'synthetic-terminal-key'
    connection = {
        'id': 'saved-terminal',
        'url': '',
        'enabled': True,
        'key': terminal_key,
        'server_type': 'orchestrator',
        'config': {'access_grants': [grant]},
    }

    async def serve(incoming):
        requests_seen.append((incoming.path, dict(incoming.headers), dict(incoming.query)))
        if incoming.path == '/api/v1/terminals/templates/sandbox':
            assert incoming.headers['Authorization'] == f'Bearer {terminal_key}'
            return web.json_response({'id': 'sandbox', 'data': {'scope': 'user'}})
        if incoming.path == '/files/view':
            assert incoming.query['path'] == '/workspace/word document.docx'
            assert incoming.headers['Authorization'] == f'Bearer {terminal_key}'
            assert incoming.headers['X-User-Id'] == owner.id
            assert incoming.headers['X-Terminal-Template-Id'] == 'sandbox'
            assert 'X-Terminal-Context-Id' not in incoming.headers
            return web.Response(
                body=word_bytes, content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            )
        assert 'Authorization' not in incoming.headers and 'Cookie' not in incoming.headers
        if incoming.path == '/page':
            if sum(path == '/page' for path, _, _ in requests_seen) > 1:
                return web.Response(
                    text='<html><!--' + 'x' * 2_000_000 + '--><main>tiny</main></html>', content_type='text/html'
                )
            return web.Response(text=f'<html><main>{long_text}</main></html>', content_type='text/html')
        if incoming.path == '/oversize-page':
            return web.Response(
                text='<html><!--' + 'x' * 2_000_000 + '--><main>tiny</main></html>', content_type='text/html'
            )
        if incoming.path == '/raw.pdf':
            return web.Response(body=pdf_bytes, content_type='application/pdf')
        if incoming.path == '/long.txt':
            return web.Response(body=long_text.encode(), content_type='text/plain')
        if incoming.path == '/blank.txt':
            return web.Response(body=b' \n ', content_type='text/plain')
        raise web.HTTPNotFound()

    app = web.Application()
    app.router.add_get('/{tail:.*}', serve)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    origin = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    connection['url'] = origin
    await Config.upsert({'terminal_server.connections': [connection]})

    # The live resolver uses real connection and per-template grants. Only the
    # unselected Tether transport is stubbed in this isolated subprocess.
    tether = ModuleType('open_webui.utils.tether')
    tether.get_terminal_connections = AsyncMock(side_effect=AssertionError('Unexpected personal terminal'))
    sys.modules[tether.__name__] = tether
    terminal_ns = {
        'Config': Config,
        'ENABLE_TOOL_SERVERS': True,
        'HTTPException': HTTPException,
        'Chats': SimpleNamespace(get_accessible_chat_by_id=AsyncMock(return_value=object())),
        'bearer_auth_header': bearer_auth_header,
        'is_saved_chat_id': lambda value: bool(value),
        'quote': urllib.parse.quote,
        'unquote': urllib.parse.unquote,
        'aiohttp': aiohttp,
        'asyncio': asyncio,
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'TERMINAL_CONTEXT_TYPES': {'chat', 'automation'},
        'TERMINAL_CONTEXT_DEFAULT': 'default',
        'TERMINAL_CONTEXT_ID_SOURCES': {'chat': 'chat_id', 'automation': 'automation_id'},
        'TERMINAL_CONTEXT_HEADER': 'X-Terminal-Context-Id',
    }
    load_source(
        terminal_ns,
        'utils/terminals.py',
        {
            'is_terminal_orchestrator',
            'get_terminal_server_url',
            'terminal_context_config',
            'terminal_context_available',
            'terminal_context_id',
            'get_connection',
            'has_connection_write_access',
            'require_terminal_access',
            'template_terminal_id',
            'split_template_id',
            'server_request',
            'find_terminal_connection',
            'terminal_headers',
            'get_terminal_context',
            'MissingTerminalContext',
        },
    )
    terminal_utils = ModuleType('open_webui.utils.terminals')
    terminal_utils.__dict__.update(terminal_ns)
    sys.modules[terminal_utils.__name__] = terminal_utils
    load_source(terminal_ns, 'routers/terminals.py', {'_http_terminal_context', '_temporary_terminal_scope'})
    terminal_router = ModuleType('open_webui.routers.terminals')
    terminal_router.__dict__.update(terminal_ns)
    sys.modules[terminal_router.__name__] = terminal_router
    context = {
        '__request__': request,
        '__user__': owner.model_dump(),
        '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': [base.id], 'terminal_enabled': True},
    }
    request.cookies = {'token': 'must-not-reach-public-source'}
    request.headers = {'Authorization': 'Bearer must-not-reach-public-source'}
    request.state = SimpleNamespace()
    try:
        from open_webui.utils.knowledge_import_sources import fetch_url_source

        probe = await fetch_url_source(request, origin + '/long.txt', 1024 * 1024)
        probe.file.close()
        for path, expected, filename in [
            ('/long.txt', long_text, 'long.txt'),
            ('/page', long_text.rstrip(), None),
            ('/raw.pdf', 'Synthetic PDF complete after source', 'raw.pdf'),
        ]:
            receipt = json.loads(
                await import_knowledge_url(base.id, origin + path + '?signature=do-not-persist', **context)
            )
            assert receipt['status'] == 'success' and receipt['indexed'] and receipt['associated'], receipt
            assert len(json.dumps(receipt)) < 700 and expected not in json.dumps(receipt)
            file = await Files.get_file_by_id(receipt['file_id'])
            assert file.data['content'].strip() == expected.strip()
            assert await Knowledges.has_file(base.id, file.id)
            assert 'signature' not in json.dumps(file.meta)
            if filename == 'raw.pdf':
                assert Path(file.path).read_bytes() == pdf_bytes
            elif filename == 'long.txt':
                assert Path(file.path).read_bytes() == long_text.encode()
            else:
                assert Path(file.path).read_text(encoding='utf-8').strip() == long_text.strip()
            assert (await vectors.query(base.id, {'file_id': file.id})).documents[0] == [file.data['content']]

        assert sum(path == '/page' for path, _, _ in requests_seen) == 1
        oversized_html = json.loads(await import_knowledge_url(base.id, origin + '/oversize-page', **context))
        assert oversized_html['status'] == 'error' and 'file_id' not in oversized_html

        terminal_receipt = json.loads(
            await import_knowledge_terminal_file(
                base.id,
                '/workspace/word document.docx',
                __metadata__={'terminal_id': terminal_id, 'automation_id': 'synthetic-automation'},
                **context,
            )
        )
        assert terminal_receipt['status'] == 'success' and terminal_receipt['source_type'] == 'terminal', (
            terminal_receipt
        )
        terminal_file = await Files.get_file_by_id(terminal_receipt['file_id'])
        assert Path(terminal_file.path).read_bytes() == word_bytes
        assert terminal_file.data['content'] == 'Word complete after 知识 😀'
        assert await Knowledges.has_file(base.id, terminal_file.id)
        before = len(requests_seen)
        conflict = json.loads(await import_knowledge_url(base.id, origin + '/raw.pdf', filename='RAW.PDF', **context))
        assert conflict['status'] == 'error' and len(requests_seen) == before
        blank = json.loads(await import_knowledge_url(base.id, origin + '/blank.txt', **context))
        assert blank['status'] == 'error'
        assert await Files.get_file_by_id(blank['file_id']) is None
        assert await Knowledges.get_knowledges_by_file_id(blank['file_id']) == []

        # A revocation after target-KB embedding must leave neither membership nor
        # the just-written target chunks, and must preserve unrelated KB files.
        real_save_docs = ns['save_docs_to_vector_db']
        tracked_ids = []

        async def revoke_during_index(*args, **kwargs):
            saved = await real_save_docs(*args, **kwargs)
            if kwargs['collection_name'] == base.id and kwargs['metadata']['name'] == 'late.docx':
                tracked_ids.append(kwargs['metadata']['file_id'])
                await set_terminal_grant(False)
            return saved

        ns['save_docs_to_vector_db'] = revoke_during_index
        late = json.loads(
            await import_knowledge_terminal_file(
                base.id,
                '/workspace/word document.docx',
                filename='late.docx',
                __metadata__={'terminal_id': terminal_id, 'automation_id': 'synthetic-automation'},
                **context,
            )
        )
        assert late['status'] == 'error' and late['file_id'] == tracked_ids[0], late
        assert await Files.get_file_by_id(late['file_id']) is None
        assert not await Knowledges.has_file(base.id, late['file_id'])
        assert not (await vectors.query(base.id, {'file_id': late['file_id']})).ids[0]
        assert await Files.get_file_by_id(terminal_file.id) is not None
        await set_terminal_grant(True)
        ns['save_docs_to_vector_db'] = real_save_docs

        started, release = asyncio.Event(), asyncio.Event()
        normal_save_docs = ns['save_docs_to_vector_db']
        cancelled_ids = []

        async def pause_target_index(*args, **kwargs):
            result = await normal_save_docs(*args, **kwargs)
            if kwargs.get('collection_name') == base.id and kwargs['metadata']['name'] == 'cancelled.docx':
                cancelled_ids.append(kwargs['metadata']['file_id'])
                started.set()
                await release.wait()
            return result

        ns['save_docs_to_vector_db'] = pause_target_index
        cancelled_task = asyncio.create_task(
            import_knowledge_terminal_file(
                base.id,
                '/workspace/word document.docx',
                filename='cancelled.docx',
                __metadata__={'terminal_id': terminal_id, 'automation_id': 'synthetic-automation'},
                **context,
            )
        )
        await asyncio.wait_for(started.wait(), 10)
        cancelled_task.cancel()
        await asyncio.sleep(0)
        assert await Files.get_file_by_id(cancelled_ids[0]) is not None
        release.set()
        try:
            await cancelled_task
            raise AssertionError('Cancellation must propagate')
        except asyncio.CancelledError:
            pass
        assert await Files.get_file_by_id(cancelled_ids[0]) is None
        assert not await Knowledges.has_file(base.id, cancelled_ids[0])
        assert not (await vectors.query(base.id, {'file_id': cancelled_ids[0]})).ids[0]
        ns['save_docs_to_vector_db'] = normal_save_docs

        # An association appearing after the cleanup precheck must win over the
        # atomic owner+orphan DELETE, leaving both SQL and original storage intact.
        race_path = data / 'race.txt'
        race_path.write_text('preserve a concurrently shared upload', encoding='utf-8')
        race = await Files.insert_new_file(
            owner.id,
            FileForm(
                id='orphan-race',
                filename='race.txt',
                path=str(race_path),
                data={'content': 'preserved', 'status': 'completed'},
                meta={},
            ),
        )
        original_orphan_delete = Files.delete_owned_orphan_by_id

        async def associate_before_delete(id, user_id, db=None):
            await Knowledges.add_file_to_knowledge_by_id(base.id, id, owner.id)
            return await original_orphan_delete(id, user_id, db=db)

        Files.delete_owned_orphan_by_id = associate_before_delete
        try:
            assert await ns['_cleanup_new_orphan_upload'](request, race.id, owner) is False
        finally:
            Files.delete_owned_orphan_by_id = original_orphan_delete
        assert await Files.get_file_by_id(race.id) is not None and race_path.exists()
        assert await Knowledges.has_file(base.id, race.id)

        # Exercise the entire recovery path when a same-target association wins
        # the orphan claim: its existing target chunks must remain intact.
        await set_terminal_grant(True)
        ns['save_docs_to_vector_db'] = revoke_during_index

        async def revoke_winner(*args, **kwargs):
            saved = await real_save_docs(*args, **kwargs)
            if kwargs['collection_name'] == base.id and kwargs['metadata']['name'] == 'winner.docx':
                await set_terminal_grant(False)
            return saved

        ns['save_docs_to_vector_db'] = revoke_winner
        Files.delete_owned_orphan_by_id = associate_before_delete
        try:
            winner = json.loads(
                await import_knowledge_terminal_file(
                    base.id,
                    '/workspace/word document.docx',
                    filename='winner.docx',
                    __metadata__={'terminal_id': terminal_id, 'automation_id': 'synthetic-automation'},
                    **context,
                )
            )
        finally:
            Files.delete_owned_orphan_by_id = original_orphan_delete
            await set_terminal_grant(True)
            ns['save_docs_to_vector_db'] = real_save_docs
        assert winner['status'] == 'partial_completion' and winner['associated'] is True, winner
        assert await Files.get_file_by_id(winner['file_id']) is not None
        assert await Knowledges.has_file(base.id, winner['file_id'])
        assert (await vectors.query(base.id, {'file_id': winner['file_id']})).documents[0] == [
            'Word complete after 知识 😀'
        ]

        # Opposite ordering: file cleanup wins after slow indexing, so the stale
        # membership INSERT must fail even with SQLite FK enforcement disabled.
        normal_process = ns['process_file']
        removed_ids = []

        async def delete_after_index(*args, **kwargs):
            result = await normal_process(*args, **kwargs)
            if (
                kwargs.get('form_data', args[1] if len(args) > 1 else None).collection_name == base.id
                and result.get('filename') == 'stale.docx'
            ):
                removed_ids.append(kwargs.get('form_data', args[1] if len(args) > 1 else None).file_id)
                await ns['_cleanup_new_orphan_upload'](request, removed_ids[-1], owner)
            return result

        ns['process_file'] = delete_after_index
        try:
            stale = json.loads(
                await import_knowledge_terminal_file(
                    base.id,
                    '/workspace/word document.docx',
                    filename='stale.docx',
                    __metadata__={'terminal_id': terminal_id, 'automation_id': 'synthetic-automation'},
                    **context,
                )
            )
        finally:
            ns['process_file'] = normal_process
        assert stale['status'] == 'error' and stale['file_id'] == removed_ids[0], stale
        assert await Files.get_file_by_id(stale['file_id']) is None
        assert not await Knowledges.has_file(base.id, stale['file_id'])
        assert not (await vectors.query(base.id, {'file_id': stale['file_id']})).ids[0]
        print('knowledge direct import HTTP/SQLite integration passed')
    finally:
        await runner.cleanup()


if __name__ == '__main__' and '--isolated-import-scenario' in sys.argv:
    asyncio.run(isolated_scenario(import_scenario))
