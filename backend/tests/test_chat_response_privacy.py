"""Run actual router projections and exports without importing app/database state."""

import ast
import asyncio
import copy
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from pydantic import BaseModel, field_validator

from open_webui.utils.chat_fork import build_fork_history

ROOT = Path(__file__).parents[1] / 'open_webui'


@pytest.fixture
def api(monkeypatch):
    namespace = {'BaseModel': BaseModel, 'field_validator': field_validator}
    schemas = ast.parse((ROOT / 'models/chats.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in schemas.body
        if isinstance(node, ast.ClassDef)
        and node.name in {'ChatResponse', 'ChatForm', 'ChatImportForm', 'ChatsImportForm'}
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<chat-schemas>', 'exec'), namespace)
    router = ast.parse((ROOT / 'routers/chats.py').read_text(encoding='utf-8'))
    selected = {
        'chat_response_for_user',
        'shared_chat_response',
        'generate_chat_export_ndjson',
        'get_chats_by_folder_id',
        'get_user_archived_chats',
        'get_all_user_chats_in_db',
        'create_new_chat',
        'import_chats',
        'update_chat_by_id',
        'pin_chat_by_id',
        'archive_chat_by_id',
        'update_chat_folder_id_by_id',
        'clone_chat_by_id',
        'share_chat_by_id',
        'update_shared_chat_access_by_id',
        'update_chat_message_by_id',
        'delete_chat_message_by_id',
        'fork_chat_by_id',
        'clone_shared_chat_by_id',
    }
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    for node in router.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in selected:
            node.decorator_list = []
            node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
            nodes.append(node)
    users = ModuleType('open_webui.models.users')
    users.Users = SimpleNamespace(get_users_by_user_ids=AsyncMock(return_value=[]))
    monkeypatch.setitem(sys.modules, users.__name__, users)
    socket = ModuleType('open_webui.socket.main')
    socket.refresh_chat_access = AsyncMock()
    monkeypatch.setitem(sys.modules, socket.__name__, socket)
    namespace.update(
        {
            'log': logging.getLogger(__name__),
            'CHAT_EXPORT_BATCH_SIZE': 2,
            'ENABLE_ADMIN_EXPORT': True,
            'ENABLE_ADMIN_CHAT_ACCESS': True,
            'HTTPException': HTTPException,
            'status': status,
            'ERROR_MESSAGES': SimpleNamespace(DEFAULT=lambda: 'error', NOT_FOUND='not found'),
            'EVENTS': SimpleNamespace(
                **{
                    name: name
                    for name in (
                        'CHAT_CREATED',
                        'CHAT_IMPORTED',
                        'CHAT_UPDATED',
                        'CHAT_PINNED',
                        'CHAT_UNPINNED',
                        'CHAT_ARCHIVED',
                        'CHAT_UNARCHIVED',
                        'CHAT_FOLDER_UPDATED',
                        'CHAT_CLONED',
                        'CHAT_SHARED',
                        'MESSAGE_UPDATED',
                        'MESSAGE_DELETED',
                    )
                }
            ),
            'publish_event': AsyncMock(),
            'require_chat_import_permission': AsyncMock(),
            'has_folder_write_access': AsyncMock(return_value=True),
            'has_active_tasks': AsyncMock(return_value=False),
            'has_permission': AsyncMock(return_value=True),
            'get_event_emitter': AsyncMock(return_value=None),
            'Config': SimpleNamespace(get=AsyncMock(return_value={})),
            'filter_allowed_access_grants': AsyncMock(return_value=[]),
            'AccessGrants': SimpleNamespace(set_access_grants=AsyncMock()),
            'stop_item_tasks': AsyncMock(),
            'build_fork_history': build_fork_history,
            'uuid4': lambda: 'new-chat',
            'is_open_shared_chat': AsyncMock(return_value=True),
            'can_read_shared_chat': AsyncMock(return_value=True),
            'Tags': SimpleNamespace(ensure_tags_exist=AsyncMock()),
        }
    )
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<chat-routes>', 'exec'), namespace
    )
    messages = {}
    for author in ('owner', 'writer'):
        for role in ('user', 'assistant'):
            message_id = f'{author}-{role}'
            messages[message_id] = {
                'id': message_id,
                'role': role,
                'user_id': author,
                'parentId': next(reversed(messages), None),
                'content': f'{author} public {role}',
                'meta': {
                    'request_context': {
                        'messages': [{'role': 'system', 'content': f'{author}-private-system'}],
                        'params': {'tool_servers': [{'key': f'{author}-private-key'}]},
                    }
                },
                'output': [
                    {'type': 'function_call_output', 'citation_context': 'frozen citation', 'output': 'raw evidence'}
                ],
            }
    chat = namespace['ChatResponse'](
        id='source-chat',
        user_id='owner',
        title='shared',
        created_at=1,
        updated_at=2,
        archived=False,
        chat={
            'params': {'system': 'owner-private-settings'},
            'tool_servers': [{'key': 'owner-server'}],
            'history': {'messages': messages, 'currentId': 'writer-assistant'},
            'messages': copy.deepcopy(list(messages.values())),
        },
        variables={'owner-private-variable': 'value'},
    )
    methods = (
        'get_chat_by_id_and_user_id',
        'get_chat_by_id',
        'get_accessible_chat_by_id',
        'update_chat_by_id',
        'toggle_chat_pinned_by_id',
        'toggle_chat_archive_by_id',
        'update_chat_folder_id_by_id_and_user_id',
        'get_chat_by_share_id',
        'update_chat_share_id_by_id',
        'upsert_message_to_chat_by_id_and_message_id',
        'delete_message_from_chat_by_id_and_message_id',
    )
    chats = SimpleNamespace(**{name: AsyncMock(return_value=chat) for name in methods})
    chats.get_messages_map_by_chat_id = AsyncMock(return_value=chat.chat['history']['messages'])
    chats.get_chats_by_user_id = AsyncMock(
        side_effect=[SimpleNamespace(items=[chat, chat]), SimpleNamespace(items=[chat])]
    )
    for name in ('get_chats_by_folder_ids_and_user_id', 'get_archived_chats_by_user_id', 'get_chats'):
        setattr(chats, name, AsyncMock(return_value=[chat]))
    captured = []
    inserted = {}

    async def insert(chat_id, user_id, form, **kwargs):
        captured.append(copy.deepcopy(form.chat))
        result = chat.model_copy(
            update={
                'id': chat_id,
                'user_id': user_id,
                'chat': copy.deepcopy(form.chat),
                'variables': form.variables or {},
            }
        )
        inserted[chat_id] = result
        return result

    async def import_chats(user_id, forms, **kwargs):
        return [await insert('new-chat', user_id, form) for form in forms]

    async def update_variables(chat_id, variables, **kwargs):
        return inserted[chat_id].model_copy(update={'variables': copy.deepcopy(variables)})

    chats.update_chat_variables_by_id = AsyncMock(side_effect=update_variables)
    chats.insert_new_chat = AsyncMock(side_effect=insert)
    chats.import_chats = AsyncMock(side_effect=import_chats)
    namespace.update(
        Chats=chats,
        Folders=SimpleNamespace(get_children_folders_by_id_and_user_id=AsyncMock(return_value=[])),
        SharedChats=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(id='share')),
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(user_id='owner', chat_id=chat.id, chat={'share_mode': 'continue'})
            ),
        ),
    )
    return SimpleNamespace(
        **namespace,
        source=chat,
        captured=captured,
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=None))),
    )


