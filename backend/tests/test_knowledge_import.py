"""Direct imports keep source bytes outside tool results and share the normal KB pipeline."""

import asyncio
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_knowledge_write_tools import runtime as write_runtime


MODULE = Path(__file__).parents[1] / 'open_webui/tools/knowledge_import.py'


def test_direct_import_tools_are_implemented():
    assert MODULE.exists(), 'Direct knowledge imports have not been implemented'


@pytest.fixture
def runtime(write_runtime, monkeypatch):
    assert MODULE.exists(), 'Direct knowledge imports have not been implemented'
    spec = importlib.util.spec_from_file_location('knowledge_import_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = write_runtime
    state.module = module
    state.settings = {
        'rag.file.max_size': 1,
        'rag.file.allowed_extensions': [],
        'rag.bypass_embedding_and_retrieval': False,
    }
    state.config.side_effect = lambda key, default=None: state.settings.get(key, default)
    state.context['__metadata__'] = {'terminal_id': 'saved-terminal', 'chat_id': 'saved-chat'}
    state.context['__knowledge_write_context__']['terminal_enabled'] = True
    state.raw = b'%PDF-synthetic-original'
    state.source = SimpleNamespace(
        file=io.BytesIO(state.raw),
        filename='source.pdf',
        content_type='application/pdf',
        size=len(state.raw),
        source_type='url',
    )
    state.fetch = AsyncMock(side_effect=lambda *a, **k: state.source)
    monkeypatch.setattr(module, '_fetch_url_source', state.fetch)
    monkeypatch.setattr(module, '_fetch_terminal_source', state.fetch)
    state.terminal_access = AsyncMock(return_value=('http://synthetic', {}, {}, None))
    monkeypatch.setattr(module, '_terminal_access', state.terminal_access)
    state.persisted = False
    state.deleted = False
    state.files.get_file_by_id.side_effect = lambda *a, **k: (
        state.file if state.persisted and not state.deleted else None
    )

    async def upload(request, **kwargs):
        assert kwargs['process'] is False and kwargs['db'] is None
        state.received = await kwargs['file'].read()
        state.file.filename = kwargs['file'].filename
        state.file.path = '/synthetic/stored'
        state.file.data = {}
        state.file.meta = {'content_type': kwargs['file'].content_type}
        state.persisted = True
        return state.file

    async def process(request, form_data, **kwargs):
        assert kwargs['db'] is state.db and kwargs['user'].id == 'user'
        state.file.data = {'status': 'completed', 'content': '知识' * 20_000}

    async def delete(**kwargs):
        state.deleted = True

    state.upload.side_effect = upload
    state.delete.side_effect = delete
    state.process = AsyncMock(side_effect=process)
    state.vector_delete = AsyncMock()
    state.vector_query = AsyncMock(return_value=SimpleNamespace(ids=[[]]))
    original_add = state.add.side_effect

    async def add(*args, **kwargs):
        if kwargs.get('before_associate'):
            await kwargs['before_associate'](
                {
                    'status': True,
                    'collection_name': None if state.settings['rag.bypass_embedding_and_retrieval'] else 'kb',
                }
            )
        return await original_add(*args, **kwargs)

    state.add.side_effect = add
    monkeypatch.setattr(sys.modules['open_webui.routers.files'], 'process_uploaded_file', AsyncMock(), raising=False)
    retrieval = ModuleType('open_webui.routers.retrieval')
    retrieval.process_file = state.process
    retrieval.ASYNC_VECTOR_DB_CLIENT = SimpleNamespace(delete=state.vector_delete, query=state.vector_query)
    retrieval.ProcessFileForm = lambda **kwargs: SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, retrieval.__name__, retrieval)

    async def cleanup(request, file_id, user, *, before_cleanup=None):
        if not state.members:
            await state.delete(request=request, id=file_id, user=user)
            if before_cleanup is not None:
                await before_cleanup()
            return True
        return False

    monkeypatch.setattr(sys.modules['open_webui.routers.files'], '_cleanup_new_orphan_upload', cleanup, raising=False)
    monkeypatch.setattr(sys.modules['open_webui.routers.knowledge'], '_add_file_to_knowledge', state.add, raising=False)
    return state


async def import_url(state, **kwargs):
    return json.loads(
        await state.module.import_knowledge_url(
            'kb', kwargs.pop('url', 'https://example.test/source.pdf'), **state.context, **kwargs
        )
    )


