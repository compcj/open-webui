"""Knowledge mutations with synthetic request context and isolated pipeline dependencies."""

import importlib.util
import ast
import json
import inspect
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from typing import Awaitable, Callable, Optional, get_args, get_type_hints
from functools import partial, update_wrapper

import pytest
from fastapi import HTTPException


MODULE = Path(__file__).parents[1] / 'open_webui/tools/knowledge_write.py'


@pytest.fixture
def runtime(monkeypatch):
    assert MODULE.exists(), 'Knowledge write tools have not been implemented'
    spec = importlib.util.spec_from_file_location('knowledge_write_under_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = object()

    @asynccontextmanager
    async def db_context():
        yield db

    base = SimpleNamespace(id='kb', user_id='user', name='Example', meta={})
    file = SimpleNamespace(
        id='new-file', user_id='user', filename='draft.md', data={'status': 'completed', 'content': 'original'}, meta={}
    )
    state = SimpleNamespace(
        module=module,
        db=db,
        base=base,
        file=file,
        root=[],
        directories=[],
        members=[],
        files=SimpleNamespace(get_file_by_id=AsyncMock(side_effect=lambda *a, **k: file)),
        knowledge=SimpleNamespace(),
        grant=AsyncMock(return_value=False),
        file_access=AsyncMock(return_value=False),
        upload=AsyncMock(),
        update=AsyncMock(),
        add=AsyncMock(),
        delete=AsyncMock(),
        config=AsyncMock(return_value=None),
    )
    state.knowledge.get_knowledge_by_id = AsyncMock(side_effect=lambda *a, **k: base)
    state.knowledge.get_files_with_directory_ids = AsyncMock(side_effect=lambda *a, **k: state.root)
    state.knowledge.get_directories = AsyncMock(side_effect=lambda *a, **k: state.directories)
    state.knowledge.get_knowledges_by_file_id = AsyncMock(side_effect=lambda *a, **k: state.members)

    async def upload(request, **kwargs):
        assert kwargs['db'] is db and kwargs['user'].id == 'user'
        assert kwargs['process'] is True and kwargs['process_in_background'] is False
        text = (await kwargs['file'].read()).decode('utf-8')
        file.data = {'status': 'completed', 'content': text}
        file.filename = kwargs['file'].filename
        return {'id': file.id, 'data': {'status': 'pending'}}

    async def add(request, id, form_data, **kwargs):
        assert kwargs['db'] is db
        state.members = [base]

    async def update(request, id, form_data, **kwargs):
        assert kwargs['db'] is db
        file.data['content'] = form_data.content
        return {'content': form_data.content}

    state.upload.side_effect = upload
    state.add.side_effect = add
    state.update.side_effect = update
    modules = {
        'open_webui.internal.db': {'get_async_db_context': db_context},
        'open_webui.models.config': {'Config': SimpleNamespace(get=state.config)},
        'open_webui.models.files': {'Files': state.files},
        'open_webui.models.knowledge': {'Knowledges': state.knowledge},
        'open_webui.models.access_grants': {'AccessGrants': SimpleNamespace(has_access=state.grant)},
        'open_webui.models.users': {'UserModel': lambda **kwargs: SimpleNamespace(**kwargs)},
        'open_webui.models.notes': {'Notes': SimpleNamespace()},
        'open_webui.utils.access_control.files': {'has_access_to_file': state.file_access},
        'open_webui.routers.files': {
            'upload_file_handler': state.upload,
            'update_file_data_content_by_id': state.update,
            'ContentForm': lambda **kwargs: SimpleNamespace(**kwargs),
            'delete_file_by_id': state.delete,
        },
        'open_webui.routers.knowledge': {
            'add_file_to_knowledge_by_id': state.add,
            'KnowledgeFileIdForm': lambda **kwargs: SimpleNamespace(**kwargs),
        },
    }
    for name, attrs in modules.items():
        fake = ModuleType(name)
        fake.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, fake)
    state.context = {
        '__request__': SimpleNamespace(),
        '__user__': {'id': 'user', 'role': 'user'},
        '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': ['kb']},
    }
    return state


async def create(state, **kwargs):
    return json.loads(
        await state.module.write_knowledge_file(
            knowledge_id='kb',
            filename=kwargs.pop('filename', 'draft'),
            content=kwargs.pop('content', 'hello'),
            **state.context,
            **kwargs,
        )
    )


async def edit(state, **kwargs):
    return json.loads(
        await state.module.replace_knowledge_file_content(
            knowledge_id='kb', file_id='new-file', **state.context, **kwargs
        )
    )


