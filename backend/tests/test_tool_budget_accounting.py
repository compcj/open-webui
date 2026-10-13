"""Tool accounting is durable metadata independent of conversation notices."""

import ast
import asyncio
import copy
import time
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import Column, JSON, Text, Integer, select, text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm.attributes import flag_modified

import test_tool_result_budget as tool_helpers
from test_web_tool_citations import load_functions


@pytest.fixture
def batch_engine():
    case = tool_helpers.ToolBudgetEngineTests()
    case.setUp()
    try:
        yield case.make_batch(context=40000)
    finally:
        case.doCleanups()


def set_source(engine, *, method='original', text='short exact evidence', batch='silent-batch'):
    v2, tool, request, payload, history, service, events = engine
    ref = payload['messages'][-1]['_tool_result_refs'][0]
    ref.update(
        text=text,
        batch_id=batch,
        name='read_tool_result' if method == 'read' else 'search',
        kind=method if method == 'read' else 'native',
    )
    payload['messages'][-1]['content'] = text
    snapshot = history.read_snapshot.return_value
    snapshot['current_id'] = 'a2'
    output = snapshot['messages_map']['a2']['output'][-1]
    output['output'] = text
    output['tool_result'] = {k: ref[k] for k in ('batch_id', 'name', 'kind')}
    return snapshot


async def usage(engine, snapshot, *, model='model', current_id=None):
    v2, tool, request, payload, history, service, events = engine
    raw = v2.branch_messages(snapshot['messages_map'], current_id or snapshot['current_id'])
    with (
        patch.object(
            v2, 'load_compaction_settings', AsyncMock(return_value=request.state.context_compaction['settings'])
        ),
        patch.object(v2, '_history_payload', AsyncMock(return_value=(payload, raw))),
    ):
        return await v2.get_v2_chat_context_usage(
            SimpleNamespace(id='chat'), model, {'model': {}, 'other': {}}, current_id=current_id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['original', 'read'])
async def test_silent_original_and_exact_batch_survive_status_reload(batch_engine, method):
    v2, tool, request, payload, history, service, events = batch_engine
    snapshot = set_source(batch_engine, method=method)
    before = copy.deepcopy(snapshot['messages_map'])
    await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}})
    live = request.state.context_compaction['last_tool_batch']
    assert live['results'][0]['method'] == method
    assert not any(kind == 'tool_result_compaction' for kind, _ in events)
    data = next(data for kind, data in reversed(events) if kind == 'context_usage')
    accounting = data['tool_budget_accounting']
    snapshot['messages_map']['a2'].setdefault('meta', {})['tool_budget_accounting'] = {'model': accounting}
    restored = await usage(batch_engine, snapshot)
    assert restored['last_tool_batch'] == live
    assert before['a2']['output'] == snapshot['messages_map']['a2']['output']


