from __future__ import annotations

import copy
import json
import math
import re
from types import SimpleNamespace
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class ComfyUIWorkflowNode(BaseModel):
    type: str = ''
    key: str = ''
    node_ids: list[str] = Field(default_factory=list)

    @field_validator('type', 'key', mode='before')
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        return '' if value is None else str(value).strip()


class ImageEngineConfig(BaseModel):
    engine: Literal['openai', 'gemini', 'grok', 'comfyui']
    model: str = ''
    base_url: str = ''
    api_key: str = ''
    api_version: str = ''
    params: dict[str, Any] = Field(default_factory=dict)
    size: str = ''
    steps: int | None = Field(default=None, ge=0)
    gemini_endpoint_method: Literal['predict', 'generateContent'] = 'generateContent'
    comfyui_workflow: str = ''
    comfyui_workflow_nodes: list[ComfyUIWorkflowNode] = Field(default_factory=list)

    @field_validator('model', 'api_key', 'api_version', 'size', 'comfyui_workflow', mode='before')
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str:
        return '' if value is None else str(value).strip()

    @field_validator('base_url', mode='before')
    @classmethod
    def normalize_base_url(cls, value: Any) -> str:
        return ('' if value is None else str(value).strip()).rstrip('/')

    @model_validator(mode='after')
    def validate_provider_config(self):
        if self.engine == 'comfyui' and self.comfyui_workflow:
            try:
                workflow = json.loads(self.comfyui_workflow)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError('comfyui_workflow must be valid JSON') from exc
            if not isinstance(workflow, dict):
                raise ValueError('comfyui_workflow must contain a JSON object')

        if self.engine == 'grok':
            if not self.model:
                self.model = 'grok-imagine-image'
            if not self.base_url:
                self.base_url = 'https://api.x.ai/v1'

        if self.engine in {'openai', 'gemini'} and not self.model:
            raise ValueError(f'{self.engine} profiles require a model')
        if not self.base_url:
            raise ValueError(f'{self.engine} profiles require a base_url')
        parsed_base_url = urlparse(self.base_url)
        if parsed_base_url.scheme not in {'http', 'https'} or not parsed_base_url.netloc:
            raise ValueError('base_url must be an absolute HTTP(S) URL')

        if self.size:
            is_positive_dimensions = bool(re.fullmatch(r'[1-9]\d*x[1-9]\d*', self.size))
            is_positive_ratio = bool(re.fullmatch(r'[1-9]\d*:[1-9]\d*', self.size))
            if self.size == 'auto' and self.engine == 'openai':
                pass
            elif is_positive_dimensions:
                pass
            elif is_positive_ratio and self.engine == 'grok':
                pass
            else:
                raise ValueError(f'invalid size for {self.engine} profile')

        return self


