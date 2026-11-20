"""Independent Tether authorization and old-client configuration compatibility."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, field_validator
from multidict import CIMultiDict

from test_auth_revocation import load_functions

ROOT = Path(__file__).parents[1] / 'open_webui'


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('websocket', [False, True])
async def test_tether_gate_is_independent_of_personal_integrations(direct, enabled, websocket):
    config = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda key, default=None: {
                'tether.enable': enabled,
                'direct.integrations.enable': direct,
            }.get(key, default)
        )
    )
    ns = {'Config': config, 'HTTPException': HTTPException, 'ENABLE_WEBSOCKET_SUPPORT': websocket}
    load_functions('utils/tether.py', {'require_tether_enabled'}, ns)
    if enabled and websocket:
        await ns['require_tether_enabled']()
    else:
        with pytest.raises(HTTPException) as caught:
            await ns['require_tether_enabled']()
        assert caught.value.status_code == 403
    assert all(call.args[0] == 'tether.enable' for call in config.get.await_args_list)


@pytest.mark.asyncio
async def test_disabled_tether_discovery_does_not_contact_relay():
    configured = [{'id': 'admin-server'}]
    config = SimpleNamespace(
        get=AsyncMock(
            side_effect=lambda key, default=None: {
                'tether.enable': False,
                'terminal_server.connections': configured,
            }.get(key, default)
        )
    )
    hub = SimpleNamespace(presences=AsyncMock(side_effect=AssertionError('must not discover')))
    ns = {'Config': config, 'hub': hub, 'ENABLE_WEBSOCKET_SUPPORT': True}
    load_functions('utils/tether.py', {'get_terminal_connections'}, ns)
    assert await ns['get_terminal_connections'](SimpleNamespace(id='actor')) == configured
    hub.presences.assert_not_awaited()


@pytest.fixture
def connections_form():
    tree = ast.parse((ROOT / 'routers/configs.py').read_text(encoding='utf-8'))
    model = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConnectionsConfigForm')
    ns = {'BaseModel': BaseModel, 'field_validator': field_validator}
    exec(compile(ast.Module(body=[model], type_ignores=[]), '<connections-form>', 'exec'), ns)
    return ns['ConnectionsConfigForm']


def test_legacy_connections_payload_does_not_reset_new_settings(connections_form):
    form = connections_form(ENABLE_DIRECT_CONNECTIONS=True, ENABLE_BASE_MODELS_CACHE=False)
    assert 'ENABLE_TETHER' not in form.model_dump(exclude_unset=True)
    assert 'TERMINAL_PREVIEW_BASE_URL' not in form.model_dump(exclude_unset=True)
    assert form.ENABLE_TETHER is False
    assert form.SHOW_FILES_ON_TERMINAL_SELECT is False
    assert 'SHOW_FILES_ON_TERMINAL_SELECT' not in form.model_dump(exclude_unset=True)


@pytest.mark.asyncio
async def test_terminal_selection_setting_survives_partial_updates(connections_form):
    module = ast.parse((ROOT / 'routers/configs.py').read_text(encoding='utf-8'))
    keys = next(
        node.value
        for node in module.body
        if isinstance(node, ast.Assign) and node.targets[0].id == 'CONNECTIONS_CONFIG_KEYS'
    )
    storage = {}
    ns = {
        'Config': SimpleNamespace(
            upsert=AsyncMock(side_effect=storage.update),
            get_many=AsyncMock(side_effect=lambda *keys: {k: storage[k] for k in keys if k in storage}),
        ),
        'CONNECTIONS_CONFIG_KEYS': ast.literal_eval(keys),
        'publish_event': AsyncMock(),
        'EVENTS': SimpleNamespace(CONFIG_CONNECTIONS_UPDATED='updated'),
    }
    load_functions(
        'routers/configs.py',
        {'get_config_values', 'config_updates', 'set_connections_config', 'get_connections_config'},
        ns,
    )
    for enabled in (True, False):
        await ns['set_connections_config'](None, connections_form(SHOW_FILES_ON_TERMINAL_SELECT=enabled))
        await ns['set_connections_config'](None, connections_form(ENABLE_DIRECT_CONNECTIONS=True))
        assert storage['terminal.show_files_on_select'] is enabled
        assert (await ns['get_connections_config'](None))['SHOW_FILES_ON_TERMINAL_SELECT'] is enabled


def test_environment_defaults_keep_tether_preview_and_automatic_files_disabled():
    tree = ast.parse((ROOT / 'config.py').read_text(encoding='utf-8'))
    selected = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id in {'ENABLE_TETHER', 'TERMINAL_PREVIEW_BASE_URL', 'SHOW_FILES_ON_TERMINAL_SELECT'}
            for target in n.targets
        )
    ]
    ns = {'os': SimpleNamespace(getenv=lambda key, default=None: default)}
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<config-defaults>', 'exec'), ns)
    assert ns['ENABLE_TETHER'] is False
    assert ns['TERMINAL_PREVIEW_BASE_URL'] == ''
    assert ns['SHOW_FILES_ON_TERMINAL_SELECT'] is False


@pytest.mark.asyncio
async def test_relay_bounded_file_probe_preserves_remaining_stream_bytes():
    tree = ast.parse((ROOT / 'utils/tether.py').read_text(encoding='utf-8'))
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in {'RelayResponse', 'RelayInterrupted'}
    ]
    ns = {'asyncio': asyncio, 'CIMultiDict': CIMultiDict, 'HTTPException': HTTPException, 'MAX_MESSAGE': 1024}
    exec(compile(ast.Module(body=classes, type_ignores=[]), '<relay-response>', 'exec'), ns)
    stream = SimpleNamespace(
        receive=AsyncMock(side_effect=[({'type': 'data'}, b'ab'), ({'type': 'data'}, b'cdef'), ({'type': 'end'}, b'')]),
        consumed=AsyncMock(),
        close=AsyncMock(),
    )
    response = ns['RelayResponse'](stream, {'status': 200}, asyncio.create_task(asyncio.sleep(0)))
    assert await response.content.read(3) == b'abc'
    assert b''.join([chunk async for chunk in response.content.iter_any()]) == b'def'
    await response.close()