def assert_private(response, actor):
    data = response.model_dump() if hasattr(response, 'model_dump') else response
    body = json.dumps(data)
    for author in ('owner', 'writer'):
        assert (f'{author}-private-system' in body) == (actor == author)
        assert (f'{author}-private-key' in body) == (actor == author)
        assert f'{author} public' in body
    assert ('owner-private-settings' in body) == (actor == 'owner')
    if actor != 'owner':
        assert 'owner-private-variable' not in body
        assert 'owner-server' not in body
    assert 'raw evidence' in body and 'frozen citation' in body


@pytest.mark.parametrize('actor', ['owner', 'writer', 'admin', None])
def test_shared_projection_is_author_scoped_and_source_immutable(api, actor):
    before = api.source.model_dump()
    user = SimpleNamespace(id=actor) if actor else None
    result = asyncio.run(api.shared_chat_response(api.source, user))
    assert_private(result, actor)
    assert api.source.model_dump() == before


def test_ndjson_export_projects_each_batch_without_altering_server_snapshot(api):
    before = api.source.model_dump()

    async def collect():
        return [json.loads(line) async for line in api.generate_chat_export_ndjson('owner')]

    result = asyncio.run(collect())
    assert len(result) == 3
    for response in result:
        assert_private(response, 'owner')
    assert [call.kwargs['skip'] for call in api.Chats.get_chats_by_user_id.await_args_list] == [0, 2]
    assert all(call.kwargs['db'] is None for call in api.Chats.get_chats_by_user_id.await_args_list)
    assert api.source.model_dump() == before


