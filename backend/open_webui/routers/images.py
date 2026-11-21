from __future__ import annotations

import asyncio
import base64
import io
import logging
import mimetypes
import re
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from urllib.parse import quote, urlparse

import aiofiles
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from open_webui.config import (
    CACHE_DIR,
    ENABLE_OPENAI_IMAGE_EDIT_NORMALIZATION,
    IMAGE_AUTO_SIZE_MODELS_REGEX_PATTERN,
    IMAGE_URL_RESPONSE_MODELS_REGEX_PATTERN,
)
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import AIOHTTP_CLIENT_ALLOW_REDIRECTS, AIOHTTP_CLIENT_SESSION_SSL
from open_webui.events import EVENTS, publish_event
from open_webui.internal.db import get_async_session
from open_webui.models.chats import Chats
from open_webui.models.config import Config
from open_webui.retrieval.web.utils import assert_identity_encoding, get_ssrf_safe_session, validate_url
from open_webui.routers.files import get_file_content_by_id, upload_file_handler
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.headers import get_headers_and_cookies
from open_webui.utils.images.comfyui import (
    ComfyUICreateImageForm,
    ComfyUIError,
    ComfyUIEditImageForm,
    ComfyUIWorkflow,
    comfyui_create_image,
    comfyui_edit_image,
    comfyui_upload_image,
)
from open_webui.utils.images.connections import (
    DEFAULT_GEMINI_IMAGE_MODEL,
    DEFAULT_OPENAI_IMAGE_MODEL,
    IMAGE_CONNECTIONS_CONFIG_KEY,
    IMAGE_EDIT_DEFAULT_MODEL_CONFIG_KEY,
    IMAGE_GENERATION_DEFAULT_MODEL_CONFIG_KEY,
    IMAGE_SHORTCUTS_CONFIG_KEY,
    get_image_models,
    migrate_legacy_image_config,
    get_openai_connection_models,
    normalize_image_connection,
    resolve_image_model,
)
from open_webui.utils.images.engines import (
    build_comfyui_edit_options,
    build_comfyui_payload,
    build_gemini_edit_payload,
    build_gemini_request,
    build_grok_edit_payload,
    build_grok_payload,
    build_openai_payload,
    describe_profile_validation_error,
    redact_image_metadata,
)
from open_webui.utils.images.profiles import legacy_profile_projection, migrate_named_profiles, normalize_shortcuts
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.session_pool import get_session
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# An image can lie as easily as it can illuminate. Let what
# is generated here be honest about what it shows.
IMAGE_CACHE_DIR = CACHE_DIR / 'image' / 'generations'
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter()

IMAGE_FILE_EXTENSIONS = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/mpo': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
}

IMAGE_CONFIG_KEYS = {
    'ENABLE_IMAGE_GENERATION': 'image_generation.enable',
    'ENABLE_IMAGE_PROMPT_GENERATION': 'image_generation.prompt.enable',
    'ENABLE_IMAGE_EDIT': 'images.edit.enable',
    'IMAGE_GENERATION_DEFAULT_MODEL': IMAGE_GENERATION_DEFAULT_MODEL_CONFIG_KEY,
    'IMAGE_EDIT_DEFAULT_MODEL': IMAGE_EDIT_DEFAULT_MODEL_CONFIG_KEY,
    'IMAGE_CONNECTIONS': IMAGE_CONNECTIONS_CONFIG_KEY,
    'IMAGE_PROFILES': IMAGE_SHORTCUTS_CONFIG_KEY,
    'IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX': 'image_generation.tool_description_suffix',
    'IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX': 'images.edit.tool_description_suffix',
    'USER_PERMISSIONS': 'user.permissions',
}


async def get_config_values(key_map: dict[str, str]) -> dict:
    values = await Config.get_many(*key_map.values())
    return {field: values[storage_key] for field, storage_key in key_map.items() if storage_key in values}


async def get_image_config() -> SimpleNamespace:
    return SimpleNamespace(**await get_config_values(IMAGE_CONFIG_KEYS))


def config_updates(data: dict, key_map: dict[str, str]) -> dict:
    return {key_map[field]: value for field, value in data.items() if field in key_map}


def normalize_openai_edit_image_data_url(data_url: str) -> str:
    if not data_url.startswith('data:') or ',' not in data_url:
        return data_url

    header, encoded = data_url.split(',', 1)
    mime_type = header.split(';')[0].lstrip('data:').lower()
    if mime_type not in {'image/jpeg', 'image/jpg', 'image/mpo'}:
        return data_url

    try:
        image_bytes = base64.b64decode(encoded)
        with Image.open(io.BytesIO(image_bytes)) as image:
            orientation = image.getexif().get(274)
            needs_normalization = (
                mime_type == 'image/mpo'
                or image.format == 'MPO'
                or getattr(image, 'n_frames', 1) > 1
                or orientation not in (None, 1)
                or image.mode not in ('RGB', 'L')
            )

            if not needs_normalization:
                return data_url

            image.seek(0)
            image = ImageOps.exif_transpose(image)
            if image.mode != 'RGB':
                image = image.convert('RGB')

            output = io.BytesIO()
            image.save(output, format='JPEG', quality=95)
            normalized_image = base64.b64encode(output.getvalue()).decode('utf-8')
            return f'data:image/jpeg;base64,{normalized_image}'
    except Exception as e:
        log.debug('Image edit normalization skipped: %s', e)

    return data_url


