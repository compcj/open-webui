"""Page-local contexts. History and derived V2 state live only in the context store."""

from __future__ import annotations

import asyncio
import json
import math
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, ValidationError

from open_webui.utils.auth import get_verified_user
from open_webui.utils.context_store_errors import ContextCapacity, ContextConflict, ContextStoreError
from open_webui.utils.temporary_contexts import (
    temporary_repository,
    sync_temporary_history,
    public_identity,
    public_state,
    resolve_temporary_scope,
)
from open_webui.utils.temporary_context_runtime import (
    authenticate_socket,
    bind_temporary_reply,
    acknowledge_temporary_task,
    stop_temporary_context,
)


class ContextRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def checked(request):
            try:
                if any(len(str(value).encode('utf-8')) > 128 for value in request.path_params.values()):
                    raise HTTPException(400, 'Invalid temporary context identifier.')
                return await handler(request)
            except ContextStoreError as error:
                return JSONResponse({'detail': error.detail()}, status_code=error.status_code)

        return checked


router = APIRouter(route_class=ContextRoute)


class IdentityForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    epoch: str = Field(min_length=1, max_length=128)


class SyncForm(IdentityForm):
    expected_revision: StrictInt = Field(ge=0)
    history: dict
    params: dict | None = None
    files: list[dict] | None = None


class LeaseForm(IdentityForm):
    rebind: StrictBool = False
    session_id: str = Field(min_length=1, max_length=128)


class ModelForm(IdentityForm):
    revision: StrictInt = Field(ge=0)
    model: str = Field(min_length=1, max_length=256)
    branch_id: str = Field(min_length=1, max_length=128)
    model_item: dict | None = None
    session_id: str | None = None


def finite_json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError('Non-finite JSON number')
    return parsed


async def read_form(request, model=None):
    limit = temporary_repository(request).config.context_bytes
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > limit:
            raise ContextCapacity(limit='request_bytes')
        payload.extend(chunk)
    try:
        data = json.loads(
            payload or b'{}',
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            parse_float=finite_json_float,
        )
        if not isinstance(data, dict):
            raise ValueError()
        return model.model_validate(data) if model else data
    except (ValueError, TypeError, ValidationError):
        raise HTTPException(400, {'code': 'temporary_context_invalid_request', 'retryable': False}) from None


async def check_temporary_permission(user):
    from open_webui.models.config import Config

    if user.role != 'admin' and not await Config.get('chat.temporary.enable', True):
        raise HTTPException(403, 'Temporary chats are disabled.')


async def authorize_files(user, history, files):
    from open_webui.models.files import Files
    from open_webui.utils.access_control.files import has_access_to_file

    candidates = list(files or [])
    for message in (history.get('messages') or {}).values():
        if isinstance(message, dict):
            candidates.extend(message.get('files') or [])
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise HTTPException(400, 'Invalid file reference.')
        file_id = (item.get('file') or {}).get('id') if isinstance(item.get('file'), dict) else None
        file_id = file_id or (item.get('id') if item.get('type') in ('file', 'image', 'audio', 'video') else None)
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        file = await Files.get_file_by_id(file_id)
        if not file or not (
            file.user_id == user.id or user.role == 'admin' or await has_access_to_file(file_id, 'read', user)
        ):
            raise HTTPException(403, 'File access denied.')


@router.post('/')
async def create_context(request: Request, user=Depends(get_verified_user)):
    await check_temporary_permission(user)
    form = await read_form(request)
    if set(form) - {'replaces'}:
        raise HTTPException(400, 'Unexpected context fields.')
    repository = temporary_repository(request)
    if previous := form.get('replaces'):
        if (
            not isinstance(previous, dict)
            or not isinstance(previous.get('id'), str)
            or not isinstance(previous.get('epoch'), str)
        ):
            raise HTTPException(400, 'Invalid previous context.')
        old = await repository.read(previous['id'], user.id, previous['epoch'], allow_closed=True)
        if not old['rebuild_allowed'] or old['status'] not in ('closed_confirmed', 'tombstone'):
            raise ContextConflict()
    return public_identity(await repository.create(user.id), repository.config)


@router.post('/{id}/sync')
async def sync_context(id: str, request: Request, user=Depends(get_verified_user)):
    await check_temporary_permission(user)
    form = await read_form(request, SyncForm)
    repository = temporary_repository(request)
    await repository.read(id, user.id, form.epoch)
    from open_webui.utils.temporary_contexts import validate_temporary_history

    history = validate_temporary_history(form.history)
    await authorize_files(user, history, form.files)
    return await sync_temporary_history(
        repository, user.id, id, form.epoch, form.expected_revision, history, params=form.params, files=form.files
    )


@router.post('/{id}/lease')
async def lease_context(id: str, request: Request, user=Depends(get_verified_user)):
    form = await read_form(request, LeaseForm)
    repository = temporary_repository(request)
    await repository.read(id, user.id, form.epoch)
    await authenticate_socket(user.id, id, form.session_id)
    doc = await repository.lease(id, user.id, form.epoch, socket_id=form.session_id, rebind=form.rebind)
    return public_identity(doc, repository.config)


class RpcClaimForm(IdentityForm):
    socket_generation: StrictInt = Field(ge=0)
    task_id: str = Field(min_length=1, max_length=256)
    reply_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    seq: StrictInt = Field(ge=1)


@router.post('/{id}/rpc/{rpc_id}/claim')
async def claim_rpc(id: str, rpc_id: str, request: Request, user=Depends(get_verified_user)):
    form = await read_form(request, RpcClaimForm)
    from open_webui.utils.temporary_context_runtime import claim_temporary_rpc

    return await claim_temporary_rpc(
        temporary_repository(request), id, user.id, form.epoch, rpc_id, form.model_dump(exclude={'epoch'})
    )


