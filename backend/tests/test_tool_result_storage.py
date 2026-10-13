"""Tool-result storage transactions and exact authenticated paging on isolated SQLite."""

import ast
import asyncio
import importlib
import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import JSON, Column, Text, Table, create_engine, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).parents[1]


@pytest_asyncio.fixture
async def database(tmp_path):
    assert (ROOT / 'open_webui/utils/tool_result_store.py').exists(), 'Tool-result store is not implemented'
    base = declarative_base()
    chat = Table(
        'chat',
        base.metadata,
        Column('id', Text, primary_key=True),
        Column('chat', JSON),
        Column('current_message_id', Text),
        Column('user_id', Text),
    )
    message = Table(
        'chat_message',
        base.metadata,
        Column('id', Text, primary_key=True),
        Column('chat_id', Text),
        Column('role', Text),
        Column('parent_id', Text),
        Column('content', JSON),
        Column('output', JSON),
        Column('meta', JSON),
    )
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'tools.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            yield session

    db_module = ModuleType('open_webui.internal.db')
    db_module.Base, db_module.get_async_db_context = base, context
    package = ModuleType('open_webui')
    package.__path__ = [str(ROOT / 'open_webui')]
    models = ModuleType('open_webui.models')
    models.__path__ = [str(ROOT / 'open_webui/models')]
    utils = ModuleType('open_webui.utils')
    utils.__path__ = [str(ROOT / 'open_webui/utils')]
    modules = {
        'open_webui': package,
        'open_webui.internal.db': db_module,
        'open_webui.models': models,
        'open_webui.utils': utils,
    }
    env = ModuleType('open_webui.env')
    env.WEBUI_SECRET_KEY = 'synthetic-tool-result-cursor-key'
    modules['open_webui.env'] = env
    for name in (
        'open_webui.models.chat_context_checkpoints',
        'open_webui.models.chat_tool_results',
        'open_webui.models.chat_tool_result_selections',
        'open_webui.utils.tool_result_store',
        'open_webui.utils.tool_result_sources',
    ):
        modules[name] = None
    with patch.dict(sys.modules, modules):
        for name in list(modules):
            if modules[name] is None:
                del sys.modules[name]
        model = importlib.import_module('open_webui.models.chat_tool_results')
        selection_model = importlib.import_module('open_webui.models.chat_tool_result_selections')
        store_module = importlib.import_module('open_webui.utils.tool_result_store')
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        original = ('研究结果 {"value":42}\n' * 100) + 'NEEDLE evidence\n' + ('尾部证据 ' * 200)
        raw = {
            'u': {'id': 'u', 'role': 'user', 'content': 'question', 'parentId': None},
            'a': {
                'id': 'a',
                'role': 'assistant',
                'content': '',
                'parentId': 'u',
                'output': [
                    {
                        'id': 'out',
                        'type': 'function_call_output',
                        'call_id': 'call',
                        'output': [{'type': 'input_text', 'text': original}],
                        'tool_result': {'batch_id': 'batch', 'name': 'search', 'kind': 'native'},
                    }
                ],
            },
            'leaf': {'id': 'leaf', 'role': 'user', 'content': 'next', 'parentId': 'a'},
            'sibling': {'id': 'sibling', 'role': 'assistant', 'content': 'other', 'parentId': 'u'},
        }
        async with sessions() as session:
            await session.execute(
                chat.insert().values(
                    id='chat', user_id='owner', chat={'history': {'messages': raw}}, current_message_id='leaf'
                )
            )
            await session.execute(
                chat.insert().values(
                    id='other', user_id='other', chat={'history': {'messages': raw}}, current_message_id='leaf'
                )
            )
            await session.commit()

        accessible_users = {'member'}

        async def accessible(chat_id, user):
            async with sessions() as session:
                row = (await session.execute(select(chat).where(chat.c.id == chat_id))).mappings().first()
                return (
                    SimpleNamespace(**row)
                    if row and (row['user_id'] == user.id or (chat_id == 'chat' and user.id in accessible_users))
                    else None
                )

        chats_module = ModuleType('open_webui.models.chats')
        chats_module.Chats = SimpleNamespace(get_accessible_chat_by_id=accessible)
        with patch.dict(sys.modules, {'open_webui.models.chats': chats_module}):
            yield SimpleNamespace(
                store=store_module.get_tool_result_store(),
                module=store_module,
                model=model,
                selections=selection_model.get_tool_selection_store(),
                selection_model=selection_model,
                sessions=sessions,
                chat=chat,
                message=message,
                raw=raw,
                text=original,
                accessible_users=accessible_users,
            )
        await engine.dispose()