@pytest.mark.asyncio
async def test_mixed_new_cached_and_short_reports_all_results_without_extra_notices(batch_engine):
    v2, tool, request, payload, history, service, events = batch_engine
    snapshot = set_source(batch_engine, text='large source ' * 6000, batch='mixed')
    ref = payload['messages'][-1]['_tool_result_refs'][0]
    ref['output_id'], ref['call_id'] = 'new-out', 'new-call'
    snapshot['messages_map']['a2']['output'][-1].update(id='new-out', call_id='new-call')
    new_message = copy.deepcopy(payload['messages'][-1])
    for output_id, call_id, text in [
        ('cached-out', 'cached-call', 'cached source ' * 6000),
        ('short-out', 'short-call', 'short evidence'),
    ]:
        message = copy.deepcopy(new_message)
        message.update(tool_call_id=call_id, content=text)
        message['_tool_result_refs'][0].update(output_id=output_id, call_id=call_id, text=text)
        payload['messages'].append(message)
        snapshot['messages_map']['a2']['output'].append(
            {'id': output_id, 'call_id': call_id, 'type': 'function_call_output', 'status': 'completed', 'output': text}
        )
    service.save_result.side_effect = lambda chat_id, descriptor, **kw: {**descriptor, 'id': descriptor['output_id']}
    service.get_context.side_effect = lambda result_id, **kw: (
        {'text': 'cached facts', 'method': 'summary'} if result_id == 'cached-out' else None
    )
    with patch.object(v2, 'generate_summary', AsyncMock(return_value='new facts')):
        await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}})
    live = request.state.context_compaction['last_tool_batch']
    assert [r['method'] for r in live['results']] == ['summary', 'summary', 'original']
    done = [d for k, d in events if k == 'tool_result_compaction' and d.get('done')]
    assert len(done) == 1 and done[0]['output_id'] == 'new-out'
    accounting = next(d for k, d in reversed(events) if k == 'context_usage')['tool_budget_accounting']
    snapshot['messages_map']['a2'].setdefault('meta', {})['tool_budget_accounting'] = {'model': accounting}
    restored = (await usage(batch_engine, snapshot))['last_tool_batch']
    assert restored == live
    assert restored['original_tokens'] == sum(r['original_tokens'] for r in restored['results'])
    assert restored['processed_tokens'] == sum(r['processed_tokens'] for r in restored['results'])


@pytest.mark.asyncio
async def test_newest_silent_snapshot_replaces_old_notices_and_is_model_branch_source_isolated(batch_engine):
    v2, tool, request, payload, history, service, events = batch_engine
    snapshot = set_source(batch_engine)
    snapshot['messages_map']['a2']['statusHistory'] = [
        {
            'action': 'tool_result_compaction',
            'model': 'model',
            'done': True,
            'method': 'summary',
            'batch_id': 'old-summary',
            'original_tokens': 1000,
            'processed_tokens': 10,
        }
    ]
    await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}})
    accounting = next(d for k, d in reversed(events) if k == 'context_usage')['tool_budget_accounting']
    snapshot['messages_map']['a2'].setdefault('meta', {})['tool_budget_accounting'] = {'model': accounting}
    assert (await usage(batch_engine, snapshot))['last_tool_batch']['batch_id'] == 'silent-batch'
    assert (await usage(batch_engine, snapshot, model='other'))['last_tool_batch'] is None
    snapshot['messages_map']['sibling'] = {'id': 'sibling', 'parentId': 'u', 'role': 'assistant', 'content': 'sibling'}
    assert (await usage(batch_engine, snapshot, current_id='sibling'))['last_tool_batch'] is None
    # Appending another completed output on the same assistant keeps source prefix valid.
    snapshot['messages_map']['a2']['output'].append({'id': 'later', 'type': 'message', 'content': 'later text'})
    assert (await usage(batch_engine, snapshot))['last_tool_batch']['batch_id'] == 'silent-batch'
    snapshot['messages_map']['u']['content'] = 'edited task'
    assert (await usage(batch_engine, snapshot))['last_tool_batch'] is None


@pytest.mark.asyncio
async def test_context_usage_persists_accounting_without_a_status_notice():
    accounting = {'model': 'model', 'branch_id': 'a', 'last_tool_batch': {'batch_id': 'silent'}}
    chats = SimpleNamespace(
        save_tool_budget_accounting=AsyncMock(return_value=True),
        add_message_status_to_chat_by_id_and_message_id=AsyncMock(),
    )
    sio = SimpleNamespace(emit=AsyncMock(), manager=SimpleNamespace(rooms={'/': {'user:owner': {}}}))
    namespace = dict(
        Chats=chats,
        Users=SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id='owner'))),
        sio=sio,
        WEBSOCKET_MANAGER='',
        is_saved_chat_id=lambda id: id == 'chat',
    )
    chats.get_accessible_chat_by_id = AsyncMock(return_value=SimpleNamespace(id='chat'))
    load_functions(namespace, 'socket/main.py', {'get_event_emitter'})
    emitter = await namespace['get_event_emitter']({'user_id': 'owner', 'chat_id': 'chat', 'message_id': 'a'})
    await emitter({'type': 'context_usage', 'data': {'mode': 'v2', 'tool_budget_accounting': accounting}})
    chats.save_tool_budget_accounting.assert_awaited_once_with('chat', 'a', accounting)
    chats.add_message_status_to_chat_by_id_and_message_id.assert_not_awaited()
    assert sio.emit.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('embedded,normalized', [(True, True), (True, False), (False, True)])
