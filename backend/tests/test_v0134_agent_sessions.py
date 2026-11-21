"""Native Agent replay and migration use the authenticated source branch and exact catalog identity."""

import hashlib
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote

import pytest

from test_web_tool_citations import load_functions


def session_namespace(messages, execution):
    namespace = {
        'hashlib': hashlib,
        'json': __import__('json'),
        'Chats': SimpleNamespace(
            get_messages_map_by_chat_id=AsyncMock(return_value=messages),
            get_agent_execution=AsyncMock(return_value=execution),
        ),
        'decode_token': lambda token: tokens[token],
    }
    tokens = {key: message.pop('signed') for key, message in messages.items() if 'signed' in message}
    load_functions(
        namespace, 'utils/agents/session.py', {'_state', '_find', 'get_resume_point', 'branch_digest', 'content_digest'}
    )
    return namespace


def sample(chat='chat', author='author', legacy=False):
    state = {
        ('purpose' if legacy else 'typ'): 'terminal-agent' if legacy else 'agent_session',
        'chat': chat,
        'user': author,
        'model': 'old.model',
        'terminal_id': 'term',
        'provider': 'codex',
        'cwd': '/work',
        'session_id': 'provider-id',
        'turn': 'checkpoint',
        'terminal_identity': 'identity',
        'terminal_context': 'scope',
    }
    return {
        'id': 'ref',
        'role': 'assistant',
        'user_id': author,
        'done': True,
        'parentId': None,
        'meta': {'agent': 'ref'},
        'signed': state,
    }


def completed():
    return {
        'state': 'completed',
        'chat_id': 'chat',
        'message_id': 'ref',
        'user_id': 'author',
        'provider': 'codex',
        'model': 'old.model',
        'terminal_id': 'term',
        'cwd': '/work',
        'remote_completed': True,
        'output_published': True,
        'terminal_identity': 'identity',
        'terminal_context': 'scope',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('chat,author', [('copy', 'author'), ('chat', 'other')])
async def test_copies_and_other_authors_never_receive_native_session(chat, author):
    ns = session_namespace({'ref': sample()}, completed())
    state, branched = await ns['get_resume_point'](SimpleNamespace(id=chat, user_id=author), 'ref', author)
    assert state is None and not branched


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_completed_checkpoint_accepts_current_and_legacy_signatures(legacy):
    ns = session_namespace({'ref': sample(legacy=legacy)}, completed())
    state, branched = await ns['get_resume_point'](SimpleNamespace(id='chat', user_id='author'), 'ref', 'author')
    assert state['session_id'] == 'provider-id' and not branched


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'record', [None, {'output_published': False}, {'cwd': '/different'}, {'terminal_context': None}]
)
async def test_incomplete_evidence_reconstructs_from_visible_text(record):
    execution = None if record is None else {**completed(), **record}
    ns = session_namespace({'ref': sample(legacy=True)}, execution)
    state, _ = await ns['get_resume_point'](SimpleNamespace(id='chat', user_id='author'), 'ref', 'author')
    assert state is None


def test_legacy_aliases_require_one_live_catalog_identity():
    ns = {'re': re, 'hashlib': hashlib, 'quote': quote}
    load_functions(ns, 'utils/agents/session.py', {'model_id', 'legacy_model_id'})
    load_functions(ns, 'utils/agents/discovery.py', {'resolve_catalog_model'})
    state = {'terminal_id': 'device:1', 'provider': 'codex', 'model': 'gpt.6'}
    item = {'id': ns['model_id'](state), 'agent': state}
    old = ns['legacy_model_id'](state)
    assert ns['resolve_catalog_model']([item], old) == item
    assert ns['resolve_catalog_model']([item], old.replace('device', 'other')) is None
    assert ns['resolve_catalog_model']([item, {**item, 'id': old, 'agent': {**state, 'model': 'other'}}], old) is None


def test_reference_migration_keeps_unverified_ids_and_other_terminal():
    ns = {'re': re, 'hashlib': hashlib, 'quote': quote}
    load_functions(ns, 'utils/agents/session.py', {'model_id', 'legacy_model_id'})
    load_functions(ns, 'utils/agents/discovery.py', {'resolve_catalog_model'})
    load_functions(ns, 'utils/agents/references.py', {'migrate_ids'})
    state = {'terminal_id': 'term', 'provider': 'codex', 'model': 'gpt.6'}
    new, old = ns['model_id'](state), ns['legacy_model_id'](state)
    missing = 'agent:term:codex:not-installed'
    other = 'agent:other:codex:gpt.6'
    assert ns['migrate_ids']([other, old, missing, 'ordinary'], [{'id': new, 'agent': state}], 'term') == (
        [other, new, missing, 'ordinary'],
        [missing],
    )


@pytest.mark.asyncio
async def test_sibling_checkpoint_requires_a_provider_fork_even_at_same_timestamp():
    original = sample()
    sibling = sample()
    sibling.update(id='next', parentId='ref', meta={'agent': 'next'}, timestamp=0)
    ns = session_namespace({'ref': original, 'next': sibling}, completed())
    _, branched = await ns['get_resume_point'](SimpleNamespace(id='chat', user_id='author'), 'ref', 'author')
    assert branched