def descriptor(db):
    return {
        'message_id': 'a',
        'output_id': 'out',
        'call_id': 'call',
        'batch_id': 'batch',
        'name': 'search',
        'kind': 'native',
        'text': db.text,
    }


async def edit(db, message_id, **updates):
    db.raw[message_id] = {**db.raw.get(message_id, {'id': message_id}), **updates}
    async with db.sessions() as session:
        await session.execute(
            db.chat.update().where(db.chat.c.id == 'chat').values(chat={'history': {'messages': db.raw}})
        )
        await session.commit()


@pytest.mark.asyncio
async def test_source_identity_idempotence_and_branch_validation(database):
    db = database
    data = descriptor(db)
    result = await db.store.save_result('chat', data, branch_id='leaf')
    assert result and result['text'] == db.text and result['source_hash']
    assert (await db.store.save_result('chat', data, branch_id='leaf'))['id'] == result['id']
    assert (await db.store.save_result('chat', data, branch_id='a'))['id'] == result['id']
    assert await db.store.save_result('chat', data, branch_id='sibling') is None
    assert await db.store.save_result('chat', {**data, 'text': 'forged'}, branch_id='leaf') is None
    assert await db.store.save_result('chat', {**data, 'call_id': 'forged'}, branch_id='leaf') is None
    assert await db.store.save_result('missing', data, branch_id='leaf') is None


@pytest.mark.asyncio
async def test_context_cache_isolated_and_invalidated_by_source_or_ancestry(database):
    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    key = dict(model_id='model', branch_id='leaf', task_hash='task', policy_hash='policy', budget=100)
    saved = await db.store.save_context(
        result['id'], **key, text='summary', method='summary', original_tokens=1000, processed_tokens=50
    )
    assert saved and (await db.store.get_context(result['id'], **key))['text'] == 'summary'
    assert await db.store.get_context(result['id'], **{**key, 'model_id': 'other'}) is None
    assert await db.store.get_context(result['id'], **{**key, 'budget': 99}) is None
    assert await db.store.list_contexts('chat', 'model', 'sibling') == []
    assert len(await db.store.list_contexts('chat', 'model', 'leaf')) == 1
    await edit(db, 'u', content='edited task')
    assert await db.store.get_context(result['id'], **key) is None
    assert (
        await db.store.save_context(
            result['id'], **key, text='stale', method='summary', original_tokens=1000, processed_tokens=40
        )
        is None
    )
    assert await db.store.list_contexts('chat', 'model', 'leaf') == []