@pytest.mark.parametrize('route', ['get_chats_by_folder_id', 'get_user_archived_chats', 'get_all_user_chats_in_db'])
@pytest.mark.parametrize('actor', ['owner', 'writer', 'admin'])
def test_list_responses_apply_same_author_projection(api, route, actor):
    before = api.source.model_dump()
    kwargs = {'user': SimpleNamespace(id=actor, role='admin' if actor == 'admin' else 'user'), 'db': None}
    if route == 'get_chats_by_folder_id':
        kwargs['folder_id'] = 'folder'
    result = asyncio.run(getattr(api, route)(**kwargs))
    assert_private(result[0], actor)
    assert api.source.model_dump() == before


@pytest.mark.parametrize(
    'route',
    [
        'update_chat_by_id',
        'pin_chat_by_id',
        'archive_chat_by_id',
        'update_chat_folder_id_by_id',
        'share_chat_by_id',
        'update_shared_chat_access_by_id',
        'update_chat_message_by_id',
        'delete_chat_message_by_id',
    ],
)
def test_owner_mutation_response_hides_writer_private_snapshot(api, route):
    before = api.source.model_dump()
    kwargs = {'request': api.request, 'id': api.source.id, 'user': SimpleNamespace(id='owner', role='user'), 'db': None}
    if route == 'update_chat_by_id':
        kwargs['form_data'] = api.ChatForm(chat={'title': 'new title'})
    if route == 'update_chat_folder_id_by_id':
        kwargs['form_data'] = SimpleNamespace(folder_id=None)
    if route in {'update_chat_message_by_id', 'delete_chat_message_by_id'}:
        kwargs['message_id'] = 'owner-user'
    if route == 'update_chat_message_by_id':
        kwargs['form_data'] = SimpleNamespace(content='edited', voice=None)
    if route == 'update_shared_chat_access_by_id':
        kwargs['form_data'] = SimpleNamespace(access_grants=[], model_fields_set=set())
    result = asyncio.run(getattr(api, route)(**kwargs))
    assert_private(result, 'owner')
    assert api.source.model_dump() == before


@pytest.mark.parametrize(
    'route,actor',
    [
        ('clone_chat_by_id', 'owner'),
        ('fork_chat_by_id', 'owner'),
        ('fork_chat_by_id', 'writer'),
        ('clone_shared_chat_by_id', 'writer'),
    ],
)
def test_copy_never_persists_other_authors_private_snapshot(api, route, actor):
    before = api.source.model_dump()
    kwargs = {'request': api.request, 'id': api.source.id, 'user': SimpleNamespace(id=actor, role='user'), 'db': None}
    if route == 'clone_chat_by_id':
        kwargs['form_data'] = SimpleNamespace(title=None)
    result = asyncio.run(getattr(api, route)(**kwargs))
    assert_private(result, actor)
    assert len(api.captured) == 1
    assert_private({'chat': api.captured[0]}, actor)
    assert api.source.model_dump() == before


@pytest.mark.parametrize('route', ['create_new_chat', 'import_chats'])
def test_create_and_import_responses_follow_actor_projection(api, route):
    before = api.source.model_dump()
    form = api.ChatForm(chat=copy.deepcopy(api.source.chat))
    if route == 'import_chats':
        form = api.ChatsImportForm(chats=[api.ChatImportForm(chat=form.chat)])
    result = asyncio.run(
        getattr(api, route)(request=api.request, form_data=form, user=SimpleNamespace(id='owner', role='user'), db=None)
    )
    assert_private(result[0] if isinstance(result, list) else result, 'owner')
    assert api.source.model_dump() == before