@router.get('/{id}/state')
async def context_state(
    id: str,
    request: Request,
    epoch: str = Query(min_length=1, max_length=128),
    after_seq: int = Query(default=0, ge=0),
    user=Depends(get_verified_user),
):
    repository = temporary_repository(request)
    doc = await repository.read(id, user.id, epoch, allow_closed=True)
    return public_state(doc, repository.config, after_seq=after_seq)


async def model_context(id, request, user, form, *, require_session=False):
    await check_temporary_permission(user)
    store, doc = await resolve_temporary_scope(
        request,
        user,
        dict(id=id, epoch=form.epoch, revision=form.revision, branch_id=form.branch_id),
        model_id=form.model,
        require_revision=True,
    )
    snapshot = doc['data'].get('snapshot') or {}
    chat = SimpleNamespace(
        id=store.scope.store_key,
        current_message_id=form.branch_id,
        user_id=user.id,
        chat={
            **snapshot.get('chat', {}),
            'history': {'currentId': form.branch_id, 'messages': snapshot.get('messages_map', {})},
        },
    )
    if require_session:
        await authenticate_socket(user.id, id, form.session_id)
        if doc['socket']['id'] != form.session_id or not doc['socket']['connected']:
            raise ContextConflict()
    from open_webui.routers.chats import get_compaction_models

    models = await get_compaction_models(request, user, chat, form.model, form, require_session=require_session)
    return store, doc, chat, models


@router.post('/{id}/context-usage')
async def context_usage(id: str, request: Request, user=Depends(get_verified_user)):
    form = await read_form(request, ModelForm)
    store, _, chat, models = await model_context(id, request, user, form)
    if models[form.model].get('pipe'):
        return None
    from open_webui.utils.context_compaction_v2 import get_v2_chat_context_usage

    return await get_v2_chat_context_usage(chat, form.model, models, current_id=form.branch_id, context_store=store)


@router.post('/{id}/compact')
async def compact_context(id: str, request: Request, user=Depends(get_verified_user)):
    form = await read_form(request, ModelForm)
    store, doc, chat, models = await model_context(id, request, user, form, require_session=True)
    if models[form.model].get('pipe'):
        return {'ok': True, 'compacted': False, 'reason': 'unsupported_model'}
    from open_webui.utils.context_compaction_config import load_compaction_settings

    if (await load_compaction_settings()).mode != 'v2':
        return {'ok': True, 'compacted': False, 'reason': 'disabled'}
    repository = temporary_repository(request)
    task_id = f'temporary.{id}.{uuid4().hex}'
    _, tasks = await repository.register_tasks(
        id,
        user.id,
        form.epoch,
        [
            dict(
                task_id=task_id,
                reply_id=form.branch_id,
                model_id=form.model,
                branch_id=form.branch_id,
                worker_nonce=uuid4().hex,
            )
        ],
        expected_revision=form.revision,
        exclusive=True,
    )
    scope = store.scope.for_reply(
        reply_id=form.branch_id, model_id=form.model, task_id=task_id, fence=tasks[0]['fence']
    )
    metadata = {
        'user_id': user.id,
        'chat_id': chat.id,
        'message_id': form.branch_id,
        'task_id': task_id,
        'session_id': form.session_id,
    }
    bind_temporary_reply(request, metadata, {'scope': scope, 'socket_generation': doc['socket']['generation']})
    request.state.metadata = metadata
    from open_webui.utils.context_compaction_v2 import compact_v2_chat_branch
    from open_webui.tasks import create_task

    async def operation():
        return await compact_v2_chat_branch(
            request, user, chat, form.model, models, context_store=request.state.context_store
        )

    _, task = await create_task(
        request.app.state.redis,
        operation(),
        id=chat.id,
        task_id=task_id,
        on_complete=lambda: acknowledge_temporary_task(metadata),
    )
    try:
        return await task
    except asyncio.CancelledError:
        raise HTTPException(409, {'code': 'temporary_context_compaction_cancelled', 'retryable': True}) from None


@router.get('/{id}/tool-results/{result_id}')
async def tool_result(
    id: str,
    result_id: str,
    request: Request,
    epoch: str = Query(min_length=1, max_length=128),
    branch_id: str = Query(min_length=1, max_length=128),
    query: str | None = Query(default=None, max_length=1024),
    cursor: str | None = Query(default=None, max_length=1024),
    max_tokens: int = Query(default=2048, ge=1, le=8192),
    user=Depends(get_verified_user),
):
    repository = temporary_repository(request)
    doc = await repository.read(id, user.id, epoch)
    store, _ = await resolve_temporary_scope(
        request, user, dict(id=id, epoch=epoch, revision=doc['revision'], branch_id=branch_id)
    )
    from open_webui.utils.tool_result_store import read_tool_result_page

    return await read_tool_result_page(
        user,
        store.scope.store_key,
        result_id,
        branch_id=branch_id,
        query=query,
        cursor=cursor,
        max_tokens=max_tokens,
        context_store=store,
    )


@router.delete('/{id}')
async def close_context(
    id: str, request: Request, epoch: str = Query(min_length=1, max_length=128), user=Depends(get_verified_user)
):
    doc, result = await stop_temporary_context(request, user, id, epoch, close=True)
    return {
        **public_identity(doc, temporary_repository(request).config),
        'retryable': result['retryable'],
        'task_ids': result['task_ids'],
    }
