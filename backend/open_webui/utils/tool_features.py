"""Request feature policy shared by builtin tools and automatic memory context."""

import json
from functools import wraps

from open_webui.models.config import Config
from open_webui.utils.access_control import has_permission


async def memory_enabled(features: dict, model: dict, user) -> bool:
    if not features.get('memory', False):
        return False
    meta = (model or {}).get('info', {}).get('meta', {})
    capabilities = meta.get('capabilities') or {}
    if not (
        capabilities.get('builtin_tools', True)
        and capabilities.get('memory', True)
        and (meta.get('builtinTools') or {}).get('memory', True)
    ):
        return False

    config = await Config.get_many('memories.enable', 'ui.default_interface_settings')
    if not config.get('memories.enable', False):
        return False
    user = user if isinstance(user, dict) else user.model_dump()
    settings = user.get('settings') or {}
    defaults = config.get('ui.default_interface_settings') or {}
    master_enabled = (settings.get('ui') or {}).get('memory')
    if master_enabled is None:
        master_enabled = defaults.get('memory')
    if master_enabled is None:
        master_enabled = config['memories.enable']
    if not master_enabled:
        return False
    return user.get('role') == 'admin' or await has_permission(
        user.get('id', ''), 'features.memories', await Config.get('user.permissions')
    )


def scope_attachment_tool(name, function, attachments, chat_files, user):
    """Bound native readers to explicit attachments; underlying readers still enforce ACLs."""
    note_ids = {item['id'] for item in attachments if item.get('type') == 'note' and item.get('id')}
    file_ids = {item['id'] for item in attachments if item.get('type') == 'file' and item.get('id')}
    file_ids.update(
        item.get('id') or item.get('url')
        for item in chat_files
        if isinstance(item, dict) and item.get('type', 'file') == 'file' and (item.get('id') or item.get('url'))
    )
    collection_ids = {item['id'] for item in attachments if item.get('type') == 'collection' and item.get('id')}

    @wraps(function)
    async def scoped(**kwargs):
        denied = json.dumps({'error': 'Resource is not attached to this conversation'})
        if name == 'view_note' and kwargs.get('note_id') not in note_ids:
            return denied
        if name in ('view_file', 'view_knowledge_file') and kwargs.get('file_id') not in file_ids:
            if not collection_ids:
                return denied
            from open_webui.models.access_grants import AccessGrants
            from open_webui.models.knowledge import Knowledges

            bases = await Knowledges.get_knowledges_by_file_id(kwargs.get('file_id'))
            allowed = False
            for base in bases:
                if base.id in collection_ids and (
                    user.get('role') == 'admin'
                    or base.user_id == user.get('id')
                    or await AccessGrants.has_access(
                        user_id=user.get('id'), resource_type='knowledge', resource_id=base.id, permission='read'
                    )
                ):
                    allowed = True
                    break
            if not allowed:
                return denied
        if name in ('list_knowledge', 'query_knowledge_files'):
            if not attachments:
                return denied
            ids = kwargs.get('knowledge_ids')
            if ids is not None and (not isinstance(ids, list) or any(item not in collection_ids for item in ids)):
                return denied
            if kwargs.get('knowledge_id') and kwargs['knowledge_id'] not in collection_ids:
                return denied
        return await function(**kwargs)

    return scoped