@pytest.mark.asyncio
async def test_create_uploads_utf8_then_links_and_rereads_state(runtime):
    result = await create(runtime, content='知识内容')
    assert result['status'] == 'success' and result['file_id'] == 'new-file'
    assert runtime.file.filename == 'draft.md' and runtime.file.data['content'] == '知识内容'
    assert runtime.upload.await_count == runtime.add.await_count == 1
    assert runtime.files.get_file_by_id.await_count >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'filename',
    [
        '../a.md',
        'a/b.txt',
        'a\\b.md',
        'CON.md',
        'aux',
        'a:.md',
        'x\x00.md',
        '.md',
        'a.pdf',
        'a.md ',
        'a.',
        'a\x85.md',
        'a\u202e.md',
        'CONIN$.txt',
        'NUL .txt',
        'COM¹.txt',
    ],
)
async def test_create_rejects_invalid_root_filenames(runtime, filename):
    assert 'error' in await create(runtime, filename=filename)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('content', ['', ' \n', None, 42])
async def test_create_rejects_empty_or_nonstring_content(runtime, content):
    assert 'error' in await create(runtime, content=content)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_respects_configured_utf8_byte_limit(runtime):
    runtime.config.return_value = 1
    assert 'error' in await create(runtime, content='知' * 400_000)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_root_file_or_directory_refuses_overwrite(runtime):
    runtime.root = [(SimpleNamespace(filename='Draft.md'), None)]
    assert 'error' in await create(runtime)
    runtime.root = []
    runtime.directories = [SimpleNamespace(name='draft.md')]
    assert 'error' in await create(runtime)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('denial', ['disabled', 'out_of_scope', 'read_only', 'external', 'no_request'])
async def test_create_checks_context_scope_and_live_kb_acl(runtime, denial):
    if denial == 'disabled':
        runtime.context.pop('__knowledge_write_context__')
    elif denial == 'out_of_scope':
        runtime.context['__knowledge_write_context__']['knowledge_ids'] = ['other']
    elif denial == 'read_only':
        runtime.base.user_id = 'other'
    elif denial == 'external':
        runtime.base.meta = {'source': 'external'}
    else:
        runtime.context['__request__'] = None
    assert 'error' in await create(runtime)
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_kb_grant_allows_creation_but_is_rechecked_after_approval(runtime):
    runtime.base.user_id = 'other'
    runtime.grant.return_value = True
    assert (await create(runtime))['status'] == 'success'
    runtime.grant.return_value = False
    assert 'error' in await create(runtime, filename='second')
    assert runtime.upload.await_count == 1


@pytest.mark.asyncio
async def test_unscoped_writes_still_require_writable_existing_kb(runtime):
    runtime.context['__knowledge_write_context__']['knowledge_ids'] = []
    assert (await create(runtime))['status'] == 'success'
    runtime.knowledge.get_knowledge_by_id.return_value = None
    runtime.knowledge.get_knowledge_by_id.side_effect = None
    assert 'error' in await create(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['failed_status', 'missing_content', 'missing_membership'])
async def test_create_detects_swallowed_processing_or_link_failure_and_cleans_only_orphan(runtime, failure):
    original_upload = runtime.upload.side_effect

    async def upload(*args, **kwargs):
        result = await original_upload(*args, **kwargs)
        if failure == 'failed_status':
            runtime.file.data['status'] = 'failed'
        elif failure == 'missing_content':
            runtime.file.data.pop('content')
        return result

    runtime.upload.side_effect = upload
    if failure == 'missing_membership':
        runtime.add.side_effect = None
    assert 'error' in await create(runtime)
    runtime.delete.assert_awaited_once()
    if failure != 'missing_membership':
        runtime.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_failure_preserves_any_associated_file(runtime):
    runtime.members = [SimpleNamespace(id='shared-kb')]
    runtime.add.side_effect = HTTPException(500, 'link failed')
    assert 'error' in await create(runtime)
    runtime.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_replace_ranges_use_original_offsets_and_support_eof_append(runtime):
    runtime.members = [runtime.base]
    runtime.file.data['content'] = 'one two'
    result = await edit(
        runtime,
        operations=[
            {'action': 'replace_range', 'start': 4, 'end': 7, 'content': 'three', 'expected': 'two'},
            {'action': 'replace_range', 'start': 0, 'end': 3, 'content': '1', 'expected': 'one'},
            {'action': 'replace_range', 'start': 7, 'end': 7, 'content': '!'},
        ],
    )
    assert result['status'] == 'success'
    assert runtime.file.data['content'] == '1 three!'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    [
        {'start': True, 'end': 2, 'content': 'x', 'expected': 'or'},
        {'start': 0, 'end': 2, 'content': 'x'},
        {'start': 0, 'end': 2, 'content': 'x', 'expected': 'stale'},
        {'start': -1, 'end': 2, 'content': 'x', 'expected': ''},
        {'start': 0, 'end': 99, 'content': 'x', 'expected': 'original'},
        {'start': 1.5, 'end': 2, 'content': 'x', 'expected': ''},
    ],
)
async def test_invalid_ranges_never_mutate(runtime, operation):
    runtime.members = [runtime.base]
    assert 'error' in await edit(runtime, operations=[{'action': 'replace_range', **operation}])
    runtime.update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operations',
    [
        [],
        [{'action': 'replace_range', 'start': 0, 'end': 0, 'content': 'a'}] * 2,
        [
            {'action': 'replace_range', 'start': 0, 'end': 2, 'content': 'a', 'expected': 'or'},
            {'action': 'replace_range', 'start': 1, 'end': 3, 'content': 'b', 'expected': 'ri'},
        ],
    ],
)
async def test_empty_duplicate_and_overlapping_operations_never_mutate(runtime, operations):
    runtime.members = [runtime.base]
    assert 'error' in await edit(runtime, operations=operations)
    runtime.update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{}, {'content': 'x', 'operations': []}, {'content': '  '}])