@pytest.mark.asyncio
async def test_url_preserves_document_bytes_and_returns_only_compact_metadata(runtime):
    result = await import_url(runtime)
    assert result == {
        'status': 'success',
        'knowledge_id': 'kb',
        'file_id': 'new-file',
        'filename': 'source.pdf',
        'source_type': 'url',
        'size': len(runtime.raw),
        'content_saved': True,
        'associated': True,
        'indexed': True,
    }
    assert runtime.received == runtime.raw
    assert len(json.dumps(result)) < 500 and '知识' not in json.dumps(result)
    runtime.upload.assert_awaited_once()
    runtime.process.assert_awaited_once()
    assert runtime.source.file.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['disabled', 'scope', 'external', 'acl'])
async def test_import_denied_before_fetch(runtime, denial):
    if denial == 'disabled':
        runtime.context['__knowledge_write_context__']['enabled'] = False
    elif denial == 'scope':
        runtime.context['__knowledge_write_context__']['knowledge_ids'] = ['other']
    elif denial == 'external':
        runtime.base.meta = {'source': 'external'}
    else:
        runtime.base.user_id = 'other'
    assert 'error' in await import_url(runtime)
    runtime.fetch.assert_not_awaited()
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_name_conflict_avoids_fetch_and_late_conflict_avoids_upload(runtime):
    runtime.root = [(SimpleNamespace(filename='Chosen.PDF'), None)]
    assert 'error' in await import_url(runtime, filename='chosen.pdf')
    runtime.fetch.assert_not_awaited()
    runtime.root = []

    async def fetch(*args, **kwargs):
        runtime.directories = [SimpleNamespace(name='SOURCE.PDF')]
        return runtime.source

    runtime.fetch.side_effect = fetch
    assert 'error' in await import_url(runtime)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_revocation_during_download_prevents_persistence(runtime):
    async def fetch(*args, **kwargs):
        runtime.base.user_id = 'other'
        return runtime.source

    runtime.fetch.side_effect = fetch
    assert 'error' in await import_url(runtime)
    runtime.upload.assert_not_awaited()
    assert runtime.source.file.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'filename', ['../x.pdf', 'CON.pdf', '.hidden.pdf', 'a\\b.pdf', 'a.docx', 'a.pdf ', 'x\x00.pdf']
)
async def test_filename_safety_and_extension_match(runtime, filename):
    assert 'error' in await import_url(runtime, filename=filename)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_extension_and_actual_size_enforced(runtime):
    runtime.settings['rag.file.allowed_extensions'] = ['txt']
    assert 'error' in await import_url(runtime)
    runtime.upload.assert_not_awaited()
    runtime.settings['rag.file.allowed_extensions'] = []
    runtime.source.size = 2 * 1024 * 1024
    assert 'error' in await import_url(runtime)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_bypass_is_honest_and_blank_extraction_is_cleaned(runtime):
    runtime.settings['rag.bypass_embedding_and_retrieval'] = True
    result = await import_url(runtime)
    assert result['indexed'] is False and result['indexing_skipped'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('bypassed', [False, True])
async def test_indexing_receipt_uses_actual_association_result_when_configuration_changes(runtime, bypassed):
    async def add(*args, **kwargs):
        await kwargs['before_associate']({'status': True, 'collection_name': None if bypassed else 'kb'})
        runtime.settings['rag.bypass_embedding_and_retrieval'] = not bypassed
        runtime.members = [runtime.base]

    runtime.add.side_effect = add
    result = await import_url(runtime)
    assert result['indexed'] is (not bypassed)
    assert result.get('indexing_skipped', False) is bypassed


@pytest.mark.asyncio
async def test_failed_blank_processing_cleans_only_confirmed_orphan(runtime):
    async def blank(*args, **kwargs):
        runtime.file.data = {'status': 'completed', 'content': '  '}

    runtime.process.side_effect = blank
    result = await import_url(runtime)
    assert result['status'] == 'error' and result['content_saved'] is False
    runtime.delete.assert_awaited_once()
    runtime.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_association_preserves_file_associated_elsewhere_and_reports_partial(runtime):
    async def fail(*args, **kwargs):
        runtime.members = [SimpleNamespace(id='another-kb')]
        raise RuntimeError('secret https://user:password@example.test/private')

    runtime.add.side_effect = fail
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['file_id'] == 'new-file'
    assert result['content_saved'] is True and result['associated'] is False and result['indexed'] is False
    assert 'recovery' in result and 'password' not in json.dumps(result)
    runtime.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_denial_cleans_only_target_file_id_chunks_before_orphan_delete(runtime):
    async def deny(*args, **kwargs):
        raise RuntimeError('final association denied')

    runtime.add.side_effect = deny
    result = await import_url(runtime)
    assert result['status'] == 'error'
    runtime.vector_delete.assert_awaited_once_with(collection_name='kb', filter={'file_id': 'new-file'})
    runtime.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_recovery_preserves_same_target_membership_that_wins_cleanup_claim(runtime, monkeypatch):
    original_cleanup = sys.modules['open_webui.routers.files']._cleanup_new_orphan_upload

    async def concurrent_association(*args, **kwargs):
        runtime.members = [runtime.base]
        return await original_cleanup(*args, **kwargs)

    monkeypatch.setattr(sys.modules['open_webui.routers.files'], '_cleanup_new_orphan_upload', concurrent_association)
    runtime.add.side_effect = RuntimeError('failed after indexing')
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['associated'] is True
    runtime.vector_delete.assert_not_awaited()
    runtime.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_cleanup_failure_after_orphan_claim_reports_partial(runtime):
    runtime.add.side_effect = RuntimeError('indexing failed')
    runtime.vector_delete.side_effect = RuntimeError('vector unavailable')
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['file_id'] == 'new-file'
    assert result['cleanup_verified'] is False and result['file_retained'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('query_result', [None, SimpleNamespace(ids=[['remaining-chunk']])])
async def test_silent_vector_delete_or_unknown_query_reports_unverified_claimed_file(runtime, query_result):
    runtime.add.side_effect = RuntimeError('indexing failed')
    runtime.vector_query.return_value = query_result
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['cleanup_verified'] is False
    assert result['file_retained'] is False


@pytest.mark.asyncio
async def test_cleanup_failure_after_row_delete_reports_absent_row_and_operator_recovery(runtime, monkeypatch):
    async def cleanup(*args, **kwargs):
        runtime.deleted = True
        raise RuntimeError('physical cleanup failed')

    monkeypatch.setattr(sys.modules['open_webui.routers.files'], '_cleanup_new_orphan_upload', cleanup)
    runtime.add.side_effect = RuntimeError('indexing failed')
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['file_retained'] is False
    assert result['content_saved'] is False and 'row is absent' in result['recovery']


@pytest.mark.asyncio
async def test_config_value_error_never_reaches_model_receipt(runtime):
    runtime.config.side_effect = ValueError('synthetic-private-token')
    result = await import_url(runtime)
    assert 'synthetic-private-token' not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('presence', [[True, False], [True, True], [None]])
async def test_orphan_helper_uses_verified_collection_delete_instead_of_unfiltered_item_delete(presence):
    import ast
    from unittest.mock import Mock

    path = MODULE.parents[1] / 'routers/files.py'
    node = next(
        node
        for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_cleanup_new_orphan_upload'
    )
    files = SimpleNamespace(
        get_file_by_id=AsyncMock(
            return_value=SimpleNamespace(user_id='actor', path='/synthetic/upload', filename='source.pdf')
        ),
        delete_owned_orphan_by_id=AsyncMock(return_value=True),
    )
    vectors = SimpleNamespace(
        has_collection=AsyncMock(side_effect=presence),
        delete=AsyncMock(side_effect=AssertionError('Unfiltered item delete is not collection cleanup')),
        delete_collection=AsyncMock(),
    )
    storage = SimpleNamespace(delete_file=Mock())
    ns = {
        'asyncio': asyncio,
        'Files': files,
        'ASYNC_VECTOR_DB_CLIENT': vectors,
        'Storage': storage,
        'publish_event': AsyncMock(),
        'EVENTS': SimpleNamespace(FILE_DELETED='deleted'),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    operation = ns['_cleanup_new_orphan_upload'](object(), 'new-file', SimpleNamespace(id='actor'))
    if presence == [True, False]:
        assert await operation is True
        storage.delete_file.assert_called_once_with('/synthetic/upload')
    else:
        with pytest.raises(RuntimeError, match='could not be verified'):
            await operation
        storage.delete_file.assert_not_called()
    vectors.delete.assert_not_awaited()
    if presence[0] is True:
        vectors.delete_collection.assert_awaited_once_with(collection_name='file-new-file')


@pytest.mark.asyncio
async def test_atomic_membership_insert_refuses_an_upload_deleted_before_association():
    import ast
    import time
    import uuid
    from contextlib import asynccontextmanager
    from typing import Optional
    from pydantic import BaseModel, ConfigDict
    from sqlalchemy import Column, Integer, String, insert, literal, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.orm import declarative_base

    base = declarative_base()

    class File(base):
        __tablename__ = 'file'
        id = Column(String, primary_key=True)

    class KnowledgeFile(base):
        __tablename__ = 'knowledge_file'
        id = Column(String, primary_key=True)
        knowledge_id = Column(String)
        file_id = Column(String)
        directory_id = Column(String, nullable=True)
        user_id = Column(String)
        created_at = Column(Integer)
        updated_at = Column(Integer)

    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    path = MODULE.parents[1] / 'models/knowledge.py'
    source = ast.parse(path.read_text(encoding='utf-8'))
    model = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'KnowledgeFileModel')
    table = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'KnowledgeTable')
    method = next(
        node
        for node in table.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'add_file_to_knowledge_by_id'
    )
    ns = dict(
        BaseModel=BaseModel,
        ConfigDict=ConfigDict,
        Optional=Optional,
        time=time,
        uuid=uuid,
        get_async_db_context=context,
        AsyncSession=AsyncSession,
        KnowledgeFile=KnowledgeFile,
        File=File,
        insert=insert,
        literal=literal,
        select=select,
    )
    exec(compile(ast.Module(body=[model, method], type_ignores=[]), str(path), 'exec'), ns)
    async with engine.begin() as connection:
        await connection.run_sync(base.metadata.create_all)
    try:
        assert await ns['add_file_to_knowledge_by_id'](None, 'kb', 'deleted-file', 'actor') is None
        async with sessions() as session:
            assert (await session.execute(select(KnowledgeFile))).scalars().all() == []
            session.add(File(id='live-file'))
            await session.commit()
        added = await ns['add_file_to_knowledge_by_id'](None, 'kb', 'live-file', 'actor')
        assert added is not None and added.file_id == 'live-file'
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_settles_started_parser_before_cleanup_and_propagates(runtime):
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        runtime.file.data = {'status': 'failed'}

    runtime.process.side_effect = delayed
    task = asyncio.create_task(import_url(runtime))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not runtime.source.file.closed
    runtime.delete.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    runtime.delete.assert_awaited_once()
    assert runtime.source.file.closed


@pytest.mark.asyncio
async def test_cancelled_successful_parser_does_not_start_association(runtime):
    started, release = asyncio.Event(), asyncio.Event()
    original = runtime.process.side_effect

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        await original(*args, **kwargs)

    runtime.process.side_effect = delayed
    task = asyncio.create_task(import_url(runtime))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    runtime.add.assert_not_awaited()
    runtime.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_association_callback_rechecks_live_terminal_after_embedding(runtime):
    called = []

    async def add(request, id, form_data, **kwargs):
        runtime.terminal_access.return_value = ('https://changed.test', {}, {}, None)
        called.append('before_associate' in kwargs)
        await kwargs['before_associate']({'status': True, 'collection_name': id})
        runtime.members = [runtime.base]

    runtime.add.side_effect = add
    result = json.loads(
        await runtime.module.import_knowledge_terminal_file('kb', '/home/source.pdf', **runtime.context)
    )
    assert result['status'] == 'error' and runtime.members == []
    assert called == [True]
    runtime.delete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path', ['relative.pdf', '/../x.pdf', '/x/../y.pdf', '//x.pdf', '/x\\y.pdf', '/x\x00.pdf', '/x/']
)
async def test_terminal_requires_canonical_absolute_file_path(runtime, path):
    result = json.loads(await runtime.module.import_knowledge_terminal_file('kb', path, **runtime.context))
    assert 'error' in result
    runtime.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_rechecks_access_and_uses_selected_context(runtime):
    runtime.source.source_type = 'terminal'
    result = json.loads(
        await runtime.module.import_knowledge_terminal_file('kb', '/home/source.pdf', **runtime.context)
    )
    assert result['status'] == 'success' and result['source_type'] == 'terminal'
    assert runtime.terminal_access.await_count >= 3


