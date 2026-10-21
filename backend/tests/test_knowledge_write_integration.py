"""Real SQLite/file pipelines with deterministic storage and vector dependencies.

The subprocess sets every data path before importing models. Router functions are
compiled from their source to avoid importing audio/model download integrations.
"""

import ast
import asyncio
import errno
import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock

import pytest


ROOT = Path(__file__).parents[2]


def test_real_sqlite_knowledge_write_and_shared_reindex(tmp_path):
    data = tmp_path / 'knowledge-write'
    data.mkdir()
    env = {
        **os.environ,
        'PYTHONPATH': str(ROOT / 'backend'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUTF8': '1',
        'ENABLE_DB_MIGRATIONS': 'false',
        'WEBUI_SECRET_KEY': 'synthetic-knowledge-test-key',
        'DATA_DIR': str(data),
        'STATIC_DIR': str(data / 'static'),
        'DATABASE_URL': 'sqlite:///' + (data / 'test.db').as_posix(),
        'KNOWLEDGE_WRITE_TEST_DATA': str(data),
        'OFFLINE_MODE': 'true',
    }
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--isolated-scenario'],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    assert 'knowledge write SQLite integration passed' in result.stdout


def load_source(namespace, relative, names):
    path = ROOT / 'backend/open_webui' / relative
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in nodes} == set(names)
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