async def test_edit_requires_exactly_one_nonempty_content_source(runtime, kwargs):
    runtime.members = [runtime.base]
    assert 'error' in await edit(runtime, **kwargs)
    runtime.update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['not_member', 'file_read_only'])
async def test_edit_checks_membership_and_file_acl(runtime, failure):
    runtime.members = [runtime.base]
    if failure == 'not_member':
        runtime.members = []
    elif failure == 'file_read_only':
        runtime.file.user_id = 'other'
    assert 'error' in await edit(runtime, content='updated')
    runtime.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_can_change_pdf_extracted_text_without_replacing_upload(runtime):
    runtime.members = [runtime.base]
    runtime.file.filename = 'source.pdf'
    runtime.file.path = '/original/source.pdf'
    assert (await edit(runtime, content='updated extracted text'))['status'] == 'success'
    assert runtime.file.path == '/original/source.pdf'
    runtime.upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'replacement',
    [
        {'content': 'updated'},
        {
            'operations': [
                {'action': 'replace_range', 'start': 0, 'end': 8, 'content': 'updated', 'expected': 'original'}
            ]
        },
    ],
)
async def test_changed_text_returns_saved_but_unindexed_partial_completion(runtime, replacement):
    runtime.members = [runtime.base]
    original_update = runtime.update.side_effect

    async def update(*args, **kwargs):
        await original_update(*args, **kwargs)
        raise HTTPException(500, 'Index unavailable')

    runtime.update.side_effect = update
    result = await edit(runtime, **replacement)
    assert result['status'] == 'partial_completion'
    assert result['content_saved'] is True and result['indexed'] is False and result['file_id'] == runtime.file.id
    assert 'read' in result['error'].lower()
    runtime.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_failure_before_save_is_not_reported_as_partial(runtime):
    runtime.members = [runtime.base]
    runtime.update.side_effect = HTTPException(500, 'unavailable')
    result = await edit(runtime, content='updated')
    assert 'error' in result and result.get('content_saved') is not True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,detail', [(404, 'Access denied before save'), (500, 'Database unavailable before save')]
)
@pytest.mark.parametrize(
    'replacement',
    [
        {'content': 'original'},
        {
            'operations': [
                {'action': 'replace_range', 'start': 0, 'end': 8, 'content': 'original', 'expected': 'original'}
            ]
        },
    ],
)
async def test_unchanged_text_preserves_original_pre_save_failure(runtime, status, detail, replacement):
    runtime.members = [runtime.base]
    runtime.update.side_effect = HTTPException(status, detail)
    result = await edit(runtime, **replacement)
    assert result == {'error': detail, 'file_id': runtime.file.id}
    assert runtime.file.data['content'] == 'original'
    runtime.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_with_configured_retrieval_bypass_does_not_claim_indexed(runtime):
    runtime.members = [runtime.base]
    runtime.config.side_effect = lambda key, default=None: key == 'rag.bypass_embedding_and_retrieval'
    result = await edit(runtime, content='saved without vector indexing')
    assert result['status'] == 'success' and result['content_saved'] is True
    assert result['indexed'] is False and result['indexing_skipped'] is True
    assert runtime.file.data['content'] == 'saved without vector indexing'