@pytest.mark.asyncio
async def test_concurrent_raw_and_context_cache_writers_are_idempotent(database):
    db = database
    results = await asyncio.gather(*(db.store.save_result('chat', descriptor(db), branch_id='leaf') for _ in range(4)))
    assert all(results) and len({result['id'] for result in results}) == 1
    key = dict(
        model_id='model',
        branch_id='leaf',
        task_hash='task',
        policy_hash='policy',
        budget=100,
        text='summary',
        method='summary',
        original_tokens=1000,
        processed_tokens=50,
    )
    contexts = await asyncio.gather(*(db.store.save_context(results[0]['id'], **key) for _ in range(4)))
    assert all(contexts) and len({context['id'] for context in contexts}) == 1
    async with db.sessions() as session:
        assert len((await session.execute(select(db.model.ChatToolResult))).scalars().all()) == 1
        assert len((await session.execute(select(db.model.ChatToolResultContext))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_reader_exact_bounded_pages_search_and_bound_cursor(database):
    from open_webui.utils.context_compaction_v2 import estimate_text

    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    user = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='leaf', max_tokens=400)
    assert page['text'] == db.text[page['start'] : page['end']]
    assert estimate_text(page['text']) <= 400 and page['next_cursor'] and page['incomplete']
    second = await db.module.read_tool_result_page(
        user, 'chat', result['id'], branch_id='leaf', cursor=page['next_cursor'], max_tokens=400
    )
    assert second['start'] == page['end']
    query = await db.module.read_tool_result_page(
        user, 'chat', result['id'], branch_id='leaf', query='NEEDLE', max_tokens=400
    )
    assert 'NEEDLE' in query['text'] and query['text'] == db.text[query['start'] : query['end']]
    assert query['source'] == {k: descriptor(db)[k] for k in ('name', 'call_id', 'batch_id', 'message_id', 'output_id')}
    from fastapi import HTTPException

    for invalid in (
        {'query': 'changed', 'cursor': page['next_cursor']},
        {'branch_id': 'a', 'cursor': page['next_cursor']},
        {'cursor': 'bad cursor'},
    ):
        with pytest.raises(HTTPException) as denied:
            await db.module.read_tool_result_page(
                user, 'chat', result['id'], **{'branch_id': 'leaf', **invalid}, max_tokens=400
            )
        assert denied.value.status_code == 400


@pytest.mark.asyncio
async def test_reader_acl_cross_chat_cross_branch_and_deleted_sources(database):
    from fastapi import HTTPException

    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    owner = SimpleNamespace(id='owner', role='user')
    for user, chat_id, branch in (
        (SimpleNamespace(id='stranger', role='user'), 'chat', 'leaf'),
        (owner, 'other', 'leaf'),
        (owner, 'chat', 'sibling'),
    ):
        with pytest.raises(HTTPException) as denied:
            await db.module.read_tool_result_page(user, chat_id, result['id'], branch_id=branch)
        assert denied.value.status_code == 404
    await edit(db, 'a', output=[])
    with pytest.raises(HTTPException) as denied:
        await db.module.read_tool_result_page(owner, 'chat', result['id'], branch_id='leaf')
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_normalized_source_rows_override_legacy_and_sidecar_is_readable(database):
    db = database
    sidecar = {
        'id': 'side',
        'call_id': 'call',
        'batch_id': 'batch',
        'name': 'search',
        'kind': 'injected',
        'text': 'exact escaped &lt;result&gt;',
    }
    async with db.sessions() as session:
        await session.execute(
            db.message.insert().values(
                id='chat-a',
                chat_id='chat',
                role='assistant',
                parent_id='u',
                content='',
                output=[],
                meta={'tool_results': [sidecar]},
            )
        )
        await session.commit()
    assert await db.store.save_result('chat', descriptor(db), branch_id='leaf') is None
    saved = await db.store.save_result('chat', {**sidecar, 'message_id': 'a', 'output_id': 'side'}, branch_id='leaf')
    assert saved and saved['text'] == sidecar['text']


def test_migration_upgrade_downgrade_and_foreign_key_cleanup(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    paths = list((ROOT / 'open_webui/migrations/versions').glob('*_add_chat_tool_results.py'))
    assert len(paths) == 1, 'Tool-result migration is not implemented'
    spec = importlib.util.spec_from_file_location('tool_migration', paths[0])
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == 'c7d2e4f6a801'
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        connection.exec_driver_sql('CREATE TABLE chat (id TEXT PRIMARY KEY)')
        connection.exec_driver_sql("INSERT INTO chat VALUES ('chat')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert {'chat_tool_result', 'chat_tool_result_context'} <= set(inspect(connection).get_table_names())
            connection.exec_driver_sql("""INSERT INTO chat_tool_result
                (id,chat_id,message_id,output_id,call_id,batch_id,name,kind,text,source_hash,source_fingerprint,created_at)
                VALUES ('result','chat','a','out','call','batch','tool','native','raw','hash','fingerprint',1)""")
            connection.exec_driver_sql("""INSERT INTO chat_tool_result_context
                (id,result_id,chat_id,model_id,branch_id,task_hash,policy_hash,budget,text,method,original_tokens,processed_tokens,created_at)
                VALUES ('context','result','chat','model','leaf','task','policy',100,'summary','summary',1000,10,1)""")
            connection.exec_driver_sql("DELETE FROM chat WHERE id='chat'")
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat_tool_result').scalar() == 0
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat_tool_result_context').scalar() == 0
            migration.downgrade()
        assert inspect(connection).get_table_names() == ['chat']
    engine.dispose()


@pytest.mark.asyncio
async def test_resize_exact_pages_counts_full_json_and_restarts_at_shorter_cursor(database):
    import json
    from fastapi import HTTPException
    from open_webui.utils.context_compaction_v2 import estimate_text

    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    user = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='leaf', max_tokens=900)
    smaller = await db.module.resize_tool_result_page(user, 'chat', page, branch_id='leaf', max_tokens=400)
    assert smaller['start'] == page['start'] and smaller['end'] < page['end']
    assert smaller['text'] == db.text[smaller['start'] : smaller['end']]
    assert estimate_text(json.dumps(smaller, ensure_ascii=False, separators=(',', ':'))) <= 400
    resumed = await db.module.read_tool_result_page(
        user, 'chat', result['id'], branch_id='leaf', cursor=smaller['next_cursor'], max_tokens=400
    )
    assert resumed['start'] == smaller['end']
    for invalid in ({**page, 'text': 'forged'}, {**page, 'start': 1}):
        with pytest.raises(HTTPException) as denied:
            await db.module.resize_tool_result_page(user, 'chat', invalid, branch_id='leaf', max_tokens=400)
        assert denied.value.status_code == 400
    with pytest.raises(HTTPException) as tiny:
        await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='leaf', max_tokens=1)
    assert tiny.value.status_code == 400


@pytest.mark.asyncio
async def test_authenticated_raw_endpoint_runs_reader_acl_and_cursor_validation(database):
    import httpx
    from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query

    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')

    async def verified(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=authorization, role='user')

    router = APIRouter()
    path = ROOT / 'open_webui/routers/chats.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_chat_tool_result_page'
    ]
    assert len(functions) == 1, 'Authenticated raw-result endpoint is not implemented'
    namespace = dict(
        router=router,
        Depends=Depends,
        Query=Query,
        get_verified_user=verified,
        read_tool_result_page=db.module.read_tool_result_page,
    )
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1/chats')
    url = f'/api/v1/chats/chat/tool-results/{result["id"]}?branch_id=leaf&max_tokens=400'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers={'authorization': 'stranger'})).status_code == 404
        success = await client.get(url, headers={'authorization': 'owner'})
        assert success.status_code == 200, success.text
        assert success.json()['text'] == db.text[success.json()['start'] : success.json()['end']]
        headers = {'authorization': 'owner'}
        assert (
            await client.get(url.replace('branch_id=leaf', 'branch_id=sibling'), headers=headers)
        ).status_code == 404
        assert (
            await client.get(url.replace('chat/tool-results', 'other/tool-results'), headers=headers)
        ).status_code == 404
        assert (await client.get(url + '&cursor=forged', headers=headers)).status_code == 400
        assert (await client.get(url.replace('max_tokens=400', 'max_tokens=0'), headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_appended_assistant_output_keeps_prior_result_valid_but_source_edits_do_not(database):
    db = database
    data = descriptor(db)
    result = await db.store.save_result('chat', data, branch_id='leaf')
    await edit(
        db,
        'a',
        content='later final answer',
        output=[*db.raw['a']['output'], {'id': 'later', 'type': 'message', 'content': 'answer'}],
    )
    assert (await db.store.save_result('chat', data, branch_id='leaf'))['id'] == result['id']
    changed = [
        {**item, 'output': [{'type': 'input_text', 'text': 'edited raw result'}]} if item.get('id') == 'out' else item
        for item in db.raw['a']['output']
    ]
    await edit(db, 'a', output=changed)
    assert await db.store.get_result(result['id'], branch_id='leaf') is None
    assert await db.store.save_result('chat', data, branch_id='leaf') is None
    replacement = await db.store.save_result('chat', {**data, 'text': 'edited raw result'}, branch_id='leaf')
    assert replacement and replacement['id'] != result['id']


@pytest.mark.asyncio
async def test_reader_empty_result_query_unicode_offsets_and_cross_source_cursor(database):
    db = database
    data = descriptor(db)
    result = await db.store.save_result('chat', data, branch_id='leaf')
    user = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='leaf', max_tokens=400)
    from fastapi import HTTPException

    foreign = await db.store.save_result('other', data, branch_id='leaf')
    other_user = SimpleNamespace(id='other', role='user')
    with pytest.raises(HTTPException) as foreign_cursor:
        await db.module.read_tool_result_page(
            other_user, 'other', foreign['id'], branch_id='leaf', cursor=page['next_cursor'], max_tokens=400
        )
    assert foreign_cursor.value.status_code == 400
    special = 'ß before unicode NEEDLE result'
    await edit(db, 'a', output=[{**db.raw['a']['output'][0], 'output': [{'type': 'input_text', 'text': special}]}])
    updated = await db.store.save_result('chat', {**data, 'text': special}, branch_id='leaf')
    matched = await db.module.read_tool_result_page(
        user, 'chat', updated['id'], branch_id='leaf', query='needle', max_tokens=400
    )
    assert matched['start'] == special.index('NEEDLE') and matched['text'].startswith('NEEDLE')
    await edit(db, 'a', output=[{**db.raw['a']['output'][0], 'output': []}])
    empty = await db.store.save_result('chat', {**data, 'text': ''}, branch_id='leaf')
    empty_page = await db.module.read_tool_result_page(user, 'chat', empty['id'], branch_id='leaf', max_tokens=400)
    assert empty_page['text'] == '' and empty_page['start'] == empty_page['end'] == empty_page['total_chars'] == 0
    assert empty_page['next_cursor'] is None and empty_page['incomplete'] is False


@pytest.mark.asyncio
async def test_invalid_or_dangling_branch_is_rejected_and_invalid_context_is_not_cached(database):
    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    key = dict(
        model_id='model',
        branch_id='leaf',
        task_hash='task',
        policy_hash='policy',
        budget=10,
        text='summary',
        method='summary',
        original_tokens=100,
        processed_tokens=20,
    )
    assert await db.store.save_context(result['id'], **key) is None
    assert await db.store.list_contexts('chat', 'model', 'leaf') == []
    await edit(db, 'u', parentId='missing')
    assert await db.store.get_result(result['id'], branch_id='leaf') is None
    await edit(db, 'u', parentId='leaf')
    assert await db.store.get_result(result['id'], branch_id='leaf') is None


@pytest.mark.asyncio
async def test_legacy_output_without_id_uses_converter_index_identity(database):
    db = database
    await edit(db, 'a', output=[{k: v for k, v in db.raw['a']['output'][0].items() if k != 'id'}])
    saved = await db.store.save_result('chat', {**descriptor(db), 'output_id': 'output-0'}, branch_id='leaf')
    assert saved and saved['text'] == db.text


@pytest.mark.asyncio
async def test_ancestor_sidecar_evidence_edit_invalidates_result(database):
    db = database
    await edit(db, 'u', meta={'tool_results': [{'id': 'earlier', 'text': 'earlier original'}]})
    saved = await db.store.save_result('chat', descriptor(db), branch_id='leaf')
    assert saved
    await edit(db, 'u', meta={'tool_results': [{'id': 'earlier', 'text': 'edited evidence'}]})
    assert await db.store.get_result(saved['id'], branch_id='leaf') is None


@pytest.mark.asyncio
async def test_cursor_accepts_descendant_branch_and_legacy_ancestor_signature(database):
    import base64
    import struct
    from fastapi import HTTPException

    db = database
    result = await db.store.save_result('chat', descriptor(db), branch_id='a')
    user = SimpleNamespace(id='owner', role='user')
    page = await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='a', max_tokens=400)
    resumed = await db.module.read_tool_result_page(
        user, 'chat', result['id'], branch_id='leaf', cursor=page['next_cursor'], max_tokens=400
    )
    assert resumed['start'] == page['end']
    offset = struct.pack('>Q', page['end'])
    legacy = (
        base64.urlsafe_b64encode(offset + db.module._cursor_signature(result, 'a', None, offset)).decode().rstrip('=')
    )
    old = await db.module.read_tool_result_page(
        user, 'chat', result['id'], branch_id='leaf', cursor=legacy, max_tokens=400
    )
    assert old['start'] == page['end']
    leaf_page = await db.module.read_tool_result_page(user, 'chat', result['id'], branch_id='leaf', max_tokens=400)
    await edit(db, 'sister', role='user', content='sister task', parentId='a')
    for cursor in (leaf_page['next_cursor'], 'x' * 4096):
        with pytest.raises(HTTPException) as denied:
            await db.module.read_tool_result_page(
                user, 'chat', result['id'], branch_id='sister', cursor=cursor, max_tokens=400
            )
        assert denied.value.status_code == 400
    changed = [{**db.raw['a']['output'][0], 'output': [{'type': 'input_text', 'text': 'changed source'}]}]
    await edit(db, 'a', output=changed)
    replacement = await db.store.save_result('chat', {**descriptor(db), 'text': 'changed source'}, branch_id='leaf')
    with pytest.raises(HTTPException) as denied:
        await db.module.read_tool_result_page(
            user, 'chat', replacement['id'], branch_id='leaf', cursor=page['next_cursor'], max_tokens=400
        )
    assert denied.value.status_code == 400


