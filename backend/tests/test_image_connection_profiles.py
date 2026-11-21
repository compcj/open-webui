"""Named image shortcuts reference the live connection catalog, including migrated fork profiles."""

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from open_webui.utils.images.profiles import migrate_named_profiles, normalize_shortcuts

from test_image_generation_engines import BACKEND, image_router


def legacy_profile(**fields):
    return {
        'id': 'studio/portrait',
        'name': 'Studio',
        'engine': 'grok',
        'api_key': 'synthetic-generation',
        'size': '3:2',
        'tool_description_suffix': 'Use for portraits.',
        **fields,
    }


def test_migration_keeps_two_providers_but_only_model_references_in_shortcut():
    original = legacy_profile(
        edit={
            'engine': 'openai',
            'model': 'editor',
            'base_url': 'https://edit.example.test',
            'api_key': 'synthetic-editor',
            'tool_description_suffix': 'Keep the background.',
        }
    )
    connections, shortcuts = migrate_named_profiles([original])
    assert migrate_named_profiles([original]) == (connections, shortcuts)
    assert [c['type'] for c in connections] == ['grok', 'openai']
    assert connections[0]['config']['size'] == '3:2'
    assert connections[0]['config']['capabilities'] == {'image_generation': True, 'image_edit': False}
    assert connections[1]['config']['capabilities'] == {'image_generation': False, 'image_edit': True}
    assert shortcuts == [
        {
            'id': 'studio/portrait',
            'name': 'Studio',
            'generation_model': connections[0]['id'] + ':grok-imagine-image',
            'edit_model': connections[1]['id'] + ':editor',
            'tool_description_suffix': 'Use for portraits.',
            'edit_tool_description_suffix': 'Keep the background.',
        }
    ]
    assert 'synthetic' not in repr(shortcuts) and 'example.test' not in repr(shortcuts)


@pytest.mark.parametrize(('edit', 'expected'), [(None, None), ({'engine': 'disabled'}, 'disabled')])
def test_migration_distinguishes_disabled_edit_from_inheriting(edit, expected):
    connections, shortcuts = migrate_named_profiles([legacy_profile(edit=edit)])
    assert len(connections) == 1
    assert shortcuts[0]['edit_model'] == expected


def test_shortcut_id_stays_stable_when_display_name_changes_and_rejects_ambiguity():
    connections, shortcuts = migrate_named_profiles([legacy_profile()])
    renamed_connections, renamed = migrate_named_profiles([legacy_profile(name='Renamed')])
    assert shortcuts[0]['generation_model'] == renamed[0]['generation_model']
    assert connections[0]['id'] == renamed_connections[0]['id']
    with pytest.raises(ValueError):
        normalize_shortcuts([*shortcuts, *renamed])
    with pytest.raises((ValidationError, ValueError)):
        normalize_shortcuts([{**shortcuts[0], 'generation_model': 'unqualified'}])


@pytest.mark.asyncio
async def test_repeat_migration_and_removed_profiles_cannot_restore_stale_access(image_router):
    catalog, state = image_router.connections, image_router.state
    state.values['image_generation.engines'] = [legacy_profile(edit={'engine': 'grok', 'api_key': 'test-edit'})]
    connections = await catalog.migrate_legacy_image_config()
    reference = state.values['images.profiles'][0]
    assert connections == await catalog.migrate_legacy_image_config()
    assert len(state.upserts) == 1
    user = SimpleNamespace(id='user-1', role='user')
    request = SimpleNamespace()
    for capability, field in [('image_generation', 'generation_model'), ('image_edit', 'edit_model')]:
        selected, model = await catalog.resolve_image_model(request, user, capability, engine_id=reference['id'])
        assert reference[field] == f'{selected["id"]}:{model}'
    assert await catalog.get_image_tool_settings(request, user, reference['id']) == ('Use for portraits.', '', True)
    connections[-1]['config']['access_grants'] = []
    assert await catalog.get_image_tool_settings(request, user, reference['id']) == ('Use for portraits.', '', False)
    with pytest.raises(HTTPException) as denied:
        await catalog.resolve_image_model(request, user, 'image_edit', engine_id=reference['id'])
    assert denied.value.status_code == 403
    connections[-2]['config']['access_grants'] = []
    assert await image_router.module.get_image_generation_engines(request, user) == []
    with pytest.raises(HTTPException) as hidden:
        await catalog.resolve_image_model(request, user, 'image_generation', engine_id=reference['id'])
    assert hidden.value.status_code == 403
    state.values['images.profiles'] = []
    state.values['images.connections'] = []
    assert await catalog.migrate_legacy_image_config() == []
    assert state.values['images.profiles'] == []


@pytest.mark.asyncio
async def test_direct_routes_reject_wrong_or_unavailable_models_before_loading_or_transport(image_router):
    router, state = image_router.module, image_router.state
    state.values['images.edit.enable'] = True
    state.values['image_generation.engines'] = [legacy_profile(edit={'engine': 'grok'})]
    request = SimpleNamespace()
    user = SimpleNamespace(id='member', role='user')
    forms = [
        (router.generate_images, router.CreateImageForm(prompt='generate', engine_id='studio/portrait', model='wrong')),
        (router.edit_images, router.EditImageForm(image='unread', prompt='edit', model='unknown')),
    ]
    for route, form in forms:
        with pytest.raises(HTTPException) as error:
            await route(request, form, user)
        assert error.value.status_code == 400
    assert state.posts == []


@pytest.mark.asyncio
async def test_concurrent_sqlite_migration_commits_both_catalogs_once(image_router, tmp_path, monkeypatch):
    # Load the repository's Config ORM against an isolated database, as separate startup workers would.
    db_module = sys.modules['open_webui.internal.db']
    base = declarative_base()
    db_engine = create_async_engine(f'sqlite+aiosqlite:///{(tmp_path / "image-config.sqlite").as_posix()}')
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_db(existing=None):
        if existing is not None:
            yield existing
        else:
            async with sessions() as session:
                yield session

    for name, value in [('Base', base), ('get_async_db', get_db), ('get_async_db_context', get_db)]:
        monkeypatch.setattr(db_module, name, value, raising=False)
    spec = importlib.util.spec_from_file_location('image_config_orm', BACKEND / 'models/config.py')
    orm = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, orm)
    spec.loader.exec_module(orm)
    monkeypatch.setattr(image_router.connections, 'Config', orm.Config)
    orm.Config.configure(defaults=image_router.state.values)
    try:
        async with db_engine.begin() as db:
            await db.run_sync(base.metadata.create_all)
        await orm.Config.upsert({'image_generation.engines': [legacy_profile(edit={'engine': 'grok'})]})
        catalogs = await asyncio.gather(*(image_router.connections.migrate_legacy_image_config() for _ in range(3)))
        assert all(catalog == catalogs[0] for catalog in catalogs)
        assert len(catalogs[0]) == 3
        saved = await orm.Config.get_many('images.connections', 'images.profiles', 'image_generation.default_model')
        assert saved['images.connections'] == catalogs[0]
        generation, edit = catalogs[0][-2:]
        assert saved['images.profiles'][0]['generation_model'] == generation['id'] + ':grok-imagine-image'
        assert saved['images.profiles'][0]['edit_model'] == edit['id'] + ':grok-imagine-image'
        assert saved['image_generation.default_model'] == catalogs[0][0]['id'] + ':default-model'
        await orm.Config.upsert({'images.connections': [], 'images.profiles': []})
        assert await image_router.connections.migrate_legacy_image_config() == []
    finally:
        await db_engine.dispose()