@pytest.mark.asyncio
async def test_live_discovery_write_access_obeys_acl_and_external_constraints(runtime):
    assert await runtime.module.knowledge_write_access(runtime.base, runtime.context['__user__'])
    runtime.base.user_id = 'other'
    assert not await runtime.module.knowledge_write_access(runtime.base, runtime.context['__user__'])
    runtime.grant.return_value = True
    assert await runtime.module.knowledge_write_access(runtime.base, runtime.context['__user__'])
    runtime.base.meta = {'source': 'external'}
    assert not await runtime.module.knowledge_write_access(runtime.base, {'id': 'admin', 'role': 'admin'})


def discovery_namespace(runtime, monkeypatch):
    root = MODULE.parents[1]
    runtime.base.description = ''
    runtime.base.updated_at = 1
    runtime.discovery_bases = [runtime.base]

    async def search(user_id, filter, skip=0, limit=30):
        items = runtime.discovery_bases
        if 'knowledge_ids' in filter:
            items = [base for base in items if base.id in filter['knowledge_ids']]
        query = filter.get('query', '').lower()
        if query:
            items = [base for base in items if query in base.name.lower() or query in base.description.lower()]
        return SimpleNamespace(items=items[skip : skip + limit])

    runtime.knowledge.search_knowledge_bases = AsyncMock(side_effect=search)
    runtime.knowledge.get_files_by_id = AsyncMock(return_value=[runtime.file])
    groups = SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[]))
    fake = ModuleType('open_webui.models.groups')
    fake.Groups = groups
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    ns = {
        'Request': object,
        'Optional': Optional,
        'JSONCodec': json,
        'Groups': groups,
        'knowledge_write_access': runtime.module.knowledge_write_access,
        'log': SimpleNamespace(exception=lambda *a: None),
    }
    path = root / 'tools/builtin.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name
        in {
            'list_knowledge_bases',
            'search_knowledge_bases',
            'list_knowledge',
        }
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns


@pytest.mark.asyncio
async def test_builtin_discovery_reports_live_write_access_and_respects_write_scope(runtime, monkeypatch):
    ns = discovery_namespace(runtime, monkeypatch)
    context = {key: value for key, value in runtime.context.items() if key != '__knowledge_write_context__'}
    for name, params in [('list_knowledge_bases', {}), ('search_knowledge_bases', {'query': 'Example'})]:
        result = json.loads(await ns[name](**params, **context))
        assert result[0]['write_access'] is True
        runtime.base.meta = {'source': 'external'}
        result = json.loads(await ns[name](**params, **context))
        assert result[0]['write_access'] is False
        runtime.base.meta = {}
        scoped = {**runtime.context, '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': ['other']}}
        assert json.loads(await ns[name](**params, **scoped)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('name,params', [('list_knowledge_bases', {}), ('search_knowledge_bases', {'query': 'Common'})])
async def test_scoped_discovery_filters_before_default_page_and_scoped_pagination(runtime, monkeypatch, name, params):
    ns = discovery_namespace(runtime, monkeypatch)
    runtime.discovery_bases = [
        SimpleNamespace(
            id=f'kb-{index}', name=f'Common KB {index}', description='', user_id='user', meta={}, updated_at=1
        )
        for index in range(13)
    ]
    runtime.discovery_bases[11].description = 'chosen A'
    runtime.discovery_bases[12].description = 'chosen B'
    context = {**runtime.context, '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': ['kb-11', 'kb-12']}}
    result = json.loads(await ns[name](**params, **context))
    assert [item['id'] for item in result] == ['kb-11', 'kb-12']
    passed_filter = runtime.knowledge.search_knowledge_bases.call_args.kwargs['filter']
    assert passed_filter['knowledge_ids'] == ['kb-11', 'kb-12']
    page = json.loads(await ns[name](**params, count=1, skip=1, **context))
    assert [item['id'] for item in page] == ['kb-12']
    if name == 'search_knowledge_bases':
        matched = json.loads(await ns[name](query='chosen B', **context))
        assert [item['id'] for item in matched] == ['kb-12']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'name,params', [('list_knowledge_bases', {}), ('search_knowledge_bases', {'query': 'Example'})]
)
@pytest.mark.parametrize(
    'write_context', [None, {'enabled': False, 'knowledge_ids': ['other']}, {'enabled': True, 'knowledge_ids': []}]
)
async def test_discovery_without_enabled_nonempty_scope_keeps_existing_filters(
    runtime, monkeypatch, name, params, write_context
):
    ns = discovery_namespace(runtime, monkeypatch)
    context = {**runtime.context, '__knowledge_write_context__': write_context}
    result = json.loads(await ns[name](**params, **context))
    assert [item['id'] for item in result] == ['kb']
    passed_filter = runtime.knowledge.search_knowledge_bases.call_args.kwargs['filter']
    assert passed_filter == {'query': params.get('query', ''), 'user_id': 'user', 'group_ids': []}