def get_image_data_url(data: bytes) -> str:
    """Validate image bytes and encode them with their detected MIME type."""
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in {'PNG', 'JPEG', 'MPO', 'WEBP', 'GIF'}:
            raise ValueError('Image must be PNG, JPEG, WebP, or GIF.')
        if image.width * image.height > 25_000_000:
            raise ValueError('Image must be at most 25 megapixels.')
        mime_type = Image.MIME[image.format]
        image.verify()
    # verify() does not decode pixels for every format.
    with Image.open(io.BytesIO(data)) as image:
        image.load()
    return f'data:{mime_type};base64,{base64.b64encode(data).decode("utf-8")}'


def get_image_file_item(base64_string, param_name='image'):
    header, encoded = base64_string.split(',', 1)
    mime_type = header.split(';')[0].lstrip('data:') or 'image/png'
    image_data = base64.b64decode(encoded)
    extension = IMAGE_FILE_EXTENSIONS.get(mime_type.lower()) or mimetypes.guess_extension(mime_type) or '.png'
    return (
        param_name,
        (
            f'{uuid.uuid4()}{extension}',
            io.BytesIO(image_data),
            mime_type,
        ),
    )


def get_automatic1111_api_auth(api_auth: str | None) -> str:
    if not api_auth:
        return ''
    return f'Basic {base64.b64encode(api_auth.encode("utf-8")).decode("utf-8")}'