async def isolated_scenario(import_scenario=None):
    # Config imports can start migrations/copy assets; only model-required constants
    # are provided. Config.get/upsert below remain the real database implementation.
    config_module = ModuleType('open_webui.config')
    config_module.RAG_FILE_CONTENT_SEARCH_MAX_CHARS = 100_000
    config_module.DEFAULT_USER_PERMISSIONS = {}
    sys.modules[config_module.__name__] = config_module

    import fastapi
    from langchain_core.documents import Document
    from pydantic import BaseModel
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession
    from open_webui.constants import ERROR_MESSAGES
    from open_webui.internal.db import Base, async_engine, get_async_db, get_async_db_context, get_async_session
    from open_webui.models.access_grants import AccessGrants
    from open_webui.models.config import Config
    from open_webui.models.files import FileForm, FileMetadataResponse, Files
    from open_webui.models.groups import Groups
    from open_webui.models.knowledge import Knowledge, KnowledgeForm, KnowledgeResponse, Knowledges
    from open_webui.models.messages import Messages
    from open_webui.models.users import UserModel, Users
    from open_webui.retrieval.vector.main import SearchResult
    from open_webui.tools.knowledge_write import (
        knowledge_write_access,
        replace_knowledge_file_content,
        write_knowledge_file,
    )
    from open_webui.utils.access_control.files import has_access_to_file
    from open_webui.utils.misc import strict_match_mime_type

    data = Path(os.environ['KNOWLEDGE_WRITE_TEST_DATA'])
    async with async_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await Config.upsert(
        {
            'rag.file.max_size': 1,
            'rag.file.allowed_extensions': [],
            'rag.bypass_embedding_and_retrieval': False,
            'audio.stt.supported_content_types': [],
            'rag.content_extraction_engine': '',
            'rag.enable_hybrid_search': False,
        }
    )

    class Storage:
        @staticmethod
        def upload_file(file, name, tags):
            content = file.read()
            target = data / name
            target.write_bytes(content)
            return content, str(target)

        @staticmethod
        def get_file(path):
            return path

        @staticmethod
        def delete_file(path):
            Path(path).unlink(missing_ok=True)

    class Vectors:
        def __init__(self):
            self.collections = {}
            self.fail_collection = None
            self.searches = []

        def search(self, collection_name, vectors, limit):
            self.searches.append((collection_name, vectors, limit))
            query_vector = vectors[0]
            ranked = sorted(
                self.collections.get(collection_name, []),
                key=lambda entry: sum(a * b for a, b in zip(query_vector, embed_text(entry['content']))),
                reverse=True,
            )[:limit]
            return SearchResult(
                ids=[[entry['id'] for entry in ranked]],
                documents=[[entry['content'] for entry in ranked]],
                metadatas=[[dict(entry['metadata']) for entry in ranked]],
                distances=[
                    [sum(a * b for a, b in zip(query_vector, embed_text(entry['content']))) for entry in ranked]
                ],
            )

        async def query(self, collection_name, filter):
            entries = [
                entry
                for entry in self.collections.get(collection_name, [])
                if all(entry['metadata'].get(key) == value for key, value in filter.items())
            ]
            return SimpleNamespace(
                ids=[[entry['id'] for entry in entries]],
                documents=[[entry['content'] for entry in entries]],
                metadatas=[[entry['metadata'] for entry in entries]],
            )

        async def delete_collection(self, collection_name):
            self.collections.pop(collection_name, None)

        async def has_collection(self, collection_name):
            return collection_name in self.collections

        async def delete(self, collection_name, ids=None, filter=None):
            self.collections[collection_name] = [
                entry
                for entry in self.collections.get(collection_name, [])
                if not (
                    entry['id'] in ids
                    if ids is not None
                    else all(entry['metadata'].get(key) == value for key, value in (filter or {}).items())
                )
            ]

    vectors = Vectors()

    def embed_text(text):
        return [float(text.lower().count(token)) for token in ('before', 'after', '知识')]

    embedding_requests = []

    async def embedding_function(queries, prefix, user):
        embedding_requests.append((queries, prefix, user.id))
        return [embed_text(query) for query in queries]

    def save_docs(request, docs, collection_name, config, metadata, add, user):
        if collection_name == vectors.fail_collection:
            raise RuntimeError('Synthetic indexing failure')
        entries = [
            {'id': str(uuid.uuid4()), 'content': doc.page_content, 'metadata': {**doc.metadata, **metadata}}
            for doc in docs
        ]
        vectors.collections[collection_name] = (vectors.collections.get(collection_name, []) if add else []) + entries
        return True

    class Loader:
        async def aload(self, filename, content_type, path):
            return [Document(page_content=Path(path).read_text(encoding='utf-8'), metadata={})]

    async def run_in_threadpool(function, *args, **kwargs):
        return function(*args, **kwargs)

    events = SimpleNamespace(
        **{
            name: name
            for name in [
                'RETRIEVAL_CONTENT_PROCESSED',
                'RETRIEVAL_CONTENT_PROCESS_FAILED',
                'FILE_CONTENT_UPDATED',
                'FILE_DELETED',
                'KNOWLEDGE_FILE_ADDED',
            ]
        }
    )
    ns = {
        **{
            name: getattr(fastapi, name)
            for name in [
                'Request',
                'UploadFile',
                'BackgroundTasks',
                'File',
                'Form',
                'Query',
                'Depends',
                'HTTPException',
                'status',
            ]
        },
        'Optional': Optional,
        'AsyncSession': AsyncSession,
        'BaseModel': BaseModel,
        'get_verified_user': lambda: None,
        'get_async_session': get_async_session,
        'get_async_db_context': get_async_db_context,
        'get_async_db': get_async_db,
        'Files': Files,
        'Knowledges': Knowledges,
        'Groups': Groups,
        'AccessGrants': AccessGrants,
        'Config': Config,
        'FileForm': FileForm,
        'Storage': Storage,
        'Document': Document,
        'ERROR_MESSAGES': ERROR_MESSAGES,
        'EVENTS': events,
        'publish_event': AsyncMock(),
        'has_access_to_file': has_access_to_file,
        'strict_match_mime_type': strict_match_mime_type,
        'JSONCodec': json,
        'asyncio': asyncio,
        'os': os,
        'errno': errno,
        'hashlib': hashlib,
        'uuid': uuid,
        'log': logging.getLogger(__name__),
        'ASYNC_VECTOR_DB_CLIENT': vectors,
        'RETRIEVAL_CONFIG_KEYS': {'bypass': 'rag.bypass_embedding_and_retrieval'},
        'build_loader_from_config': lambda config: Loader(),
        'filter_file_metadata': lambda metadata: metadata or {},
        'filter_metadata': lambda metadata: metadata,
        'calculate_sha256_string': lambda text: hashlib.sha256(text.encode()).hexdigest(),
        'save_docs_to_vector_db': save_docs,
        'run_in_threadpool': run_in_threadpool,
        '_validate_collection_access': AsyncMock(),
        'STORAGE_LOCAL_CACHE': True,
        'STORAGE_PROVIDER': 'local',
        'knowledge_write_access': knowledge_write_access,
        'KnowledgeResponse': KnowledgeResponse,
        'FileMetadataResponse': FileMetadataResponse,
        'UserModel': UserModel,
        'get_vector_db_client': lambda: vectors,
        'RAG_EMBEDDING_QUERY_PREFIX': 'synthetic-query-prefix',
        'CHUNK_HASH_KEY': '_chunk_hash',
        'RAG_SOURCE_METADATA_KEYS': ['file_id', 'name', 'source'],
        'VIEW_FILE_DEFAULT_MAX_CHARS': 20_000,
        'VIEW_FILE_MAX_CHARS': 100_000,
    }
    load_source(ns, 'routers/retrieval.py', {'ProcessFileForm', 'has_vector_results', 'process_file'})
    load_source(
        ns,
        'routers/files.py',
        {
            '_cleanup_local_cache',
            '_media_supported_for_extraction',
            'process_uploaded_file',
            'upload_file_handler',
            'ContentForm',
            'update_file_data_content_by_id',
            'delete_file_by_id',
            '_cleanup_new_orphan_upload',
        },
    )
    # Lazy imports in the production tools reach these exact source functions.
    files_router = ModuleType('open_webui.routers.files')
    files_router.__dict__.update(ns)
    sys.modules[files_router.__name__] = files_router
    load_source(
        ns,
        'routers/knowledge.py',
        {
            'KnowledgeFileIdForm',
            'KnowledgeFilesResponse',
            '_verify_directory_in_knowledge',
            'is_external_knowledge',
            'external_knowledge_error',
            'add_file_to_knowledge_by_id',
            '_add_file_to_knowledge',
        },
    )
    knowledge_router = ModuleType('open_webui.routers.knowledge')
    knowledge_router.__dict__.update(ns)
    sys.modules[knowledge_router.__name__] = knowledge_router
    load_source(
        ns,
        'retrieval/utils.py',
        {'_content_hash', 'query_doc', 'merge_and_sort_query_results', 'query_collection', 'filter_source_metadata'},
    )
    retrieval_utils = ModuleType('open_webui.retrieval.utils')
    retrieval_utils.__dict__.update(ns)
    sys.modules[retrieval_utils.__name__] = retrieval_utils
    external_retrieval = ModuleType('open_webui.retrieval.external')
    external_retrieval.retrieve_external_knowledge = AsyncMock(side_effect=AssertionError('No external KB expected'))
    sys.modules[external_retrieval.__name__] = external_retrieval
    load_source(
        ns,
        'tools/builtin.py',
        {'view_knowledge_file', 'list_knowledge_bases', 'search_knowledge_bases', 'query_knowledge_files'},
    )
    load_source(
        ns, 'tools/knowledge_fs.py', {'_get_accessible_kb_ids', '_get_accessible_files', '_resolve_file', '_kb_cat'}
    )

    owner = await Users.insert_new_user('owner', 'Synthetic Owner', 'owner@example.test', role='user')
    actor = await Users.insert_new_user('actor', 'Synthetic Actor', 'actor@example.test', role='user')
    assert owner and actor
    base = await Knowledges.insert_new_knowledge(
        owner.id, KnowledgeForm(name='Integration KB', description='', access_grants=[])
    )
    shared = await Knowledges.insert_new_knowledge(
        owner.id, KnowledgeForm(name='Shared KB', description='', access_grants=[])
    )
    assert base and shared
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(EMBEDDING_FUNCTION=embedding_function)))

    # Attached targets beyond the default global page must paginate within scope.
    pager = await Users.insert_new_user('pager', 'Synthetic Pager', 'pager@example.test', role='user')
    paging_bases = [
        await Knowledges.insert_new_knowledge(
            pager.id,
            KnowledgeForm(name=f'Common Discovery {index:02d}', description=f'chosen {index}', access_grants=[]),
        )
        for index in range(13)
    ]
    granted = await Knowledges.insert_new_knowledge(
        actor.id,
        KnowledgeForm(
            name='Common Discovery Shared',
            description='shared read access',
            access_grants=[{'principal_type': 'user', 'principal_id': pager.id, 'permission': 'read'}],
        ),
    )
    private = await Knowledges.insert_new_knowledge(
        actor.id, KnowledgeForm(name='Common Discovery Private', description='unreadable', access_grants=[])
    )
    async with get_async_db_context() as db:
        for index, kb in enumerate([*paging_bases, granted, private]):
            await db.execute(update(Knowledge).where(Knowledge.id == kb.id).values(updated_at=50_000 - index))
        await db.commit()
    paging_ids = [paging_bases[11].id, paging_bases[12].id, granted.id, private.id]
    paging_context = {
        '__request__': request,
        '__user__': pager.model_dump(),
        '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': paging_ids},
    }
    expected_ids = [paging_bases[11].id, paging_bases[12].id, granted.id]
    for function, query in [('list_knowledge_bases', {}), ('search_knowledge_bases', {'query': 'Common Discovery'})]:
        scoped = json.loads(await ns[function](**query, **paging_context))
        assert [kb['id'] for kb in scoped] == expected_ids, scoped
        assert scoped[-1]['write_access'] is False
        next_page = json.loads(await ns[function](**query, count=1, skip=1, **paging_context))
        assert [kb['id'] for kb in next_page] == [paging_bases[12].id]
        for scope_context in (
            None,
            {'enabled': False, 'knowledge_ids': paging_ids},
            {'enabled': True, 'knowledge_ids': []},
        ):
            unscoped = json.loads(
                await ns[function](
                    **query,
                    count=3,
                    skip=1,
                    **{
                        **paging_context,
                        '__knowledge_write_context__': scope_context,
                    },
                )
            )
            assert [kb['id'] for kb in unscoped] == [kb.id for kb in paging_bases[1:4]]
    searched = json.loads(await ns['search_knowledge_bases'](query='chosen 12', **paging_context))
    assert [kb['id'] for kb in searched] == [paging_bases[12].id]

    scoped_filter = {'user_id': pager.id, 'group_ids': [], 'knowledge_ids': paging_ids}
    scoped_model_page = await Knowledges.search_knowledge_bases(pager.id, filter=scoped_filter, skip=1, limit=1)
    assert scoped_model_page.total == 3 and [kb.id for kb in scoped_model_page.items] == [paging_bases[12].id]
    private_only = await Knowledges.search_knowledge_bases(
        pager.id, filter={**scoped_filter, 'knowledge_ids': [private.id]}
    )
    assert private_only.total == 0 and private_only.items == []
    empty_scope = await Knowledges.search_knowledge_bases(pager.id, filter={**scoped_filter, 'knowledge_ids': []})
    assert empty_scope.total == 0 and empty_scope.items == []
    ordered_scope = await Knowledges.search_knowledge_bases(
        pager.id, filter={**scoped_filter, 'order_by': 'name', 'direction': 'desc'}
    )
    assert ordered_scope.total == 3 and [kb.id for kb in ordered_scope.items] == list(reversed(expected_ids))
    unscoped_model_page = await Knowledges.search_knowledge_bases(
        pager.id, filter={'user_id': pager.id, 'group_ids': []}, skip=1, limit=3
    )
    assert unscoped_model_page.total == 14 and [kb.id for kb in unscoped_model_page.items] == [
        kb.id for kb in paging_bases[1:4]
    ]

    context = {
        '__request__': request,
        '__user__': owner.model_dump(),
        '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': [base.id]},
    }
    result = json.loads(await write_knowledge_file(base.id, 'draft', 'before 知识😀', **context))
    assert result['status'] == 'success', result
    file_id = result['file_id']
    file = await Files.get_file_by_id(file_id)
    original = Path(file.path).read_bytes()
    assert original == 'before 知识😀'.encode()
    assert await Knowledges.has_file(base.id, file_id)
    listing = json.loads(await ns['list_knowledge_bases'](__request__=request, __user__=owner.model_dump()))
    assert any(item['id'] == base.id and item['write_access'] for item in listing)
    viewed = json.loads(await ns['view_knowledge_file'](file_id, __request__=request, __user__=owner.model_dump()))
    assert viewed['content'] == 'before 知识😀'
    attached = [{'type': 'collection', 'id': base.id}]
    assert await ns['_kb_cat']([file_id], set(), owner.model_dump(), attached) == viewed['content']

    async def retrieve(query, knowledge_id):
        chunks = json.loads(
            await ns['query_knowledge_files'](
                query,
                knowledge_ids=[knowledge_id],
                __request__=request,
                __user__=owner.model_dump(),
            )
        )
        assert isinstance(chunks, list), chunks
        return chunks

    initial_chunks = await retrieve('before 知识', base.id)
    assert [chunk['content'] for chunk in initial_chunks] == ['before 知识😀']
    assert initial_chunks[0]['file_id'] == file_id and initial_chunks[0]['distance'] > 0
    assert embedding_requests[-1] == (['before 知识'], 'synthetic-query-prefix', owner.id)
    assert vectors.searches[-1][0] == base.id
    async with get_async_db_context() as db:
        await ns['add_file_to_knowledge_by_id'](
            request, shared.id, ns['KnowledgeFileIdForm'](file_id=file_id), user=owner, db=db
        )
    edit = json.loads(
        await replace_knowledge_file_content(
            base.id,
            file_id,
            operations=[
                {
                    'action': 'replace_range',
                    'start': 0,
                    'end': 6,
                    'content': 'after',
                    'expected': viewed['content'][:6],
                },
                {
                    'action': 'replace_range',
                    'start': len(viewed['content']),
                    'end': len(viewed['content']),
                    'content': '!',
                },
            ],
            **context,
        )
    )
    assert edit['status'] == 'success', edit
    assert Path(file.path).read_bytes() == original
    for kb in (base, shared):
        content = (await vectors.query(kb.id, {'file_id': file_id})).documents[0]
        assert content == ['after 知识😀!'], content
        assert not any('before' in text for text in content)
        for query in ('after 知识', 'before 知识'):
            updated_chunks = await retrieve(query, kb.id)
            assert [chunk['content'] for chunk in updated_chunks] == ['after 知识😀!']
            assert all('before' not in chunk['content'] and chunk['file_id'] == file_id for chunk in updated_chunks)
            assert vectors.searches[-1][0] == kb.id
    assert await ns['_kb_cat']([file_id], set(), owner.model_dump(), attached) == 'after 知识😀!'

    # Existing binary documents expose editable extracted retrieval text; upload stays intact.
    pdf_path = data / 'original.pdf'
    pdf_bytes = b'%PDF-1.4\nsynthetic original binary\x00\xff'
    pdf_path.write_bytes(pdf_bytes)
    pdf = await Files.insert_new_file(
        owner.id,
        FileForm(
            id='pdf',
            filename='original.pdf',
            path=str(pdf_path),
            data={'content': 'PDF extracted before', 'status': 'completed'},
            meta={'content_type': 'application/pdf'},
        ),
    )
    await Knowledges.add_file_to_knowledge_by_id(base.id, pdf.id, owner.id)
    edited = json.loads(await replace_knowledge_file_content(base.id, pdf.id, content='PDF extracted after', **context))
    assert edited['status'] == 'success', edited
    assert pdf_path.read_bytes() == pdf_bytes
    assert (await vectors.query(base.id, {'file_id': pdf.id})).documents == [['PDF extracted after']]
    pdf_chunks = await retrieve('PDF extracted after', base.id)
    assert any(chunk['file_id'] == pdf.id and chunk['content'] == 'PDF extracted after' for chunk in pdf_chunks)
    assert all('PDF extracted before' not in chunk['content'] for chunk in pdf_chunks)

    # A KB owner cannot launder write access to another owner's read-only file.
    actor_kb = await Knowledges.insert_new_knowledge(
        actor.id, KnowledgeForm(name='Actor KB', description='', access_grants=[])
    )
    await Knowledges.add_file_to_knowledge_by_id(actor_kb.id, file_id, actor.id)
    assert not await has_access_to_file(file_id, 'write', actor)
    search_count = len(vectors.searches)
    unreadable_chunks = json.loads(
        await ns['query_knowledge_files'](
            'after 知识', knowledge_ids=[base.id], __request__=request, __user__=actor.model_dump()
        )
    )
    assert unreadable_chunks == [] and len(vectors.searches) == search_count
    actor_context = {
        **context,
        '__user__': actor.model_dump(),
        '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': [actor_kb.id]},
    }
    denied = json.loads(
        await replace_knowledge_file_content(actor_kb.id, file_id, content='laundered', **actor_context)
    )
    assert 'error' in denied and (await Files.get_file_by_id(file_id)).data['content'] == 'after 知识😀!'

    # Existing pipeline saves SQL before reindexing all referencing KBs.
    vectors.fail_collection = shared.id
    partial = json.loads(await replace_knowledge_file_content(base.id, file_id, content='saved partial', **context))
    assert partial['status'] == 'partial_completion' and partial['content_saved'] and not partial['indexed'], partial
    assert (await Files.get_file_by_id(file_id)).data['content'] == 'saved partial'
    assert Path(file.path).read_bytes() == original

    # The configured bypass intentionally saves text without generating vectors.
    await Config.upsert({'rag.bypass_embedding_and_retrieval': True})
    bypassed = json.loads(
        await replace_knowledge_file_content(base.id, pdf.id, content='PDF text saved with bypass', **context)
    )
    assert bypassed['status'] == 'success' and bypassed['content_saved'], bypassed
    assert bypassed['indexed'] is False and bypassed['indexing_skipped'] is True, bypassed
    assert (await Files.get_file_by_id(pdf.id)).data['content'] == 'PDF text saved with bypass'
    assert pdf_path.read_bytes() == pdf_bytes
    assert not any(
        entry['content'] == 'PDF text saved with bypass'
        for entries in vectors.collections.values()
        for entry in entries
    )
    if import_scenario is not None:
        await import_scenario(ns, request, owner, base, data, vectors)
    await async_engine.dispose()
    print('knowledge write SQLite integration passed')


if __name__ == '__main__' and '--isolated-scenario' in sys.argv:
    asyncio.run(isolated_scenario())
