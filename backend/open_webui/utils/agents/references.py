"""Upgrade stored Agent references when an authorized terminal supplies a live, unambiguous catalog."""

from urllib.parse import quote

from sqlalchemy import Text, cast, select, text

from open_webui.internal.db import get_async_db
from open_webui.models.automations import Automation
from open_webui.models.config import Config
from open_webui.models.groups import Group
from open_webui.models.users import User

from .discovery import resolve_catalog_model


def migrate_ids(ids, catalog, terminal_id):
    """Keep ordering and unavailable IDs. A missing catalog cannot establish a replacement."""
    prefix = f'agent:{quote(terminal_id, safe="")}:'
    updated, unresolved = [], []
    for value in ids:
        model = resolve_catalog_model(catalog, value) if isinstance(value, str) and value.startswith(prefix) else None
        if model:
            updated.append(model['id'])
        else:
            updated.append(value)
            if isinstance(value, str) and value.startswith(prefix):
                unresolved.append(value)
    return updated, unresolved


async def migrate_model_references(user, terminal_id, catalog, *, cwd=None):
    """Migrate this user's UI selections and matching automations under database locks."""
    if not terminal_id or not catalog:
        return
    async with get_async_db() as db:
        if db.bind.dialect.name == 'sqlite':
            await db.execute(text('BEGIN IMMEDIATE'))
        row = await db.get(User, user.id, with_for_update=True)
        if row is None:
            return
        ui = dict((row.settings or {}).get('ui') or {})
        original = dict(ui)
        prefix = f'agent:{quote(terminal_id, safe="")}:'
        rebind = [
            value
            for value in ui.get('agentModelsNeedingRebind', [])
            if isinstance(value, str) and not value.startswith(prefix)
        ]
        for field in ('models', 'pinnedModels'):
            if isinstance(ui.get(field), list):
                ui[field], missing = migrate_ids(ui[field], catalog, terminal_id)
                rebind.extend(missing)
        if rebind:
            ui['agentModelsNeedingRebind'] = list(dict.fromkeys(rebind))
        else:
            ui.pop('agentModelsNeedingRebind', None)
        if ui != original:
            row.settings = {**(row.settings or {}), 'ui': ui}

        # A catalog can differ by working directory. A different automation must establish its own match.
        automations = (
            await db.scalars(
                select(Automation)
                .where(
                    Automation.user_id == user.id,
                    Automation.data['terminal']['server_id'].as_string() == terminal_id,
                )
                .order_by(Automation.id)
                .with_for_update()
            )
        ).all()
        for automation in automations:
            data = automation.data
            if (data.get('terminal') or {}).get('cwd') != cwd:
                continue
            ref = data.get('model_id')
            if not isinstance(ref, str) or not ref.startswith(prefix):
                continue
            ids, missing = migrate_ids([ref], catalog, terminal_id)
            meta = dict(automation.meta or {})
            if missing:
                meta['agent_model_needs_rebind'] = True
            else:
                meta.pop('agent_model_needs_rebind', None)
                if ids[0] != ref:
                    automation.data = {**data, 'model_id': ids[0]}
            if meta != (automation.meta or {}):
                automation.meta = meta

        if row.role == 'admin':
            for key in ('ui.default_models', 'ui.default_pinned_models'):
                config = await db.get(Config, key, with_for_update=True)
                if config and isinstance(config.value, str) and Config.persistent_enabled_for(key):
                    ids = [value.strip() for value in config.value.split(',')]
                    updated, _ = migrate_ids(ids, catalog, terminal_id)
                    if updated != ids:
                        config.value = ','.join(updated)
            groups = await db.scalars(
                select(Group)
                .where(cast(Group.data, Text).contains(prefix, autoescape=True))
                .order_by(Group.id)
                .with_for_update()
            )
            for group in groups:
                config = (group.data or {}).get('config') or {}
                values = config.get('default_models')
                if isinstance(values, list):
                    updated, _ = migrate_ids(values, catalog, terminal_id)
                    if updated != values:
                        group.data = {**group.data, 'config': {**config, 'default_models': updated}}
        await db.commit()


async def migrate_automation_reference(user_id, automation_id, terminal_id, cwd, old_id, new_id):
    """A scheduled run can discover a per-chat template model for the first time in its new chat."""
    if not automation_id or not old_id or old_id == new_id:
        return
    async with get_async_db() as db:
        if db.bind.dialect.name == 'sqlite':
            await db.execute(text('BEGIN IMMEDIATE'))
        row = await db.get(Automation, automation_id, with_for_update=True)
        if not row or row.user_id != user_id or row.data.get('model_id') != old_id:
            return
        terminal = row.data.get('terminal') or {}
        if terminal.get('server_id') != terminal_id or terminal.get('cwd') and terminal['cwd'] != cwd:
            return
        meta = dict(row.meta or {})
        if new_id is None:
            meta['agent_model_needs_rebind'] = True
        else:
            row.data = {**row.data, 'model_id': new_id}
            meta.pop('agent_model_needs_rebind', None)
        if meta != (row.meta or {}):
            row.meta = meta
        await db.commit()