@pytest.mark.asyncio
async def test_terminal_model_capability_is_required(runtime):
    runtime.context['__knowledge_write_context__']['terminal_enabled'] = False
    assert 'error' in json.loads(
        await runtime.module.import_knowledge_terminal_file('kb', '/home/source.pdf', **runtime.context)
    )
    runtime.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_binding_change_prevents_persistence(runtime):
    runtime.terminal_access.side_effect = [
        ('https://old.test', {'actor': 'user'}, {}, 'chat:saved-chat'),
        ('https://new.test', {'actor': 'user'}, {}, 'chat:saved-chat'),
    ]
    result = json.loads(
        await runtime.module.import_knowledge_terminal_file('kb', '/home/source.pdf', **runtime.context)
    )
    assert 'error' in result
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_value_error_is_never_echoed(runtime):
    runtime.fetch.side_effect = ValueError('token=secret source document text')
    result = await import_url(runtime)
    assert 'secret' not in json.dumps(result)


@pytest.mark.asyncio
async def test_optional_filename_can_identify_an_unknown_extension_download(runtime):
    runtime.source.filename = 'download.bin'
    runtime.source.content_type = 'application/octet-stream'
    runtime.source.extension_unspecified = True
    result = await import_url(runtime, filename='manual.pdf')
    assert result['status'] == 'success' and result['filename'] == 'manual.pdf'