@pytest.mark.asyncio
async def test_list_knowledge_reports_write_access_and_can_target_unattached_writable_base(runtime, monkeypatch):
    ns = discovery_namespace(runtime, monkeypatch)
    result = json.loads(
        await ns['list_knowledge'](
            knowledge_id='kb',
            __model_knowledge__=[],
            **{**runtime.context, '__knowledge_write_context__': {'enabled': True, 'knowledge_ids': []}},
        )
    )
    assert result['knowledge_bases'][0]['write_access'] is True
    assert result['knowledge_bases'][0]['files'][0]['id'] == 'new-file'


@pytest.mark.asyncio
async def test_kb_exec_listing_reports_live_write_access(runtime):
    import logging

    path = MODULE.with_name('knowledge_fs.py')
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_kb_ls']
    runtime.knowledge.get_directories = AsyncMock(return_value=[])
    ns = {
        'knowledge_write_access': runtime.module.knowledge_write_access,
        'log': logging.getLogger(__name__),
        '_get_accessible_kb_ids': AsyncMock(return_value=[('kb', 'Example', '')]),
        '_get_accessible_files': AsyncMock(return_value=[]),
        '_sort_files': lambda files, flags: files,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    result = await ns['_kb_ls']([], set(), runtime.context['__user__'], None)
    assert 'write_access: true' in result
    runtime.base.meta = {'source': 'external'}
    result = await ns['_kb_ls']([], set(), runtime.context['__user__'], None)
    assert 'write_access: false' in result


@pytest.mark.asyncio
async def test_native_binding_preserves_invalid_argument_types_for_write_validation(runtime):
    path = MODULE.parents[1] / 'utils/tools.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_async_tool_function_and_apply_extra_params'
    ]
    ns = dict(
        inspect=inspect,
        Callable=Callable,
        Awaitable=Awaitable,
        get_type_hints=get_type_hints,
        get_args=get_args,
        partial=partial,
        update_wrapper=update_wrapper,
    )
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    bound = await ns['get_async_tool_function_and_apply_extra_params'](
        runtime.module.write_knowledge_file, runtime.context
    )
    result = json.loads(await bound(knowledge_id='kb', filename='draft', content=42))
    assert 'error' in result
    runtime.upload.assert_not_awaited()

    async def existing_tool(content: str):
        return content

    legacy = await ns['get_async_tool_function_and_apply_extra_params'](existing_tool, {})
    assert await legacy(content=42) == '42'


@pytest.mark.asyncio
async def test_real_builtin_schema_and_binding_keep_runtime_context_private(runtime):
    import copy
    import re
    from functools import cache
    from typing import Any

    from langchain_core.utils.function_calling import convert_to_openai_function
    from pydantic import BaseModel, Field, create_model
    from open_webui.utils.gemini_schema import compile_gemini_schema, validate_gemini_tool_arguments

    path = MODULE.parents[1] / 'utils/tools.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
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
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
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
    for function in (runtime.module.write_knowledge_file, runtime.module.replace_knowledge_file_content):
        spec = ns['get_builtin_tool_spec'](function)
        assert all(not key.startswith('__') for key in spec['parameters']['properties'])
        assert {'knowledge_id'} <= set(spec['parameters']['required'])
        compiled = compile_gemini_schema(spec['parameters'])
        assert 'knowledge_id' in compiled.parameters['properties']
        valid = (
            {'knowledge_id': 'kb', 'filename': 'draft', 'content': 'schema validated'}
            if function.__name__ == 'write_knowledge_file'
            else {'knowledge_id': 'kb', 'file_id': 'new-file', 'content': 'schema updated'}
        )
        assert validate_gemini_tool_arguments(spec['parameters'], valid) is None
        assert validate_gemini_tool_arguments(spec['parameters'], {**valid, 'content': 42})
        bound = await ns['get_async_tool_function_and_apply_extra_params'](
            function, runtime.context, ns['get_builtin_function_introspection'](function)
        )
        assert '__knowledge_write_context__' not in inspect.signature(bound).parameters
        if function.__name__ != 'write_knowledge_file':
            runtime.members = [runtime.base]
        assert json.loads(await bound(**valid))['status'] == 'success'