def test_selection_migration_roundtrip_preserves_raw_and_cascades(tmp_path):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    paths = list((ROOT / 'open_webui/migrations/versions').glob('*_add_chat_tool_result_selections.py'))
    assert len(paths) == 1

    def load(path, name):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    migration = load(paths[0], 'selection_migration')
    raw_migration = load(
        next((ROOT / 'open_webui/migrations/versions').glob('*_add_chat_tool_results.py')), 'raw_migration'
    )
    assert migration.down_revision == raw_migration.revision
    engine = create_engine('sqlite:///' + str(tmp_path / 'selection-migration.db'))
    with engine.begin() as connection:
        connection.exec_driver_sql('PRAGMA foreign_keys=ON')
        connection.exec_driver_sql('CREATE TABLE chat (id TEXT PRIMARY KEY)')
        connection.exec_driver_sql("INSERT INTO chat VALUES ('chat')")
        with Operations.context(MigrationContext.configure(connection)):
            raw_migration.upgrade()
            connection.exec_driver_sql("""INSERT INTO chat_tool_result
                (id,chat_id,message_id,output_id,call_id,batch_id,name,kind,text,source_hash,source_fingerprint,created_at)
                VALUES ('raw','chat','a','out','call','batch','tool','native','raw text','hash','fingerprint',1)""")
            migration.upgrade()
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat_tool_result_selection').scalar() == 0
            connection.exec_driver_sql("""INSERT INTO chat_tool_result_selection
                (id,chat_id,result_id,model_id,branch_anchor_id,state,method,allocation,original_tokens,processed_tokens,recorded_at,message_id,output_id,call_id,batch_id,name,kind,source_hash,source_fingerprint)
                VALUES ('selection','chat',NULL,'model','a','prepared','original',100,100,100,1,'a','out','call','batch','tool','native','hash','fingerprint')""")
            migration.downgrade()
            assert connection.exec_driver_sql('SELECT text FROM chat_tool_result').scalar() == 'raw text'
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat').scalar() == 1
            migration.upgrade()
            connection.exec_driver_sql("""INSERT INTO chat_tool_result_selection
                (id,chat_id,result_id,model_id,branch_anchor_id,state,method,allocation,original_tokens,processed_tokens,recorded_at,message_id,output_id,call_id,batch_id,name,kind,source_hash,source_fingerprint)
                VALUES ('selection','chat',NULL,'model','a','prepared','original',100,100,100,1,'a','out','call','batch','tool','native','hash','fingerprint')""")
            connection.exec_driver_sql("DELETE FROM chat WHERE id='chat'")
            assert connection.exec_driver_sql('SELECT COUNT(*) FROM chat_tool_result_selection').scalar() == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_shared_reader_current_permission_and_revocation_apply_to_raw_pages(database):
    from fastapi import HTTPException

    db = database
    record = await db.store.save_result('chat', descriptor(db), branch_id='a')
    member = SimpleNamespace(id='member', role='user')
    first = await db.module.read_tool_result_page(member, 'chat', record['id'], branch_id='leaf', max_tokens=300)
    assert db.text[first['start'] : first['end']] == first['text']
    db.accessible_users.remove('member')
    with pytest.raises(HTTPException) as error:
        await db.module.read_tool_result_page(
            member, 'chat', record['id'], branch_id='leaf', cursor=first['next_cursor'], max_tokens=300
        )
    assert error.value.status_code == 404