async def test_accounting_atomic_write_reload_preserves_originals_and_rejects_stale_sources(
    tmp_path, embedded, normalized
):
    from open_webui.utils import context_compaction_v2 as v2

    base = declarative_base()

    class Chat(base):
        __tablename__ = 'chat'
        id = Column(Text, primary_key=True)
        chat = Column(JSON)
        current_message_id = Column(Text)
        title = Column(Text)
        updated_at = Column(Integer)
        user_id = Column(Text)

    class ChatMessage(base):
        __tablename__ = 'chat_message'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)
        role = Column(Text)
        parent_id = Column(Text)
        content = Column(JSON)
        output = Column(JSON)
        meta = Column(JSON)
        status_history = Column(JSON)

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'accounting.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    paused, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def context(db=None):
        async with sessions() as session:
            original_get = session.get

            async def get(entity, identity, **kwargs):
                row = await original_get(entity, identity, **kwargs)
                if entity is ChatMessage and asyncio.current_task().get_name() == 'older-accounting':
                    paused.set()
                    await release.wait()
                return row

            session.get = get
            yield session

    snapshot_namespace = {'copy': copy, 'Base': SimpleNamespace(metadata=base.metadata), 'select': select}
    load_functions(snapshot_namespace, 'models/chat_context_checkpoints.py', {'read_snapshot_in_session'})
    snapshot_in_session = snapshot_namespace['read_snapshot_in_session']

    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChatTable')
    method = next(
        (n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'save_tool_budget_accounting'), None
    )
    assert method is not None, 'A separate durable accounting writer is required'
    namespace = dict(
        copy=copy,
        text=sql_text,
        log=__import__('logging').getLogger(__name__),
        Chat=Chat,
        ChatMessage=ChatMessage,
        get_async_db_context=context,
        flag_modified=flag_modified,
        read_snapshot_in_session=snapshot_in_session,
        source_refs_match=v2.source_refs_match,
        digest=v2.digest,
    )
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    table = SimpleNamespace()
    raw = {
        'u': {'id': 'u', 'role': 'user', 'content': 'task'},
        'a': {
            'id': 'a',
            'role': 'assistant',
            'parentId': 'u',
            'content': 'original',
            'output': [{'id': 'out', 'type': 'function_call_output', 'output': 'raw original'}],
            'meta': {'existing': True},
            'statusHistory': [{'action': 'web_search'}],
        },
    }
    accounting = {
        'model': 'model',
        'branch_id': 'a',
        'recorded_at': 1,
        'source_refs': v2.tool_accounting_refs(list(raw.values()), [{'source_message_id': 'a', 'output_id': 'out'}]),
        'last_tool_batch': {'batch_id': 'silent', 'results': [{'method': 'original'}]},
    }
    accounting['source_fingerprint'] = v2.digest(accounting['source_refs'])
    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            session.add(
                Chat(
                    id='chat',
                    chat={'history': {'messages': raw if embedded else {}, 'currentId': 'a'}},
                    current_message_id='a',
                )
            )
            if normalized:
                for identity, data in raw.items():
                    session.add(
                        ChatMessage(
                            id='chat-' + identity,
                            chat_id='chat',
                            role=data['role'],
                            parent_id=data.get('parentId'),
                            content=data['content'],
                            output=data.get('output'),
                            meta=data.get('meta'),
                            status_history=data.get('statusHistory'),
                        )
                    )
            await session.commit()
        save = namespace['save_tool_budget_accounting']
        assert await save(table, 'chat', 'a', accounting)
        newer = {
            **accounting,
            'recorded_at': 2,
            'last_tool_batch': {'batch_id': 'new-silent', 'results': [{'method': 'read'}]},
        }
        assert await save(table, 'chat', 'a', newer)
        assert not await save(table, 'chat', 'a', accounting)  # Late stale writer cannot replace newest.
        invalid = {**newer, 'recorded_at': 3, 'source_fingerprint': 'forged'}
        assert not await save(table, 'chat', 'a', invalid)
        other = {**accounting, 'model': 'other', 'last_tool_batch': {'batch_id': 'other-model'}}
        assert await save(table, 'chat', 'a', other)
        async with sessions() as session:
            saved = await snapshot_in_session(session, 'chat')
            message = saved['messages_map']['a']
            assert message['meta']['tool_budget_accounting']['model'] == newer
            assert message['meta']['tool_budget_accounting']['other'] == other
            assert message['content'] == 'original' and message['output'] == raw['a']['output']
            if embedded:
                chat = await session.get(Chat, 'chat')
                embedded_message = chat.chat['history']['messages']['a']
                assert embedded_message['statusHistory'] == raw['a']['statusHistory']
                assert embedded_message['meta']['tool_budget_accounting']['model'] == newer
            if normalized:
                normalized_message = await session.get(ChatMessage, 'chat-a')
                assert normalized_message.status_history == raw['a']['statusHistory']
                assert normalized_message.meta['tool_budget_accounting']['model'] == newer
        if normalized:
            older = {**newer, 'recorded_at': 3}
            newest = {**newer, 'recorded_at': 4, 'last_tool_batch': {'batch_id': 'concurrent-newest'}}
            old_task = asyncio.create_task(save(table, 'chat', 'a', older), name='older-accounting')
            await asyncio.wait_for(paused.wait(), timeout=3)
            new_task = asyncio.create_task(save(table, 'chat', 'a', newest))
            try:
                await asyncio.wait_for(asyncio.shield(new_task), timeout=0.1)
            except asyncio.TimeoutError:
                pass  # A SQLite write transaction serializes the newer writer.
            release.set()
            await old_task
            assert await new_task
            async with sessions() as session:
                stored = await session.get(ChatMessage, 'chat-a')
                assert stored.meta['tool_budget_accounting']['model'] == newest
            newer = newest
        # A stale browser save has no accounting field, including normalized-only chats.
        methods = [
            copy.deepcopy(n)
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name
            in {
                '_chat_transaction',
                'merge_history',
                'upsert_message_to_history',
                'update_chat_by_id',
                'upsert_message_to_chat_by_id_and_message_id',
            }
        ]
        for item in methods:
            if item.name != '_chat_transaction':
                item.decorator_list = []
        namespace.update(
            asynccontextmanager=asynccontextmanager,
            get_async_db=context,
            time=time,
            select=select,
            ChatModel=SimpleNamespace(model_validate=lambda row: row),
            get_output_text=lambda output: '',
            ChatMessages=SimpleNamespace(upsert_message=AsyncMock()),
        )
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(
                        body=[
                            ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                            *methods,
                        ],
                        type_ignores=[],
                    )
                ),
                str(source),
                'exec',
            ),
            namespace,
        )
        table._chat_transaction = lambda id: namespace['_chat_transaction'](table, id)
        table.merge_history = namespace['merge_history']
        table._clean_null_bytes = lambda value: value
        table.get_current_message_id = lambda chat: chat['history']['currentId']
        table._repair_chat_current_id = lambda chat: None
        table.upsert_message_to_history = namespace['upsert_message_to_history']
        namespace['ChatTable'] = SimpleNamespace(_add_child_id_to_parent=lambda *args: None)
        incoming = copy.deepcopy(raw)
        incoming['a']['meta'] = {'client': True}
        assert (
            await namespace['update_chat_by_id'](table, 'chat', {'history': {'currentId': 'a', 'messages': incoming}})
            is not None
        )
        async with sessions() as session:
            chat = await session.get(Chat, 'chat')
            assert chat.chat['history']['messages']['a']['meta']['tool_budget_accounting']['model'] == newer
        # Completion upsert also must recover server metadata from normalized-only state.
        if not embedded:
            async with sessions() as session:
                chat = await session.get(Chat, 'chat')
                chat.chat = {'history': {'messages': {}, 'currentId': 'a'}}
                await session.commit()
        assert (
            await namespace['upsert_message_to_chat_by_id_and_message_id'](
                table, 'chat', 'a', {'content': 'completed answer', 'meta': {'client': True}}
            )
            is not None
        )
        saved_call = namespace['ChatMessages'].upsert_message.await_args
        saved_data = saved_call.kwargs['data'] if 'data' in saved_call.kwargs else saved_call.args[3]
        assert saved_data['meta']['tool_budget_accounting']['model'] == newer
        async with sessions() as session:
            # Source edits reject the write without touching content/output/meta.
            if normalized:
                (await session.get(ChatMessage, 'chat-u')).content = 'edited task'
            if embedded:
                chat = await session.get(Chat, 'chat')
                chat.chat['history']['messages']['u']['content'] = 'edited task'
                flag_modified(chat, 'chat')
            await session.commit()
        assert not await save(table, 'chat', 'a', {**newer, 'recorded_at': 4})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_accounting_write_reports_usage_error_without_status_or_original_mutation():
    accounting = {'model': 'model', 'branch_id': 'a', 'last_tool_batch': {'batch_id': 'silent'}}
    chats = SimpleNamespace(
        save_tool_budget_accounting=AsyncMock(return_value=False),
        add_message_status_to_chat_by_id_and_message_id=AsyncMock(),
    )
    sio = SimpleNamespace(emit=AsyncMock(), manager=SimpleNamespace(rooms={'/': {'user:owner': {}}}))
    namespace = dict(
        Chats=chats,
        Users=SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(id='owner'))),
        sio=sio,
        WEBSOCKET_MANAGER='',
        is_saved_chat_id=lambda id: id == 'chat',
    )
    chats.get_accessible_chat_by_id = AsyncMock(return_value=SimpleNamespace(id='chat'))
    load_functions(namespace, 'socket/main.py', {'get_event_emitter'})
    emitter = await namespace['get_event_emitter']({'user_id': 'owner', 'chat_id': 'chat', 'message_id': 'a'})
    event = {'type': 'context_usage', 'data': {'mode': 'v2', 'tool_budget_accounting': accounting}}
    await emitter(event)
    emitted = sio.emit.await_args.args[1]['data']['data']
    assert emitted['accounting_error']
    assert 'reload' in emitted['accounting_error'].lower()
    chats.add_message_status_to_chat_by_id_and_message_id.assert_not_awaited()
    assert event == {'type': 'context_usage', 'data': {'mode': 'v2', 'tool_budget_accounting': accounting}}


