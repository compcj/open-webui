"""Stable, credential-free shortcuts to generation and edit models in the image connection catalog."""

from __future__ import annotations

import uuid
from typing import Any

from open_webui.utils.images.engines import ImageGenerationEngineProfile, normalize_engine_profiles
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class ImageShortcut(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')

    id: str
    name: str
    generation_model: str
    edit_model: str | None = None
    tool_description_suffix: str = ''
    edit_tool_description_suffix: str = ''

    @field_validator('id', 'name')
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value:
            raise ValueError('A shortcut ID and name are required')
        return value

    @field_validator('id')
    @classmethod
    def reserved_id(cls, value: str) -> str:
        if value.casefold() == 'default':
            raise ValueError('The default image shortcut ID is reserved')
        return value

    @field_validator('generation_model', 'edit_model')
    @classmethod
    def model_reference(cls, value: str | None, info):
        if info.field_name == 'edit_model' and value in (None, '', 'disabled'):
            return value or None
        if not value or ':' not in value or not value.split(':', 1)[0]:
            raise ValueError('Choose a connection model')
        return value


def normalize_shortcuts(values: list[dict]) -> list[dict]:
    shortcuts = [ImageShortcut.model_validate(item) for item in values]
    for key in ('id', 'name'):
        if len({getattr(item, key).casefold() for item in shortcuts}) != len(shortcuts):
            raise ValueError(f'Duplicate image shortcut {key}')
    return [item.model_dump() for item in shortcuts]


def legacy_connection_id(profile_id: str, capability: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'open-webui/image-profile/{capability}/{profile_id}'))


def migrate_named_profiles(values: list[dict]) -> tuple[list[dict], list[dict]]:
    connections, shortcuts = [], []
    for profile in normalize_engine_profiles(values):
        shortcut = {
            'id': profile.id,
            'name': profile.name,
            'tool_description_suffix': profile.tool_description_suffix,
            'edit_tool_description_suffix': profile.edit.tool_description_suffix if profile.edit else '',
            'edit_model': None,
        }
        for capability, settings in (('image_generation', profile), ('image_edit', profile.edit)):
            if settings is None:
                continue
            editing = capability == 'image_edit'
            if settings.engine == 'disabled':
                shortcut['edit_model'] = 'disabled'
                continue
            connection_id = legacy_connection_id(profile.id, capability)
            model = settings.model
            config = {
                'capabilities': {'image_generation': not editing, 'image_edit': editing},
                'model_ids': [model] if model else [],
                'access_grants': [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}],
                'edit_size' if editing else 'size': settings.size,
                'steps': settings.steps,
                'params': settings.params,
                'api_version': settings.api_version,
                'endpoint_method': settings.gemini_endpoint_method,
                'edit_workflow' if editing else 'workflow': settings.comfyui_workflow,
                'edit_nodes' if editing else 'nodes': [node.model_dump() for node in settings.comfyui_workflow_nodes],
            }
            connections.append(
                {
                    'id': connection_id,
                    'name': f'{profile.name} (edit)' if editing else profile.name,
                    'type': settings.engine,
                    'url': settings.base_url,
                    'key': settings.api_key,
                    'config': config,
                }
            )
            shortcut['edit_model' if editing else 'generation_model'] = f'{connection_id}:{model}'
        shortcuts.append(shortcut)
    return connections, shortcuts


def legacy_profile_projection(shortcuts: list[dict], connections: list[dict]) -> list[dict]:
    """Project migrated profiles for the old admin client; keep shared new connections managed in the new UI."""
    by_id = {connection['id']: connection for connection in connections}
    profiles = []
    for shortcut in shortcuts:
        fields: dict[str, Any] = {'id': shortcut['id'], 'name': shortcut['name']}
        for capability, key in (('image_generation', 'generation_model'), ('image_edit', 'edit_model')):
            editing = capability == 'image_edit'
            ref = shortcut.get(key)
            if editing and ref in (None, 'disabled'):
                fields['edit'] = {'engine': 'disabled'} if ref else None
                continue
            connection_id, _, model = ref.partition(':')
            connection = by_id.get(connection_id)
            if not connection or connection_id != legacy_connection_id(shortcut['id'], capability):
                break
            config = connection['config']
            settings = {
                'engine': connection['type'],
                'base_url': connection['url'],
                'api_key': connection['key'],
                'model': model,
                'params': config.get('params') or {},
                'api_version': config.get('api_version') or '',
                'size': config.get('edit_size' if editing else 'size') or '',
                'steps': config.get('steps'),
                'gemini_endpoint_method': config.get('endpoint_method') or 'generateContent',
                'comfyui_workflow': config.get('edit_workflow' if editing else 'workflow') or '',
                'comfyui_workflow_nodes': config.get('edit_nodes' if editing else 'nodes') or [],
                'tool_description_suffix': shortcut.get(
                    'edit_tool_description_suffix' if editing else 'tool_description_suffix', ''
                ),
            }
            if editing:
                fields['edit'] = settings
            else:
                fields.update(settings)
        else:
            try:
                profiles.append(ImageGenerationEngineProfile.model_validate(fields).model_dump())
            except ValidationError:
                # An admin may have changed the provider or target to a connection the old client cannot represent.
                continue
    return profiles