@pytest.mark.asyncio
async def test_editing_the_signed_checkpoint_invalidates_native_replay():
    ns = {'hashlib': hashlib, 'json': __import__('json')}
    load_functions(ns, 'utils/agents/session.py', {'branch_digest', 'content_digest'})
    message = sample()
    previous = {'id': 'u', 'role': 'user', 'content': 'original', 'parentId': None, 'user_id': 'author'}
    message.update(parentId='u', content='reply', output=[])
    message['signed'].update(
        source_history_digest=ns['branch_digest']({'u': previous}, 'u'),
        checkpoint_digest=ns['content_digest'](['reply', []]),
    )
    previous['content'] = 'edited'
    ns = session_namespace({'ref': message, 'u': previous}, completed())
    state, _ = await ns['get_resume_point'](SimpleNamespace(id='chat', user_id='author'), 'ref', 'author')
    assert state is None


@pytest.mark.asyncio
async def test_saved_reference_migration_is_scoped_and_idempotent(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from open_webui.models.automations import Automation
    from open_webui.models.config import Config
    from open_webui.models.groups import Group
    from open_webui.models.users import User
    from open_webui.utils.agents import references
    from open_webui.utils.agents.session import model_id, legacy_model_id

    engine = create_async_engine(f'sqlite+aiosqlite:///{(tmp_path / "refs.db").as_posix()}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    state = {'terminal_id': 'terminal', 'provider': 'codex', 'model': 'gpt.6'}
    new, old = model_id(state), legacy_model_id(state)
    unresolved = 'agent:terminal:codex:absent'
    catalog = [{'id': new, 'agent': state, 'legacy_id': old}]
    monkeypatch.setattr(references, 'get_async_db', sessions)
    try:
        async with engine.begin() as connection:
            for table in (User, Automation, Config, Group):
                await connection.run_sync(table.__table__.create)
        async with sessions() as db:
            db.add_all(
                [
                    User(
                        id='admin',
                        name='admin',
                        role='admin',
                        settings={'ui': {'models': [old, unresolved], 'pinnedModels': [old]}},
                    ),
                    User(id='other', name='other', settings={'ui': {'models': [old]}}),
                    Group(id='g', name='group', data={'config': {'default_models': [old, unresolved]}}),
                    Config(key='ui.default_models', value=old),
                    Automation(
                        created_at=1,
                        updated_at=1,
                        id='matched',
                        user_id='admin',
                        name='matched',
                        data={'model_id': old, 'terminal': {'server_id': 'terminal', 'cwd': '/w'}},
                    ),
                    Automation(
                        created_at=1,
                        updated_at=1,
                        id='unresolved',
                        user_id='admin',
                        name='unresolved',
                        data={'model_id': unresolved, 'terminal': {'server_id': 'terminal', 'cwd': '/w'}},
                    ),
                    Automation(
                        created_at=1,
                        updated_at=1,
                        id='other-cwd',
                        user_id='admin',
                        name='other-cwd',
                        data={'model_id': old, 'terminal': {'server_id': 'terminal', 'cwd': '/other'}},
                    ),
                ]
            )
            await db.commit()
        for _ in range(2):
            await references.migrate_model_references(
                SimpleNamespace(id='admin', role='admin'), 'terminal', catalog, cwd='/w'
            )
        async with sessions() as db:
            assert (await db.get(User, 'admin')).settings['ui'] == {
                'models': [new, unresolved],
                'pinnedModels': [new],
                'agentModelsNeedingRebind': [unresolved],
            }
            assert (await db.get(User, 'other')).settings['ui']['models'] == [old]
            assert (await db.get(Config, 'ui.default_models')).value == new
            assert (await db.get(Group, 'g')).data['config']['default_models'] == [new, unresolved]
            assert (await db.get(Automation, 'matched')).data['model_id'] == new
            assert (await db.get(Automation, 'other-cwd')).data['model_id'] == old
            assert (await db.get(Automation, 'unresolved')).meta == {'agent_model_needs_rebind': True}
    finally:
        await engine.dispose()


def test_template_agent_binds_resolved_context_without_a_legacy_request_header():
    from fastapi import HTTPException
    from open_webui.utils.agents.access import check_terminal_binding
    from open_webui.utils.agents.transport import TerminalTransport

    connection = {
        'url': 'http://terminal.example.invalid',
        'template_id': 'sandbox',
        'scope': 'user',
        'context_id': 'user:author',
        'terminal_context': {'user_id': 'author'},
    }
    headers = {'X-Terminal-Template-Id': 'sandbox', 'X-User-Id': 'author'}
    transport = TerminalTransport(connection, headers, {}, 'author')
    assert transport.terminal_context == 'user:author'
    check_terminal_binding((connection, headers, {}), transport.terminal_identity, 'user:author')
    changed = {**connection, 'context_id': 'user:other'}
    with pytest.raises(HTTPException) as denial:
        check_terminal_binding((changed, headers, {}), transport.terminal_identity, 'user:author')
    assert denial.value.status_code == 409