async def set_automatic1111_model(connection: dict, model: str) -> None:
    """AUTOMATIC1111 holds one checkpoint instance-wide; switch it when the request needs another."""
    if not model:
        return

    headers = {'authorization': get_automatic1111_api_auth(connection.get('key'))}
    session = await get_session()
    async with session.get(
        url=f'{connection["url"]}/sdapi/v1/options',
        headers=headers,
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as r:
        r.raise_for_status()
        options = await r.json()

    if model != options.get('sd_model_checkpoint'):
        log.info('Switching AUTOMATIC1111 checkpoint to %s', model)
        async with session.post(
            url=f'{connection["url"]}/sdapi/v1/options',
            json={**options, 'sd_model_checkpoint': model},
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as r:
            r.raise_for_status()


class ImageConnection(BaseModel):
    id: str | None = None
    name: str = ''
    type: str = 'openai'
    url: str = ''
    key: str = ''
    config: dict = {}

    model_config = ConfigDict(extra='allow')


class ImagesConfig(BaseModel):
    ENABLE_IMAGE_GENERATION: bool
    ENABLE_IMAGE_PROMPT_GENERATION: bool
    ENABLE_IMAGE_EDIT: bool
    IMAGE_GENERATION_DEFAULT_MODEL: str = ''
    IMAGE_EDIT_DEFAULT_MODEL: str = ''
    IMAGE_CONNECTIONS: list[ImageConnection] = []
    IMAGE_PROFILES: list[dict] = []
    IMAGE_GENERATION_ENGINES: list[dict] = []
    IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX: str = ''
    IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX: str = ''


@router.get('/config', response_model=ImagesConfig)
async def get_config(request: Request, user=Depends(get_admin_user)):
    connections = await migrate_legacy_image_config()
    values = await get_config_values(IMAGE_CONFIG_KEYS)
    values.pop('USER_PERMISSIONS', None)
    profiles = values.get('IMAGE_PROFILES') or []
    for field in ('IMAGE_GENERATION_TOOL_DESCRIPTION_SUFFIX', 'IMAGE_EDIT_TOOL_DESCRIPTION_SUFFIX'):
        values[field] = (values.get(field) or '').strip()
    return {
        **values,
        'IMAGE_CONNECTIONS': connections,
        'IMAGE_PROFILES': profiles,
        'IMAGE_GENERATION_ENGINES': legacy_profile_projection(profiles, connections),
    }


@router.post('/config/update')
async def update_config(request: Request, form_data: ImagesConfig, user=Depends(get_admin_user)):
    connections = await migrate_legacy_image_config()
    updates = config_updates(
        form_data.model_dump(
            exclude_unset=True, exclude={'IMAGE_CONNECTIONS', 'IMAGE_PROFILES', 'IMAGE_GENERATION_ENGINES'}
        ),
        IMAGE_CONFIG_KEYS,
    )
    for key in ('image_generation.tool_description_suffix', 'images.edit.tool_description_suffix'):
        if key in updates:
            updates[key] = updates[key].strip()

    fields = form_data.model_fields_set
    try:
        if 'IMAGE_CONNECTIONS' in fields:
            connections = [normalize_image_connection(item.model_dump()) for item in form_data.IMAGE_CONNECTIONS]
            updates[IMAGE_CONNECTIONS_CONFIG_KEY] = connections
        if 'IMAGE_PROFILES' in fields:
            updates[IMAGE_SHORTCUTS_CONFIG_KEY] = normalize_shortcuts(form_data.IMAGE_PROFILES)
        elif 'IMAGE_GENERATION_ENGINES' in fields:
            # Old admin clients can edit only shortcuts which can be projected back to their format.
            current = await Config.get(IMAGE_SHORTCUTS_CONFIG_KEY) or []
            projected = legacy_profile_projection(current, connections)
            old_connections, old_profiles = migrate_named_profiles(projected)
            owned = {item['id'] for item in old_connections}
            old_ids = {item['id'] for item in old_profiles}
            replacements, new_profiles = migrate_named_profiles(form_data.IMAGE_GENERATION_ENGINES)
            previous = {item['id']: item for item in connections}
            connections = [item for item in connections if item['id'] not in owned]
            for item in replacements:
                if item['id'] in owned:
                    # The old form has no ACL, headers or enable fields. Keep their current admin values.
                    item['config'] = {
                        **previous[item['id']]['config'],
                        **{k: v for k, v in item['config'].items() if k not in ('access_grants', 'capabilities')},
                    }
                connections.append(normalize_image_connection(item))
            updates[IMAGE_CONNECTIONS_CONFIG_KEY] = connections
            updates[IMAGE_SHORTCUTS_CONFIG_KEY] = normalize_shortcuts(
                [item for item in current if item['id'] not in old_ids] + new_profiles
            )
        if len({item['id'] for item in connections}) != len(connections):
            raise ValueError('Image connection IDs must be unique')
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, describe_profile_validation_error(exc)) from None
    await Config.upsert(updates)
    values = await get_config(request, user)
    await publish_event(
        request,
        EVENTS.CONFIG_UPDATED,
        actor=user,
        subject_id='images',
        data={
            'image_generation_enabled': values.get('ENABLE_IMAGE_GENERATION'),
            'image_edit_enabled': values.get('ENABLE_IMAGE_EDIT'),
            'image_connections': [
                {'id': connection['id'], 'name': connection['name'], 'type': connection['type']}
                for connection in connections
            ],
        },
    )
    return values


async def get_gemini_connection_models(connection: dict) -> list[dict]:
    session = await get_session()
    async with session.get(
        url=f'{connection["url"]}/models',
        headers={'x-goog-api-key': connection.get('key', '')},
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as r:
        await raise_for_provider_status(r)
        res = await r.json(content_type=None)

    models = []
    for model in res.get('models') or []:
        model_id = str(model.get('name') or '').removeprefix('models/')
        if not model_id:
            continue
        methods = model.get('supportedGenerationMethods') or []
        models.append(
            {
                'id': model_id,
                'name': model.get('displayName') or model_id,
                'image': 'predict' in methods or 'image' in model_id,
            }
        )
    return models or [{'id': DEFAULT_GEMINI_IMAGE_MODEL, 'name': DEFAULT_GEMINI_IMAGE_MODEL, 'image': True}]


async def get_comfyui_connection_models(connection: dict) -> list[dict]:
    config = connection.get('config') or {}
    headers = {'Authorization': f'Bearer {connection["key"]}'} if connection.get('key') else {}
    session = await get_session()
    async with session.get(
        url=f'{connection["url"]}/object_info', headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL
    ) as r:
        await raise_for_provider_status(r)
        info = await r.json(content_type=None)

    def options(class_type: str, key: str | None = None) -> list[dict]:
        required = ((info.get(class_type) or {}).get('input') or {}).get('required') or {}
        key = key or next((name for name in required if '_name' in name), None)
        values = (required.get(key) or [[]])[0] if key else []
        return [{'id': value, 'name': value} for value in values if isinstance(value, str)]

    workflow_json = config.get('workflow') or config.get('edit_workflow')
    nodes = config.get('nodes') if config.get('workflow') else config.get('edit_nodes')
    model_node_id = next(
        (node['node_ids'][0] for node in nodes or [] if node.get('type') == 'model' and node.get('node_ids')),
        None,
    )
    if workflow_json and model_node_id:
        workflow = JSONCodec.loads(workflow_json)
        class_type = (workflow.get(model_node_id) or {}).get('class_type')
        if class_type:
            return options(class_type)
    return options('CheckpointLoaderSimple', 'ckpt_name')


async def get_automatic1111_connection_models(connection: dict) -> list[dict]:
    session = await get_session()
    async with session.get(
        url=f'{connection["url"]}/sdapi/v1/sd-models',
        headers={'authorization': get_automatic1111_api_auth(connection.get('key'))},
        ssl=AIOHTTP_CLIENT_SESSION_SSL,
    ) as r:
        await raise_for_provider_status(r)
        models = await r.json(content_type=None)
    return [{'id': model['title'], 'name': model['model_name']} for model in models]


@router.post('/connections/verify')
async def verify_image_connection(request: Request, form_data: ImageConnection, user=Depends(get_admin_user)):
    """Reach a connection before saving it and list the models it serves."""
    try:
        connection = normalize_image_connection(form_data.model_dump())
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(e))

    if not connection['url']:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.INVALID_URL)

    try:
        if connection['type'] in ('openai', 'grok'):
            return await get_openai_connection_models(request, connection, user)
        if connection['type'] == 'gemini':
            return await get_gemini_connection_models(connection)
        if connection['type'] == 'comfyui':
            return await get_comfyui_connection_models(connection)
        return await get_automatic1111_connection_models(connection)
    except Exception as e:
        log.warning('Failed to verify image connection %s (%s)', connection['id'], type(e).__name__)
        error = image_provider_error(connection, e)
        raise HTTPException(
            status_code=400,
            detail=ERROR_MESSAGES.DEFAULT(error, 'Failed to connect to the image generation engine'),
        )


class ConnectionVerificationForm(BaseModel):
    engine: str
    url: str
    key: str | None = None