class ImageGenerationEngineProfile(ImageEngineConfig):
    id: str
    name: str
    edit: ImageEngineConfig | None = None

    @field_validator('id', 'name', mode='before')
    @classmethod
    def normalize_required_text(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('must be a non-empty string')
        return value.strip()

    @model_validator(mode='after')
    def validate_identity(self):
        if self.id.casefold() == 'default':
            raise ValueError('engine id "default" is reserved')
        return self


def normalize_engine_profiles(profiles: Any) -> list[ImageGenerationEngineProfile]:
    if profiles is None:
        return []
    if not isinstance(profiles, list):
        raise ValueError('image generation engines must be a list')

    normalized = [ImageGenerationEngineProfile.model_validate(profile) for profile in profiles]
    ids: set[str] = set()
    names: set[str] = set()
    for profile in normalized:
        folded_id = profile.id.casefold()
        folded_name = profile.name.casefold()
        if folded_id in ids:
            raise ValueError(f'duplicate image generation engine id: {profile.id}')
        if folded_name in names:
            raise ValueError(f'duplicate image generation engine name: {profile.name}')
        ids.add(folded_id)
        names.add(folded_name)
    return normalized


def describe_profile_validation_error(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        descriptions = []
        for item in error.errors(include_url=False, include_context=False, include_input=False):
            field = '.'.join(str(part) for part in item['loc']) or 'profile'
            descriptions.append(f'{field}: {item["msg"]}')
        return '; '.join(descriptions)
    return str(error)


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def redact_image_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized_key = ''.join(character for character in str(key).casefold() if character.isalnum())
            if normalized_key in {
                'apikey',
                'authorization',
                'accesstoken',
                'refreshtoken',
                'token',
                'secret',
                'password',
            } or normalized_key.endswith(('apikey', 'accesstoken', 'refreshtoken', 'secret', 'password')):
                continue
            redacted[key] = redact_image_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_image_metadata(item) for item in value]
    return copy.deepcopy(value)


def resolve_image_generation_config(
    default_config: SimpleNamespace,
    profiles: list[ImageGenerationEngineProfile] | list[dict[str, Any]] | None,
    engine_id: str | None,
) -> SimpleNamespace:
    snapshot = SimpleNamespace(**copy.deepcopy(vars(default_config)))
    if not engine_id:
        return snapshot

    normalized = normalize_engine_profiles(profiles)
    selected = next((profile for profile in normalized if profile.id == engine_id), None)
    if selected is None:
        return snapshot

    snapshot.IMAGE_GENERATION_PARAMS = {}
    snapshot.IMAGES_OPENAI_API_BASE_URL = ''
    snapshot.IMAGES_OPENAI_API_KEY = ''
    snapshot.IMAGES_OPENAI_API_VERSION = ''
    snapshot.IMAGES_OPENAI_API_PARAMS = {}
    snapshot.IMAGES_GEMINI_API_BASE_URL = ''
    snapshot.IMAGES_GEMINI_API_KEY = ''
    snapshot.IMAGES_GEMINI_ENDPOINT_METHOD = 'generateContent'
    snapshot.COMFYUI_BASE_URL = ''
    snapshot.COMFYUI_API_KEY = ''
    snapshot.COMFYUI_WORKFLOW = ''
    snapshot.COMFYUI_WORKFLOW_NODES = []

    snapshot.IMAGE_GENERATION_ENGINE = selected.engine
    snapshot.IMAGE_GENERATION_MODEL = selected.model
    snapshot.IMAGE_SIZE = selected.size
    snapshot.IMAGE_STEPS = selected.steps
    snapshot.IMAGE_GENERATION_PARAMS = copy.deepcopy(selected.params)

    if selected.engine in {'openai', 'grok'}:
        snapshot.IMAGES_OPENAI_API_BASE_URL = selected.base_url
        snapshot.IMAGES_OPENAI_API_KEY = selected.api_key
        snapshot.IMAGES_OPENAI_API_VERSION = selected.api_version
        snapshot.IMAGES_OPENAI_API_PARAMS = copy.deepcopy(selected.params)
    elif selected.engine == 'gemini':
        snapshot.IMAGES_GEMINI_API_BASE_URL = selected.base_url
        snapshot.IMAGES_GEMINI_API_KEY = selected.api_key
        snapshot.IMAGES_GEMINI_ENDPOINT_METHOD = selected.gemini_endpoint_method
    elif selected.engine == 'comfyui':
        snapshot.COMFYUI_BASE_URL = selected.base_url
        snapshot.COMFYUI_API_KEY = selected.api_key
        snapshot.COMFYUI_WORKFLOW = selected.comfyui_workflow
        snapshot.COMFYUI_WORKFLOW_NODES = [node.model_dump() for node in selected.comfyui_workflow_nodes]

    return snapshot


def resolve_image_edit_config(
    default_config: SimpleNamespace,
    profiles: list[ImageGenerationEngineProfile] | list[dict[str, Any]] | None,
    engine_id: str | None,
) -> SimpleNamespace:
    snapshot = SimpleNamespace(**copy.deepcopy(vars(default_config)))
    if not engine_id:
        return snapshot

    selected = next((profile for profile in normalize_engine_profiles(profiles) if profile.id == engine_id), None)
    if selected is None or selected.edit is None:
        return snapshot

    edit = selected.edit
    snapshot.IMAGE_EDIT_PARAMS = copy.deepcopy(edit.params)
    snapshot.IMAGES_EDIT_OPENAI_API_BASE_URL = ''
    snapshot.IMAGES_EDIT_OPENAI_API_KEY = ''
    snapshot.IMAGES_EDIT_OPENAI_API_VERSION = ''
    snapshot.IMAGES_EDIT_GEMINI_API_BASE_URL = ''
    snapshot.IMAGES_EDIT_GEMINI_API_KEY = ''
    snapshot.IMAGES_EDIT_COMFYUI_BASE_URL = ''
    snapshot.IMAGES_EDIT_COMFYUI_API_KEY = ''
    snapshot.IMAGES_EDIT_COMFYUI_WORKFLOW = ''
    snapshot.IMAGES_EDIT_COMFYUI_WORKFLOW_NODES = []

    snapshot.IMAGE_EDIT_ENGINE = edit.engine
    snapshot.IMAGE_EDIT_MODEL = edit.model
    snapshot.IMAGE_EDIT_SIZE = edit.size
    snapshot.IMAGE_EDIT_STEPS = edit.steps
    if edit.engine in {'openai', 'grok'}:
        snapshot.IMAGES_EDIT_OPENAI_API_BASE_URL = edit.base_url
        snapshot.IMAGES_EDIT_OPENAI_API_KEY = edit.api_key
        snapshot.IMAGES_EDIT_OPENAI_API_VERSION = edit.api_version
    elif edit.engine == 'gemini':
        snapshot.IMAGES_EDIT_GEMINI_API_BASE_URL = edit.base_url
        snapshot.IMAGES_EDIT_GEMINI_API_KEY = edit.api_key
    elif edit.engine == 'comfyui':
        snapshot.IMAGES_EDIT_COMFYUI_BASE_URL = edit.base_url
        snapshot.IMAGES_EDIT_COMFYUI_API_KEY = edit.api_key
        snapshot.IMAGES_EDIT_COMFYUI_WORKFLOW = edit.comfyui_workflow
        snapshot.IMAGES_EDIT_COMFYUI_WORKFLOW_NODES = [node.model_dump() for node in edit.comfyui_workflow_nodes]
    return snapshot


def _profile_params(config: SimpleNamespace, provider_field: str | None = None) -> dict[str, Any]:
    params = getattr(config, 'IMAGE_GENERATION_PARAMS', None)
    if params is None and provider_field:
        params = getattr(config, provider_field, None)
    return copy.deepcopy(params) if isinstance(params, dict) else {}


def build_openai_payload(
    config: SimpleNamespace,
    form_data: Any,
    model: str,
    *,
    url_response: bool,
) -> dict[str, Any]:
    size = form_data.size or config.IMAGE_SIZE
    payload: dict[str, Any] = {'model': model, 'prompt': form_data.prompt, 'n': form_data.n}
    if size:
        payload['size'] = size
    if not url_response:
        payload['response_format'] = 'b64_json'
    payload = deep_merge(payload, _profile_params(config, 'IMAGES_OPENAI_API_PARAMS'))
    if hasattr(config, 'IMAGE_GENERATION_PARAMS'):
        payload['model'] = model
        payload['prompt'] = form_data.prompt
    return payload


def _aspect_ratio(size: str | None) -> str | None:
    if not size:
        return None
    if ':' in size:
        return size
    if 'x' not in size:
        return None
    try:
        width, height = (int(part) for part in size.lower().split('x', 1))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    divisor = math.gcd(width, height)
    return f'{width // divisor}:{height // divisor}'


def build_grok_payload(config: SimpleNamespace, form_data: Any, model: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'n': form_data.n,
        'resolution': '1k',
        'response_format': 'b64_json',
    }
    if aspect_ratio := _aspect_ratio(form_data.size or config.IMAGE_SIZE):
        payload['aspect_ratio'] = aspect_ratio
    payload = deep_merge(payload, _profile_params(config, 'IMAGES_OPENAI_API_PARAMS'))
    for reserved in ('size', 'base_url', 'api_key', 'api_version'):
        payload.pop(reserved, None)
    payload['model'] = model
    payload['prompt'] = form_data.prompt
    return payload


def build_gemini_request(config: SimpleNamespace, form_data: Any, model: str) -> tuple[str, dict[str, Any]]:
    method = config.IMAGES_GEMINI_ENDPOINT_METHOD or 'predict'
    if method == 'predict':
        payload = {
            'instances': {'prompt': form_data.prompt},
            'parameters': {
                'sampleCount': form_data.n,
                'outputOptions': {'mimeType': 'image/png'},
            },
        }
        payload = deep_merge(payload, _profile_params(config))
        payload['instances'] = {'prompt': form_data.prompt}
    else:
        payload = {
            'contents': [{'parts': [{'text': form_data.prompt}]}],
        }
        payload = deep_merge(payload, _profile_params(config))
        payload['contents'] = [{'parts': [{'text': form_data.prompt}]}]
    return f'{model}:{method}', payload


def build_comfyui_payload(
    config: SimpleNamespace,
    form_data: Any,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    allowed_options = {'negative_prompt', 'steps', 'seed'}
    payload = {
        key: value for key, value in _profile_params(config).items() if key in allowed_options and value is not None
    }
    payload.update(
        {
            'prompt': form_data.prompt,
            'width': width,
            'height': height,
            'n': form_data.n,
        }
    )

    steps = form_data.steps if form_data.steps is not None else config.IMAGE_STEPS
    if steps is not None:
        payload['steps'] = steps
    if form_data.negative_prompt is not None:
        payload['negative_prompt'] = form_data.negative_prompt
    return payload


def build_gemini_edit_payload(config: SimpleNamespace, form_data: Any) -> dict[str, Any]:
    parts = [{'text': form_data.prompt}]
    sources = [form_data.image] if isinstance(form_data.image, str) else form_data.image
    for source in sources:
        header, encoded = source.split(',', 1)
        mime_type = 'image/png'
        if hasattr(config, 'IMAGE_EDIT_PARAMS'):
            mime_type = header.split(';', 1)[0].removeprefix('data:') or 'image/png'
        parts.append({'inline_data': {'mime_type': mime_type, 'data': encoded}})
    payload = {'contents': [{'parts': parts}]}
    params = getattr(config, 'IMAGE_EDIT_PARAMS', None)
    if isinstance(params, dict):
        payload = deep_merge(payload, params)
        payload['contents'] = [{'parts': parts}]
    for reserved in ('model', 'api_key', 'base_url', 'api_version', 'Authorization'):
        payload.pop(reserved, None)
    return payload


def build_comfyui_edit_options(config: SimpleNamespace, form_data: Any) -> dict[str, Any]:
    params = getattr(config, 'IMAGE_EDIT_PARAMS', None)
    options = {
        key: copy.deepcopy(value)
        for key, value in (params.items() if isinstance(params, dict) else [])
        if key in {'seed', 'steps'} and value is not None
    }
    if getattr(config, 'IMAGE_EDIT_STEPS', None) is not None:
        options['steps'] = config.IMAGE_EDIT_STEPS
    return options


def build_grok_edit_payload(config: SimpleNamespace, form_data: Any, model: str) -> dict[str, Any]:
    sources = [form_data.image] if isinstance(form_data.image, str) else form_data.image
    if len(sources) > 5:
        raise ValueError('Grok image editing accepts up to five source images')
    payload: dict[str, Any] = {'model': model, 'prompt': form_data.prompt}
    if form_data.n:
        payload['n'] = form_data.n
    if aspect_ratio := _aspect_ratio(form_data.size or config.IMAGE_EDIT_SIZE):
        payload['aspect_ratio'] = aspect_ratio
    params = getattr(config, 'IMAGE_EDIT_PARAMS', None)
    if isinstance(params, dict):
        payload = deep_merge(payload, params)
    for reserved in ('size', 'base_url', 'api_key', 'api_version', 'Authorization', 'image', 'images'):
        payload.pop(reserved, None)
    payload['model'] = model
    payload['prompt'] = form_data.prompt
    images = [{'url': source, 'type': 'image_url'} for source in sources]
    if len(sources) == 1:
        payload['image'] = images[0]
    else:
        payload['images'] = images
    return payload
