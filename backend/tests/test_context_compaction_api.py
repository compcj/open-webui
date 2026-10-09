"""Compaction API contracts, loaded without application import side effects."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field, ConfigDict, ValidationInfo, field_validator, model_validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]


def load_nodes(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def chat_form():
    from typing import Literal

    ns = {'BaseModel': BaseModel, 'Field': Field, 'Literal': Literal}
    return load_nodes('backend/open_webui/routers/chats.py', {'ChatConfigForm'}, ns)['ChatConfigForm']


def test_chat_config_accepts_explicit_v2_and_preserves_omission():
    form = chat_form()(CONTEXT_COMPACTION_MODE='v2')
    assert form.CONTEXT_COMPACTION_MODE == 'v2'
    assert form.model_dump(exclude_unset=True) == {'CONTEXT_COMPACTION_MODE': 'v2'}


@pytest.mark.parametrize(
    'field,value',
    [
        ('CONTEXT_COMPACTION_MODE', 'automatic'),
        ('CONTEXT_COMPACTION_DEFAULT_CONTEXT_WINDOW', 0),
        ('CONTEXT_COMPACTION_V2_MAX_OUTPUT_TOKENS', -1),
        ('CONTEXT_COMPACTION_V2_TIMEOUT_SECONDS', 0),
        ('CONTEXT_COMPACTION_V2_TRIGGER_PERCENT', 101),
        ('CONTEXT_COMPACTION_V2_MAX_RETRIES', 6),
    ],
)
def test_invalid_admin_values_rejected(field, value):
    with pytest.raises(ValidationError):
        chat_form()(**{field: value})


def test_model_context_window_is_metadata_and_round_trips():
    from typing import Any

    ns = dict(
        BaseModel=BaseModel,
        ConfigDict=ConfigDict,
        Field=Field,
        ValidationInfo=ValidationInfo,
        field_validator=field_validator,
        model_validator=model_validator,
        Any=Any,
        validate_image_url=lambda v, **kw: v,
        normalize_model_tags=lambda v: v,
        strip_extracted_content_from_model_knowledge=lambda v: v,
    )
    meta = load_nodes('backend/open_webui/models/models.py', {'ModelMeta'}, ns)['ModelMeta']
    assert meta().context_window is None
    assert meta.model_validate(meta(context_window=262144).model_dump()).context_window == 262144
    assert meta(context_window=None).context_window is None
    for invalid in (0, -1, True, '100', 1.5):
        with pytest.raises(ValidationError):
            meta(context_window=invalid)


def test_compaction_endpoints_require_expected_roles():
    tree = ast.parse((ROOT / 'backend/open_webui/routers/chats.py').read_text(encoding='utf-8'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)}
    for name in ('get_chat_config', 'set_chat_config'):
        assert 'Depends(get_admin_user)' in ast.unparse(functions[name])
    assert 'Depends(get_verified_user)' in ast.unparse(functions['compact_chat_by_id'])
    assert 'has_active_tasks' in ast.unparse(functions['compact_chat_by_id'])
    assert 'get_chat_by_id_for_user' in ast.unparse(functions['get_chat_context_usage_by_id'])
    assert 'get_compaction_models' in ast.unparse(functions['get_chat_context_usage_by_id'])
    assert 'check_model_access' in ast.unparse(functions['get_compaction_models'])


@pytest.fixture
def config_app():
    from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
    from open_webui.utils.context_compaction_config import CONFIG_FIELDS, CompactionSettings
    from typing import Literal

    values = {'chat.context_compaction.enable': True}

    class Storage:
        @staticmethod
        async def get_many(*keys):
            return {key: values[key] for key in keys if key in values}

        @staticmethod
        async def upsert(updates):
            values.update(updates)

    async def verified(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=authorization, role='admin' if authorization == 'admin' else 'user')

    async def admin(user=Depends(verified)):
        if user.role != 'admin':
            raise HTTPException(status_code=403)
        return user

    router = APIRouter()
    namespace = dict(
        BaseModel=BaseModel,
        Field=Field,
        Literal=Literal,
        ValidationError=ValidationError,
        Config=Storage,
        CONFIG_FIELDS=CONFIG_FIELDS,
        CompactionSettings=CompactionSettings,
        router=router,
        Depends=Depends,
        get_admin_user=admin,
        HTTPException=HTTPException,
    )
    namespace['CHAT_CONFIG_KEYS'] = {
        **{field: spec[1] for field, spec in CONFIG_FIELDS.items()},
        'ENABLE_CONTEXT_COMPACTION': 'chat.context_compaction.enable',
        'CONTEXT_COMPACTION_MODEL': 'chat.context_compaction.model',
        'CONTEXT_COMPACTION_TOKEN_THRESHOLD': 'chat.context_compaction.token_threshold',
        'CONTEXT_COMPACTION_TOKEN_CAP': 'chat.context_compaction.token_cap',
        'CONTEXT_COMPACTION_RETENTION_PERCENTAGE': 'chat.context_compaction.retention_percentage',
        'CONTEXT_COMPACTION_PROMPT_TEMPLATE': 'chat.context_compaction.prompt_template',
    }
    load_nodes(
        'backend/open_webui/routers/chats.py',
        {'ChatConfigForm', 'get_chat_config_values', 'chat_config_updates', 'get_chat_config', 'set_chat_config'},
        namespace,
    )
    app = FastAPI()
    app.include_router(router)
    return app, values


@pytest.mark.asyncio
async def test_admin_config_http_round_trip_and_legacy_client_preservation(config_app):
    import httpx

    app, values = config_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/config')).status_code == 401
        assert (
            await client.post('/config', headers={'authorization': 'user'}, json={'CONTEXT_COMPACTION_MODE': 'v2'})
        ).status_code == 403
        initial = await client.get('/config', headers={'authorization': 'admin'})
        assert initial.json()['CONTEXT_COMPACTION_MODE'] == 'legacy'
        saved = await client.post(
            '/config',
            headers={'authorization': 'admin'},
            json={
                'CONTEXT_COMPACTION_MODE': 'v2',
                'ENABLE_CONTEXT_COMPACTION': False,
                'CONTEXT_COMPACTION_V2_RECENT_TOKENS': 12345,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()['ENABLE_CONTEXT_COMPACTION'] is True
        assert saved.json()['CONTEXT_COMPACTION_MODE'] == 'v2'
        # Older clients cannot erase new mode/settings by sending only known fields.
        old = await client.post(
            '/config', headers={'authorization': 'admin'}, json={'ENABLE_CONTEXT_COMPACTION': False}
        )
        assert old.status_code == 200
        assert old.json()['CONTEXT_COMPACTION_MODE'] == 'v2'
        assert old.json()['CONTEXT_COMPACTION_V2_RECENT_TOKENS'] == 12345
        for mode in ('off', 'legacy', 'v2'):
            result = await client.post(
                '/config', headers={'authorization': 'admin'}, json={'CONTEXT_COMPACTION_MODE': mode}
            )
            assert result.json()['CONTEXT_COMPACTION_MODE'] == mode
        before = dict(values)
        invalid = await client.post(
            '/config', headers={'authorization': 'admin'}, json={'CONTEXT_COMPACTION_V2_EFFECTIVE_WINDOW_PERCENT': 80}
        )
        assert invalid.status_code == 422
        assert values == before


@pytest.fixture
def compaction_app():
    from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
    from unittest.mock import AsyncMock

    chat = SimpleNamespace(
        id='saved', chat={'history': {'messages': {}}, 'models': ['model']}, current_message_id='leaf'
    )
    state = SimpleNamespace(active=False, calls=[])

    async def verified(authorization: str | None = Header(default=None)):
        if not authorization:
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=authorization, role='user')

    async def owned(chat_id, user_id):
        return chat if chat_id == 'saved' and user_id == 'owner' else None

    async def accessible(chat_id, user):
        return await owned(chat_id, user.id)

    async def active(*args):
        return state.active

    async def model_access(user, model):
        if model['id'] == 'private':
            raise HTTPException(status_code=403)

    async def compact(request, user, chat, model_id, models):
        state.calls.append(model_id)
        if model_id == 'browser-model':
            assert request.state.direct is True
            assert request.state.model['id'] == model_id
            assert request.state.metadata == {
                'user_id': user.id,
                'session_id': 'owner-session',
                'chat_id': 'saved',
                'message_id': 'leaf',
            }
            assert model_id in models
            assert model_id not in request.app.state.MODELS
        return {'compacted': True, 'mode': 'v2'}

    async def usage(chat, model_id, models, *, current_id=None):
        return {
            'mode': 'v2',
            'model': model_id,
            'branch_id': current_id,
            'tokens': 100,
            'source': 'estimated:stored-history',
        }

    router = APIRouter()
    namespace = dict(
        BaseModel=BaseModel,
        Request=Request,
        HTTPException=HTTPException,
        status=status,
        router=router,
        Depends=Depends,
        get_verified_user=verified,
        Chats=SimpleNamespace(
            get_chat_by_id_and_user_id=owned,
            get_chat_by_id_for_user=accessible,
            get_messages_map_by_chat_id=AsyncMock(return_value={}),
        ),
        ERROR_MESSAGES=SimpleNamespace(NOT_FOUND='not found'),
        has_active_tasks=active,
        get_all_models=AsyncMock(),
        get_user_id_from_session_pool=lambda sid: 'owner' if sid == 'owner-session' else 'stranger',
        get_message_list=lambda *args: [],
        check_model_access=model_access,
        compact_chat_branch=compact,
        get_chat_context_usage=usage,
        publish_event=AsyncMock(),
        EVENTS=SimpleNamespace(CHAT_COMPACTED='chat:compacted'),
    )
    load_nodes(
        'backend/open_webui/routers/chats.py',
        {
            'CompactChatForm',
            'ContextUsageForm',
            'get_compaction_models',
            'compact_chat_by_id',
            'get_chat_context_usage_by_id',
            'query_chat_context_usage',
        },
        namespace,
    )
    app = FastAPI()
    app.state.redis = None
    app.state.MODELS = {'model': {'id': 'model'}, 'private': {'id': 'private'}}
    app.include_router(router)
    return app, state


@pytest.mark.asyncio
async def test_manual_and_usage_http_enforce_chat_model_access_and_active_state(compaction_app):
    import httpx

    app, state = compaction_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post('/saved/compact')).status_code == 401
        assert (await client.post('/saved/compact', headers={'authorization': 'other'})).status_code == 401
        headers = {'authorization': 'owner'}
        assert (await client.post('/saved/compact', headers=headers, json={'model': 'missing'})).status_code == 404
        assert (await client.post('/saved/compact', headers=headers, json={'model': 'private'})).status_code == 403
        state.active = True
        assert (await client.post('/saved/compact', headers=headers)).status_code == 409
        assert state.calls == []
        state.active = False
        success = await client.post('/saved/compact', headers=headers, json={'model': 'model'})
        assert success.status_code == 200, success.text
        assert success.json()['context_usage']['mode'] == 'v2'
        assert state.calls == ['model']
        assert (await client.get('/saved/context-usage?model=model')).status_code == 401
        assert (
            await client.get('/saved/context-usage?model=model', headers={'authorization': 'other'})
        ).status_code == 401
        assert (await client.get('/saved/context-usage?model=private', headers=headers)).status_code == 403
        result = await client.get('/saved/context-usage?model=model&message_id=sibling', headers=headers)
        assert result.status_code == 200
        assert result.json()['model'] == 'model'
        assert result.json()['branch_id'] == 'sibling'


@pytest.mark.asyncio
async def test_browser_direct_manual_and_usage_require_own_live_session(compaction_app):
    import httpx

    app, state = compaction_app
    body = {
        'model': 'browser-model',
        'model_item': {'id': 'browser-model', 'direct': True},
        'session_id': 'owner-session',
    }
    headers = {'authorization': 'owner'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for session_id in (None, 'another-session'):
            denied = await client.post('/saved/compact', headers=headers, json={**body, 'session_id': session_id})
            assert denied.status_code == 403
        mismatch = await client.post('/saved/compact', headers=headers, json={**body, 'model': 'other-model'})
        assert mismatch.status_code == 400
        success = await client.post('/saved/compact', headers=headers, json=body)
        assert success.status_code == 200, success.text
        assert state.calls == ['browser-model']
        for owner, session, expected in [
            ('other', 'owner-session', 401),
            ('owner', None, 200),
            ('owner', 'owner-session', 200),
        ]:
            usage = await client.post(
                '/saved/context-usage',
                headers={'authorization': owner},
                json={**body, 'session_id': session, 'message_id': 'sibling'},
            )
            assert usage.status_code == expected, usage.text
            if expected == 200:
                assert usage.json()['branch_id'] == 'sibling'
                assert usage.json()['model'] == 'browser-model'
        assert 'browser-model' not in app.state.MODELS