def test_assistant_metadata_updates_preserve_server_accounting_snapshot():
    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChatTable')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'upsert_message_to_history')
    method.decorator_list = []
    namespace = {'ChatTable': SimpleNamespace(_add_child_id_to_parent=lambda *args: None), 'time': time}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    accounting = {'model': {'recorded_at': 2, 'last_tool_batch': {'batch_id': 'silent'}}}
    history = {'messages': {'a': {'content': 'original', 'meta': {'tool_budget_accounting': accounting}}}}
    data = {
        'content': 'new content',
        'meta': {'tool_results': [{'id': 'next-tool'}], 'tool_budget_accounting': {'model': {'recorded_at': 1}}},
    }
    saved = namespace['upsert_message_to_history'](history, 'a', data)
    assert saved['meta']['tool_budget_accounting'] == accounting
    assert saved['meta']['tool_results'] == data['meta']['tool_results']
    assert saved['content'] == 'new content'


@pytest.mark.asyncio
async def test_injected_reply_completion_and_later_sidecar_preserve_source_identity(batch_engine):
    v2, tool, request, payload, history, service, events = batch_engine
    snapshot = set_source(batch_engine)
    ref = payload['messages'][-1]['_tool_result_refs'][0]
    ref['kind'] = 'injected'
    assistant = snapshot['messages_map']['a2']
    assistant.pop('output')
    assistant['content'] = ''
    sidecar = {
        'id': ref['output_id'],
        **{key: ref[key] for key in ('call_id', 'batch_id', 'name', 'kind')},
        'text': ref['text'],
    }
    assistant['meta'] = {'tool_results': [sidecar]}
    await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}})
    accounting = next(d for k, d in reversed(events) if k == 'context_usage')['tool_budget_accounting']
    assistant['meta']['tool_budget_accounting'] = {'model': accounting}
    assistant.update(
        content='completed answer',
        output=[{'id': 'answer', 'type': 'message', 'content': 'completed answer'}],
        sources=[{'name': 'citation'}],
    )
    assistant['meta']['tool_results'].append(
        {**sidecar, 'id': 'next-source', 'batch_id': 'next-batch', 'call_id': 'next-call', 'text': 'next evidence'}
    )
    assert (await usage(batch_engine, snapshot))['last_tool_batch']['batch_id'] == 'silent-batch'
    next_ref = payload['messages'][-1]['_tool_result_refs'][0]
    next_ref.update(output_id='next-source', batch_id='next-batch', call_id='next-call', text='next evidence')
    payload['messages'][-1]['content'] = 'next evidence'
    await v2.prepare_v2_request(request, None, payload, {}, 'model', {'model': {}})
    latest = next(d for k, d in reversed(events) if k == 'context_usage')['tool_budget_accounting']
    assistant['meta']['tool_budget_accounting']['model'] = latest
    assert (await usage(batch_engine, snapshot))['last_tool_batch']['batch_id'] == 'next-batch'
    assistant['meta']['tool_results'][-1]['text'] = 'edited result'
    assert (await usage(batch_engine, snapshot))['last_tool_batch'] is None