@pytest.mark.asyncio
async def test_source_provenance_drops_url_credentials_from_query_and_fragment(runtime):
    await import_url(runtime, url='https://example.test/source.pdf?token=secret#private')
    assert runtime.upload.call_args.kwargs['metadata'] == {'source_url': 'https://example.test/source.pdf'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mime,name',
    [
        ('application/pdf', 'fake.txt'),
        ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'fake.pdf'),
    ],
)
async def test_source_mime_must_match_document_extension(runtime, mime, name):
    runtime.source.content_type, runtime.source.filename = mime, name
    assert 'error' in await import_url(runtime)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_cleanup_read_reports_retained_identity_conservatively(runtime):
    runtime.process.side_effect = RuntimeError('synthetic indexing failure')
    runtime.files.get_file_by_id.side_effect = RuntimeError('database unavailable')
    result = await import_url(runtime)
    assert result['status'] == 'partial_completion' and result['file_id'] == 'new-file'
    assert result['content_saved'] is None and result['associated'] is None


@pytest.mark.asyncio
async def test_actual_native_schemas_hide_runtime_context_and_binding_preserves_types(runtime):
    import ast
    import copy
    import inspect
    import re
    from functools import cache, partial, update_wrapper
    from typing import Any, Awaitable, Callable, get_args, get_type_hints

    from langchain_core.utils.function_calling import convert_to_openai_function
    from pydantic import BaseModel, Field, create_model
    from open_webui.utils.gemini_schema import compile_gemini_schema, validate_gemini_tool_arguments

    path = MODULE.parents[1] / 'utils/tools.py'
    names = {
        'get_async_tool_function_and_apply_extra_params',
        'get_builtin_function_introspection',
        'build_builtin_tool_spec_json',
        'get_builtin_tool_spec',
        'convert_function_to_pydantic_model',
        'parse_description',
        'parse_docstring',
        'clean_properties',
        'clean_openai_tool_schema',
    }
    nodes = [
        node
        for node in ast.parse(path.read_text(encoding='utf-8')).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    ns = dict(
        __name__=__name__,
        inspect=inspect,
        Callable=Callable,
        Awaitable=Awaitable,
        get_type_hints=get_type_hints,
        get_args=get_args,
        partial=partial,
        update_wrapper=update_wrapper,
        cache=cache,
        re=re,
        copy=copy,
        Any=Any,
        BaseModel=BaseModel,
        Field=Field,
        create_model=create_model,
        JSONCodec=json,
        convert_pydantic_model_to_openai_function_spec=convert_to_openai_function,
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    for function, location in (
        (runtime.module.import_knowledge_url, 'url'),
        (runtime.module.import_knowledge_terminal_file, 'path'),
    ):
        spec = ns['get_builtin_tool_spec'](function)
        assert set(spec['parameters']['properties']) == {'knowledge_id', location, 'filename'}
        assert set(spec['parameters']['required']) == {'knowledge_id', location}
        assert location in compile_gemini_schema(spec['parameters']).parameters['properties']
        assert validate_gemini_tool_arguments(spec['parameters'], {'knowledge_id': 'kb', location: 42})
        bound = await ns['get_async_tool_function_and_apply_extra_params'](function, runtime.context)
        assert all(not name.startswith('__') for name in inspect.signature(bound).parameters)
        assert 'error' in json.loads(await bound(knowledge_id='kb', **{location: 42}))
    runtime.upload.assert_not_awaited()