@router.post('/verify')
async def verify_connection(form_data: ConnectionVerificationForm, user=Depends(get_admin_user)):
    url = form_data.url.rstrip('/')
    headers = {}
    if form_data.engine == 'automatic1111':
        url = f'{url}/sdapi/v1/options'
        if form_data.key is not None:
            headers['Authorization'] = f'Basic {base64.b64encode(form_data.key.encode("utf-8")).decode("utf-8")}'
    elif form_data.engine == 'comfyui':
        url = f'{url}/object_info'
        if form_data.key:
            headers['Authorization'] = f'Bearer {form_data.key}'
    else:
        raise HTTPException(status_code=400, detail='Unsupported image engine')

    try:
        session = await get_session()
        async with session.get(url=url, headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL) as r:
            r.raise_for_status()
            return True
    except Exception:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.INVALID_URL)


@router.get('/engines')
async def get_image_generation_engines(request: Request, user=Depends(get_verified_user)):
    image_config = await get_image_config()
    if not image_config.ENABLE_IMAGE_GENERATION:
        return []
    if user.role != 'admin' and not await has_permission(
        user.id, 'features.image_generation', image_config.USER_PERMISSIONS
    ):
        raise HTTPException(403, ERROR_MESSAGES.ACCESS_PROHIBITED)
    allowed = {item['id'] for item in await get_image_models(request, user) if item['image_generation']}
    return [
        {'id': item['id'], 'name': item['name']}
        for item in (await Config.get(IMAGE_SHORTCUTS_CONFIG_KEY) or [])
        if item['generation_model'] in allowed
    ]


@router.get('/models')
async def get_models(request: Request, user=Depends(get_verified_user)):
    """Image models this user can pick from, with what each can do."""
    image_config = await get_image_config()
    if not (image_config.ENABLE_IMAGE_GENERATION or image_config.ENABLE_IMAGE_EDIT):
        return []

    if user.role != 'admin' and not await has_permission(
        user.id, 'features.image_generation', image_config.USER_PERMISSIONS
    ):
        return []

    return [
        {key: value for key, value in model.items() if key != 'connection'}
        for model in await get_image_models(request, user)
    ]


@router.get('/connections/models')
async def get_connection_models(request: Request, user=Depends(get_admin_user)):
    """Every model the enabled connections offer, whoever may use them, for choosing the default models."""
    return [
        {key: value for key, value in model.items() if key != 'connection'}
        for model in await get_image_models(request, user, bypass_access=True)
    ]


class CreateImageForm(BaseModel):
    engine_id: str | None = None
    model: str | None = None
    prompt: str
    size: str | None = None
    n: int = 1
    steps: int | None = None
    negative_prompt: str | None = None


GenerateImageForm = CreateImageForm  # Alias for backward compatibility


async def raise_for_provider_status(r: aiohttp.ClientResponse) -> None:
    """Like raise_for_status, but keeps the provider's own error message (e.g. "Invalid size")."""
    if r.ok:
        return

    detail = ''
    try:
        text = await r.text()
        try:
            body = JSONCodec.loads(text)
        except Exception:
            body = None
        error = body.get('error') if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = error.get('message') or ''
        elif isinstance(error, str):
            detail = error
        elif isinstance(body, dict) and isinstance(body.get('message'), str):
            detail = body['message']
        else:
            detail = text.strip()
    except Exception:
        pass

    message = f'{r.status} {r.reason}'
    if detail:
        message = f'{message}: {detail[:500]}'
    raise aiohttp.ClientResponseError(r.request_info, r.history, status=r.status, message=message, headers=r.headers)


def image_provider_error(connection: dict, error: Exception):
    """Retain provider status detail while keeping connection credentials out of end-user messages."""
    if not isinstance(error, aiohttp.ClientResponseError):
        return error
    detail = error.message or ''
    secrets = [connection.get('key'), urlparse(connection['url']).password]
    for name, value in (connection.get('config', {}).get('headers') or {}).items():
        if isinstance(value, str):
            secrets.append(value)
            if name.lower() == 'authorization':
                secrets.append(value.partition(' ')[2])
    for secret in secrets:
        if secret:
            detail = detail.replace(secret, '[redacted]')
    return detail


def _is_same_origin(url: str, base_url: str) -> bool:
    """Compare scheme + hostname + port of two URLs.

    Pure string-prefix matching (``startswith``) is vulnerable to
    userinfo injection (``http://host:port@evil.com/``) and suffix
    confusion (``http://host:portevil.com/``).  Parsing both URLs
    and comparing the three origin components eliminates those
    attack vectors.
    """

    def _default_port(scheme: str) -> int:
        return 443 if scheme == 'https' else 80

    parsed = urlparse(url)
    trusted = urlparse(base_url)
    return (
        parsed.scheme == trusted.scheme
        and parsed.hostname == trusted.hostname
        and (parsed.port or _default_port(parsed.scheme)) == (trusted.port or _default_port(trusted.scheme))
    )