def test_browser_history_save_preserves_server_accounting_without_client_copy():
    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    cls = next(
        n
        for n in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(n, ast.ClassDef) and n.name == 'ChatTable'
    )
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'merge_history')
    method.decorator_list = []
    namespace = {'copy': copy}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    accounting = {'model': {'recorded_at': 2, 'last_tool_batch': {'batch_id': 'silent'}}}
    existing = {
        'currentId': 'a',
        'messages': {'a': {'id': 'a', 'content': '', 'meta': {'tool_budget_accounting': accounting}}},
    }
    incoming = {
        'currentId': 'a',
        'messages': {
            'a': {
                'id': 'a',
                'content': 'completed answer',
                'meta': {'client': True, 'tool_budget_accounting': {'model': {'recorded_at': 1}}},
            }
        },
    }
    merged = namespace['merge_history'](existing, incoming)
    assert merged['messages']['a']['meta']['tool_budget_accounting'] == accounting
    assert merged['messages']['a']['content'] == 'completed answer'
    assert merged['messages']['a']['meta']['client']


@pytest.mark.asyncio
@pytest.mark.parametrize('writer_kind', ['single', 'bulk'])
@pytest.mark.parametrize('interleaving', ['before', 'during', 'reused'])
async def test_actual_normalized_writer_keeps_newer_accounting_at_final_write(tmp_path, writer_kind, interleaving):
    from open_webui.utils import context_compaction_v2 as v2

    base = declarative_base()

    class Chat(base):
        __tablename__ = 'chat'
        id = Column(Text, primary_key=True)
        chat = Column(JSON)
        current_message_id = Column(Text)

    class ChatMessage(base):
        __tablename__ = 'chat_message'
        id = Column(Text, primary_key=True)
        chat_id = Column(Text)
        role = Column(Text)
        parent_id = Column(Text)
        content = Column(JSON)
        output = Column(JSON)
        meta = Column(JSON)
        updated_at = Column(Integer)
        user_id = Column(Text)
        model_id = Column(Text)
        files = Column(JSON)
        sources = Column(JSON)
        embeds = Column(JSON)
        done = Column(JSON)
        status_history = Column(JSON)
        error = Column(JSON)
        usage = Column(JSON)
        context_summary = Column(Text)
        created_at = Column(Integer)

    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'final-writer.db'))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    paused, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def context(db=None):
        if db is not None:
            yield db
            return
        async with sessions() as session:
            original_get, original_execute = session.get, session.execute

            async def get(entity, identity, **kwargs):
                row = await original_get(entity, identity, **kwargs)
                if (
                    writer_kind == 'single'
                    and asyncio.current_task().get_name() == 'paused-normalized'
                    and entity is ChatMessage
                ):
                    paused.set()
                    await release.wait()
                return row

            async def execute(statement, *args, **kwargs):
                result = await original_execute(statement, *args, **kwargs)
                if (
                    writer_kind == 'bulk'
                    and asyncio.current_task().get_name() == 'paused-normalized'
                    and statement.is_insert
                ):
                    paused.set()
                    await release.wait()
                return result

            session.get, session.execute = get, execute
            yield session

    snapshot_namespace = {'copy': copy, 'Base': SimpleNamespace(metadata=base.metadata), 'select': select}
    load_functions(snapshot_namespace, 'models/chat_context_checkpoints.py', {'read_snapshot_in_session'})
    read_snapshot = snapshot_namespace['read_snapshot_in_session']
    namespace = dict(
        copy=copy,
        nullcontext=nullcontext,
        time=time,
        select=select,
        text=sql_text,
        update=__import__('sqlalchemy').update,
        Chat=Chat,
        ChatMessage=ChatMessage,
        ChatMessageModel=SimpleNamespace(model_validate=lambda row: row),
        get_async_db_context=context,
        get_usage=lambda data: None,
        log=__import__('logging').getLogger(__name__),
        flag_modified=flag_modified,
        read_snapshot_in_session=read_snapshot,
    )
    normalized_path = Path(__file__).parents[1] / 'open_webui/models/chat_messages.py'
    cls = next(
        n
        for n in ast.parse(normalized_path.read_text(encoding='utf-8')).body
        if isinstance(n, ast.ClassDef) and n.name == 'ChatMessageTable'
    )
    cls.body = [
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name
        in {'_apply_message_data', '_build_message', '_lock_message_writes', 'upsert_message', 'upsert_messages'}
    ]
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls],
                    type_ignores=[],
                )
            ),
            str(normalized_path),
            'exec',
        ),
        namespace,
    )
    writer = namespace['ChatMessageTable']()
    source = Path(__file__).parents[1] / 'open_webui/models/chats.py'
    cls = next(
        n
        for n in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(n, ast.ClassDef) and n.name == 'ChatTable'
    )
    method = next(
        n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'save_tool_budget_accounting'
    )
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(
                    body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method],
                    type_ignores=[],
                )
            ),
            str(source),
            'exec',
        ),
        namespace,
    )
    save = namespace['save_tool_budget_accounting']
    raw = {
        'u': {'id': 'u', 'role': 'user', 'content': 'task'},
        'a': {
            'id': 'a',
            'role': 'assistant',
            'parentId': 'u',
            'content': '',
            'output': [{'id': 'out', 'type': 'function_call_output', 'output': 'raw evidence'}],
        },
    }
    older = {
        'model': 'model',
        'branch_id': 'a',
        'recorded_at': 4,
        'source_refs': v2.tool_accounting_refs(list(raw.values()), [{'source_message_id': 'a', 'output_id': 'out'}]),
        'last_tool_batch': {'batch_id': 'older'},
    }
    older['source_fingerprint'] = v2.digest(older['source_refs'])
    newest = {**older, 'recorded_at': 14, 'last_tool_batch': {'batch_id': 'newest'}}
    other = {**older, 'model': 'other'}
    data = {'content': 'completed answer', 'meta': {'client': True, 'tool_budget_accounting': {'model': older}}}

    async def write(db=None):
        if writer_kind == 'single':
            await writer.upsert_message('a', 'chat', 'owner', data, db=db)
        else:
            await writer.upsert_messages('chat', 'owner', {'a': data}, db=db)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        async with sessions() as session:
            session.add(Chat(id='chat', chat={'history': {'messages': raw, 'currentId': 'a'}}, current_message_id='a'))
            for identity, message in raw.items():
                session.add(
                    ChatMessage(
                        id='chat-' + identity,
                        chat_id='chat',
                        role=message['role'],
                        parent_id=message.get('parentId'),
                        content=message['content'],
                        output=message.get('output'),
                    )
                )
            await session.commit()
        assert await save(None, 'chat', 'a', older)
        assert await save(None, 'chat', 'a', other)
        if interleaving == 'during':
            old_task = asyncio.create_task(write(), name='paused-normalized')
            await asyncio.wait_for(paused.wait(), timeout=3)
            new_task = asyncio.create_task(save(None, 'chat', 'a', newest))
            try:
                await asyncio.wait_for(asyncio.shield(new_task), timeout=0.1)
            except asyncio.TimeoutError:
                pass
            release.set()
            await old_task
            assert await new_task
        elif interleaving == 'reused':
            async with sessions() as session:
                cached = await session.get(ChatMessage, 'chat-a')
                assert cached.meta['tool_budget_accounting']['model'] == older
                assert await save(None, 'chat', 'a', newest)
                await write(db=session)
                await session.commit()
        else:
            assert await save(None, 'chat', 'a', newest)
            await write()
        async with sessions() as session:
            snapshot = await read_snapshot(session, 'chat')
            saved = snapshot['messages_map']['a']
            assert saved['meta']['tool_budget_accounting']['model'] == newest
            assert saved['meta']['tool_budget_accounting']['other'] == other
            if writer_kind == 'single':
                assert saved['meta']['client'] is True
                assert saved['content'] == 'completed answer'
            else:
                # Upstream bulk backfill inserts missing rows only, preserving existing data.
                assert 'client' not in saved['meta']
                assert saved['content'] == raw['a']['content']
            assert saved['output'] == raw['a']['output']
        assert data['meta']['tool_budget_accounting']['model'] == older
        # Omitting accounting and clearing other metadata still keeps the server map.
        data['meta'] = {'client2': True}
        await write()
        data['meta'] = None
        await write()
        async with sessions() as session:
            row = await session.get(ChatMessage, 'chat-a')
            assert row.meta['tool_budget_accounting']['model'] == newest
            assert row.meta['tool_budget_accounting']['other'] == other
        # Empty bulk batches and new IDs keep their existing insertion semantics.
        await writer.upsert_messages('chat', 'owner', {})
        new_data = {'role': 'assistant', 'parentId': 'u', 'content': 'new reply', 'meta': {'note': True}}
        if writer_kind == 'single':
            await writer.upsert_message('new', 'chat', 'owner', new_data)
        else:
            await writer.upsert_messages('chat', 'owner', {'new': new_data})
        async with sessions() as session:
            row = await session.get(ChatMessage, 'chat-new')
            assert row.meta == {'note': True} and row.content == 'new reply'
    finally:
        release.set()
        await engine.dispose()