async def get_image_data(data: str, headers=None, trusted_base_url: str | None = None):
    try:
        if data.startswith('http://') or data.startswith('https://'):
            # Defense-in-depth: gate before fetch (mirrors load_url_image).
            # For URLs originating from an admin-configured backend (e.g.
            # ComfyUI on a private network), skip SSRF validation only when
            # the URL shares the exact same origin (scheme + host + port)
            # as the admin-configured base.  This avoids both the global
            # ENABLE_LOCAL_WEB_FETCH hammer and a blanket trust flag
            # that would follow arbitrary redirects.
            if trusted_base_url and _is_same_origin(data, trusted_base_url):
                session_context = nullcontext(await get_session())
            else:
                await asyncio.to_thread(validate_url, data)
                session_context = get_ssrf_safe_session()
            async with (
                session_context as session,
                session.get(
                    data,
                    headers=headers,
                    allow_redirects=False if (headers or trusted_base_url) else AIOHTTP_CLIENT_ALLOW_REDIRECTS,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r,
            ):
                r.raise_for_status()
                content_type = r.headers.get('content-type', '')
                if content_type.split('/')[0] == 'image':
                    return await r.read(), content_type
                else:
                    log.error('Url does not point to an image.')
                    return None, None
        else:
            if ',' in data:
                header, encoded = data.split(',', 1)
                mime_type = header.split(';')[0].lstrip('data:')
                img_data = base64.b64decode(encoded)
            else:
                img_data = base64.b64decode(data)
                with Image.open(io.BytesIO(img_data)) as image:
                    mime_type = Image.MIME.get(image.format, 'image/png')
            return img_data, mime_type
    except Exception as e:
        log.warning('Error loading image data (%s)', type(e).__name__)
        return None, None


async def upload_image(request, image_data, content_type, metadata, user, db=None, name='generated-image'):
    if image_data is None or content_type is None:
        raise ValueError('Failed to retrieve image data from the generation backend')
    image_format = IMAGE_FILE_EXTENSIONS.get(content_type.lower()) or mimetypes.guess_extension(content_type) or '.png'
    file = UploadFile(
        file=io.BytesIO(image_data),
        filename=f'{name}{image_format}',  # will be converted to a unique ID on upload_file
        headers={
            'content-type': content_type,
        },
    )
    file_item = await upload_file_handler(
        request,
        file=file,
        metadata=redact_image_metadata(metadata),
        process=False,
        user=user,
    )

    if file_item and file_item.id:
        # If chat_id and message_id are provided in metadata, link the file to the chat message
        chat_id = metadata.get('chat_id')
        message_id = metadata.get('message_id')

        if chat_id and message_id:
            await Chats.insert_chat_files(
                chat_id=chat_id,
                message_id=message_id,
                file_ids=[file_item.id],
                user_id=user.id,
                db=db,
            )

    url = request.app.url_path_for('get_file_content_by_id', id=file_item.id)
    return file_item, {
        'id': file_item.id,
        'url': url,
        'name': (file_item.meta or {}).get('name') or file_item.filename,
        'content_type': (file_item.meta or {}).get('content_type'),
    }


@router.post('/generations')
async def generate_images(request: Request, form_data: CreateImageForm, user=Depends(get_verified_user)):
    image_config = await get_image_config()
    if not image_config.ENABLE_IMAGE_GENERATION:
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role != 'admin' and not await has_permission(
        user.id, 'features.image_generation', image_config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = await image_generations(request, form_data, user=user, strict=True)
    await publish_event(
        request,
        EVENTS.IMAGE_GENERATED,
        actor=user,
        subject_id=None,
        subject_type='image',
        data={
            'model': form_data.model,
            'size': form_data.size,
            'n': form_data.n,
            'prompt_preview': form_data.prompt[:300],
        },
    )
    return result


async def image_generations(
    request: Request,
    form_data: CreateImageForm,
    metadata: dict | None = None,
    user=None,
    strict: bool = False,
):
    connection, model = await resolve_image_model(
        request, user, 'image_generation', form_data.model, strict=strict, engine_id=form_data.engine_id
    )
    connection_config = connection.get('config') or {}
    connection_size = connection_config.get('size') or ''
    connection_steps = connection_config.get('steps')
    provider_config = SimpleNamespace(
        IMAGE_GENERATION_PARAMS=connection_config.get('params'),
        IMAGE_SIZE=connection_size,
        IMAGE_STEPS=connection_steps,
        IMAGES_GEMINI_ENDPOINT_METHOD=connection_config.get('endpoint_method'),
    )

    # An 'auto' size has no width and height, so engines that need them fall back to 512x512.
    size = '512x512'
    if 'x' in connection_size:
        size = connection_size

    if form_data.size and 'x' in form_data.size:
        size = form_data.size

    width, height = tuple(map(int, size.split('x')))

    metadata = metadata or {}

    try:
        if connection['type'] in ('openai', 'grok'):
            model = model or ('grok-imagine-image' if connection['type'] == 'grok' else DEFAULT_OPENAI_IMAGE_MODEL)
            headers, cookies = await get_headers_and_cookies(
                request, connection['url'], connection['key'], connection['config'], metadata, user=user
            )

            url = f'{connection["url"]}/images/generations'
            openai_size = form_data.size or connection_size
            # 512x512 is the stock IMAGE_SIZE, left over from DALL-E. GPT image models reject it,
            # so let them pick their own size unless the request asked for one.
            if (
                not form_data.size
                and openai_size == '512x512'
                and re.match(IMAGE_AUTO_SIZE_MODELS_REGEX_PATTERN, model)
            ):
                openai_size = 'auto'
            if connection_config.get('api_version'):
                url = f'{url}?api-version={connection_config["api_version"]}'

            provider_config.IMAGE_SIZE = openai_size
            if connection['type'] == 'grok':
                data = build_grok_payload(provider_config, form_data, model)
            else:
                data = build_openai_payload(
                    provider_config,
                    form_data,
                    model,
                    url_response=bool(re.match(IMAGE_URL_RESPONSE_MODELS_REGEX_PATTERN, model)),
                )

            session = await get_session()
            async with session.post(
                url=url,
                json=data,
                headers=headers,
                cookies=cookies,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                await raise_for_provider_status(r)
                res = await r.json(content_type=None)

            images = []

            for image in res['data']:
                if image_url := image.get('url', None):
                    image_data, content_type = await get_image_data(
                        image_url,
                        {k: v for k, v in headers.items() if k != 'Content-Type'}
                        if _is_same_origin(image_url, connection['url'])
                        else None,
                    )
                else:
                    image_data, content_type = await get_image_data(image['b64_json'])

                _, image_file = await upload_image(
                    request, image_data, content_type, redact_image_metadata({**data, **metadata}), user
                )
                images.append(image_file)
            return images

        elif connection['type'] == 'gemini':
            model = model or DEFAULT_GEMINI_IMAGE_MODEL
            headers = {
                'Content-Type': 'application/json',
                'x-goog-api-key': connection['key'],
            }

            model, data = build_gemini_request(provider_config, form_data, model)

            session = await get_session()
            async with session.post(
                url=f'{connection["url"]}/models/{model}',
                json=data,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                await raise_for_provider_status(r)
                res = await r.json(content_type=None)

            images = []

            if model.endswith(':predict'):
                for image in res['predictions']:
                    image_data, content_type = await get_image_data(image['bytesBase64Encoded'])
                    _, image_file = await upload_image(
                        request, image_data, content_type, redact_image_metadata({**data, **metadata}), user
                    )
                    images.append(image_file)
            elif model.endswith(':generateContent'):
                for image in res['candidates']:
                    for part in image['content']['parts']:
                        if part.get('inlineData', {}).get('data'):
                            image_data, content_type = await get_image_data(part['inlineData']['data'])
                            _, image_file = await upload_image(
                                request,
                                image_data,
                                content_type,
                                redact_image_metadata({**data, **metadata}),
                                user,
                            )
                            images.append(image_file)

            return images

        elif connection['type'] == 'comfyui':
            data = build_comfyui_payload(provider_config, form_data, width=width, height=height)

            form_data = ComfyUICreateImageForm(
                **{
                    'workflow': ComfyUIWorkflow(
                        **{
                            'workflow': connection_config.get('workflow') or '',
                            'nodes': connection_config.get('nodes') or [],
                        }
                    ),
                    **data,
                }
            )
            res = await comfyui_create_image(
                model,
                form_data,
                str(uuid.uuid4()),
                connection['url'],
                connection['key'],
            )

            images = []

            for image in res['data']:
                headers = None
                if connection['key'] and _is_same_origin(image['url'], connection['url']):
                    headers = {'Authorization': f'Bearer {connection["key"]}'}

                image_data, content_type = await get_image_data(
                    image['url'],
                    headers,
                    trusted_base_url=connection['url'],
                )
                _, image_file = await upload_image(
                    request,
                    image_data,
                    content_type,
                    redact_image_metadata(
                        {**form_data.model_dump(exclude={'workflow'}, exclude_none=True), **metadata}
                    ),
                    user,
                )
                images.append(image_file)
            return images
        elif connection['type'] == 'automatic1111':
            # The checkpoint is shared by everyone on this backend. Only models an admin listed on
            # the connection reach here, so switching to one is what the admin allowed.
            await set_automatic1111_model(connection, model)

            data = {
                'prompt': form_data.prompt,
                'batch_size': form_data.n,
                'width': width,
                'height': height,
            }

            if connection_steps is not None or form_data.steps is not None:
                data['steps'] = form_data.steps if form_data.steps is not None else connection_steps

            if form_data.negative_prompt is not None:
                data['negative_prompt'] = form_data.negative_prompt

            if connection_config.get('params'):
                data = {**data, **connection_config['params']}

            session = await get_session()
            async with session.post(
                url=f'{connection["url"]}/sdapi/v1/txt2img',
                json=data,
                headers={'authorization': get_automatic1111_api_auth(connection['key'])},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                res = await r.json(content_type=None)

            images = []

            for image in res['images']:
                image_data, content_type = await get_image_data(image)
                _, image_file = await upload_image(
                    request,
                    image_data,
                    content_type,
                    redact_image_metadata({**data, 'info': res['info'], **metadata}),
                    user,
                )
                images.append(image_file)
            return images
    except ComfyUIError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as e:
        error = image_provider_error(connection, e)
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(error))


async def load_url_image(data: str, user) -> str:
    """An image as a data URL, from a data URL, a web URL (SSRF-checked) or an Open WebUI file."""
    if data.startswith('data:'):
        return data

    if data.startswith('http://') or data.startswith('https://'):
        parsed = urlparse(data)
        # Fetching /api/v1/files/{id}/content over the network would be unauthenticated.
        if parsed.path.startswith('/api/v1/files/') and '/content' in parsed.path:
            return await load_url_image(parsed.path, user)

        # Validate URL to prevent SSRF attacks against local/private networks.
        # allow_redirects=False prevents redirect-based SSRF: validate_url() is
        # called only on the originally-submitted URL; following 3xx redirects
        # without re-validation would let an attacker reach private IPs via a
        # public host that redirects internally (e.g. cloud-metadata exfil).
        await asyncio.to_thread(validate_url, data)
        # SSRF-safe session: re-checks the connect-time IP so a rebinding DNS answer
        # that passed validate_url cannot reach an internal address.
        async with get_ssrf_safe_session() as session:
            async with session.get(
                data,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                allow_redirects=AIOHTTP_CLIENT_ALLOW_REDIRECTS,
                headers={'Accept-Encoding': 'identity'},
            ) as r:
                r.raise_for_status()
                assert_identity_encoding(r.headers.getall('Content-Encoding', ()))

                image_data = base64.b64encode(await r.read()).decode('utf-8')
                return f'data:{r.headers["content-type"]};base64,{image_data}'

    else:
        file_id = None
        if data.startswith('/api/v1/files'):
            file_id = data.split('/api/v1/files/')[1].split('/content')[0]
        else:
            file_id = data

        file_response = await get_file_content_by_id(file_id, user)
        if isinstance(file_response, FileResponse):
            file_path = file_response.path

            async with aiofiles.open(file_path, 'rb') as f:
                file_bytes = await f.read()
            image_data = base64.b64encode(file_bytes).decode('utf-8')
            mime_type, _ = mimetypes.guess_type(file_path)

            return f'data:{mime_type};base64,{image_data}'
    return data


class EditImageForm(BaseModel):
    engine_id: str | None = None
    image: str | list[str]  # base64-encoded image(s) or URL(s)
    prompt: str
    model: str | None = None
    size: str | None = None
    n: int | None = None
    negative_prompt: str | None = None
    background: str | None = None


@router.post('/edit')
async def edit_images(request: Request, form_data: EditImageForm, user=Depends(get_verified_user)):
    # Authorize the direct route like /generations and the edit_image tool: enforce the
    # global image-edit switch and the per-user image-generation permission. The internal
    # callers (edit_image tool, chat middleware) gate themselves and call image_edits()
    # directly, so they are unaffected by this wrapper.
    image_config = await get_image_config()
    if not image_config.ENABLE_IMAGE_EDIT:
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if user.role != 'admin' and not await has_permission(
        user.id, 'features.image_generation', image_config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=403,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = await image_edits(request, form_data, user=user, strict=True)
    await publish_event(
        request,
        EVENTS.IMAGE_EDITED,
        actor=user,
        subject_id=None,
        subject_type='image',
        data={
            'model': form_data.model,
            'size': form_data.size,
            'n': form_data.n,
            'prompt_preview': form_data.prompt[:300],
        },
    )
    return result


async def image_edits(
    request: Request,
    form_data: EditImageForm,
    metadata: dict | None = None,
    user=Depends(get_verified_user),
    strict: bool = False,
):
    connection, model = await resolve_image_model(
        request, user, 'image_edit', form_data.model, strict=strict, engine_id=form_data.engine_id
    )
    connection_config = connection.get('config') or {}
    connection_size = connection_config.get('edit_size') or ''
    provider_config = SimpleNamespace(
        IMAGE_EDIT_SIZE=connection_size,
        IMAGE_EDIT_PARAMS=connection_config.get('params'),
        IMAGE_EDIT_STEPS=connection_config.get('steps'),
    )
    size = None
    width, height = None, None
    metadata = metadata or {}

    size = form_data.size or connection_size or None
    if size and 'x' in size:
        width, height = tuple(map(int, size.split('x')))

    try:
        # Load image(s) from URL(s) if necessary
        if isinstance(form_data.image, str):
            form_data.image = await load_url_image(form_data.image, user)
        elif isinstance(form_data.image, list):
            # Load all images in parallel for better performance
            form_data.image = list(await asyncio.gather(*[load_url_image(img, user) for img in form_data.image]))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=ERROR_MESSAGES.DEFAULT(e, 'Error loading image'),
        )

    try:
        if connection['type'] == 'openai':
            model = model or DEFAULT_OPENAI_IMAGE_MODEL
            headers, cookies = await get_headers_and_cookies(
                request, connection['url'], connection['key'], connection['config'], metadata, user=user
            )
            # The edit is a multipart upload; aiohttp sets its own content type and boundary.
            headers.pop('Content-Type', None)

            data = {
                'model': model,
                'prompt': form_data.prompt,
                **({'n': form_data.n} if form_data.n else {}),
                **({'size': size} if size else {}),
                **({'background': form_data.background} if form_data.background else {}),
                **(
                    {}
                    if re.match(
                        IMAGE_URL_RESPONSE_MODELS_REGEX_PATTERN,
                        model,
                    )
                    else {'response_format': 'b64_json'}
                ),
            }

            extra = dict(connection_config.get('params') or {})
            for reserved in ('model', 'prompt', 'image', 'images', 'api_key', 'base_url', 'api_version'):
                extra.pop(reserved, None)
            data.update(extra)

            files = []
            if isinstance(form_data.image, str):
                image = form_data.image
                if ENABLE_OPENAI_IMAGE_EDIT_NORMALIZATION:
                    image = normalize_openai_edit_image_data_url(image)
                files = [get_image_file_item(image)]
            elif isinstance(form_data.image, list):
                for img in form_data.image:
                    if ENABLE_OPENAI_IMAGE_EDIT_NORMALIZATION:
                        img = normalize_openai_edit_image_data_url(img)
                    files.append(get_image_file_item(img, 'image[]'))

            url_search_params = ''
            if connection_config.get('api_version'):
                url_search_params += f'?api-version={connection_config["api_version"]}'

            # Build multipart form data for aiohttp
            form = aiohttp.FormData()
            for key, value in data.items():
                if isinstance(value, dict):
                    form.add_field(key, JSONCodec.dumps(value))
                else:
                    form.add_field(key, str(value))
            for param_name, (filename, file_obj, content_type_val) in files:
                form.add_field(
                    param_name,
                    file_obj,
                    filename=filename,
                    content_type=content_type_val,
                )

            session = await get_session()
            async with session.post(
                url=f'{connection["url"]}/images/edits{url_search_params}',
                headers=headers,
                cookies=cookies,
                data=form,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                await raise_for_provider_status(r)
                res = await r.json(content_type=None)

            images = []
            for image in res['data']:
                if image_url := image.get('url', None):
                    image_data, content_type = await get_image_data(
                        image_url,
                        {k: v for k, v in headers.items() if k != 'Content-Type'}
                        if _is_same_origin(image_url, connection['url'])
                        else None,
                    )
                else:
                    image_data, content_type = await get_image_data(image['b64_json'])

                _, image_file = await upload_image(
                    request, image_data, content_type, redact_image_metadata({**data, **metadata}), user
                )
                images.append(image_file)
            return images

        elif connection['type'] == 'gemini':
            headers = {
                'Content-Type': 'application/json',
                'x-goog-api-key': connection['key'],
            }

            model = f'{model}:generateContent'
            data = build_gemini_edit_payload(provider_config, form_data)

            session = await get_session()
            async with session.post(
                url=f'{connection["url"]}/models/{model}',
                json=data,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                await raise_for_provider_status(r)
                res = await r.json(content_type=None)

            images = []
            for image in res['candidates']:
                for part in image['content']['parts']:
                    if part.get('inlineData', {}).get('data'):
                        image_data, content_type = await get_image_data(part['inlineData']['data'])
                        _, image_file = await upload_image(
                            request,
                            image_data,
                            content_type,
                            redact_image_metadata({**data, **metadata}),
                            user,
                        )
                        images.append(image_file)

            return images

        elif connection['type'] == 'grok':
            headers, cookies = await get_headers_and_cookies(
                request, connection['url'], connection['key'], connection['config'], metadata, user=user
            )
            data = build_grok_edit_payload(provider_config, form_data, model or 'grok-imagine-image')

            session = await get_session()
            async with session.post(
                url=f'{connection["url"]}/images/edits',
                json=data,
                cookies=cookies,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                await raise_for_provider_status(r)
                res = await r.json(content_type=None)

            images = []
            for image in res['data']:
                if image_url := image.get('url'):
                    download_headers = None
                    if _is_same_origin(image_url, connection['url']):
                        download_headers = {key: value for key, value in headers.items() if key != 'Content-Type'}
                    image_data, content_type = await get_image_data(image_url, download_headers)
                else:
                    image_data, content_type = await get_image_data(image['b64_json'])
                _, image_file = await upload_image(
                    request, image_data, content_type, {**redact_image_metadata(data), **metadata}, user
                )
                images.append(image_file)
            return images

        elif connection['type'] == 'comfyui':
            try:
                files = []
                if isinstance(form_data.image, str):
                    files = [get_image_file_item(form_data.image)]
                elif isinstance(form_data.image, list):
                    for img in form_data.image:
                        files.append(get_image_file_item(img))

                # Upload images to ComfyUI and get their names
                comfyui_images = []
                for file_item in files:
                    res = await comfyui_upload_image(
                        file_item,
                        connection['url'],
                        connection['key'],
                    )
                    comfyui_images.append(res.get('name', file_item[1][0]))
            except ComfyUIError:
                raise
            except Exception:
                raise ComfyUIError('Failed to upload images to ComfyUI.') from None

            data = {
                **build_comfyui_edit_options(provider_config, form_data),
                'image': comfyui_images,
                'prompt': form_data.prompt,
                **({'width': width} if width is not None else {}),
                **({'height': height} if height is not None else {}),
                **({'n': form_data.n} if form_data.n else {}),
            }

            form_data = ComfyUIEditImageForm(
                **{
                    'workflow': ComfyUIWorkflow(
                        **{
                            'workflow': connection_config.get('edit_workflow') or '',
                            'nodes': connection_config.get('edit_nodes') or [],
                        }
                    ),
                    **data,
                }
            )
            res = await comfyui_edit_image(
                model,
                form_data,
                str(uuid.uuid4()),
                connection['url'],
                connection['key'],
            )

            image_urls = set()
            for image in res['data']:
                image_urls.add(image['url'])
            image_urls = list(image_urls)

            # Prioritize output type URLs if available
            output_type_urls = [url for url in image_urls if 'type=output' in url]
            if output_type_urls:
                image_urls = output_type_urls

            images = []

            for image_url in image_urls:
                headers = None
                if connection['key'] and _is_same_origin(image_url, connection['url']):
                    headers = {'Authorization': f'Bearer {connection["key"]}'}

                image_data, content_type = await get_image_data(
                    image_url,
                    headers,
                    trusted_base_url=connection['url'],
                )
                _, image_file = await upload_image(
                    request,
                    image_data,
                    content_type,
                    redact_image_metadata(
                        {**form_data.model_dump(exclude={'workflow'}, exclude_none=True), **metadata}
                    ),
                    user,
                )
                images.append(image_file)

            return images
    except ComfyUIError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as e:
        error = image_provider_error(connection, e)

        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(error))
