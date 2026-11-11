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


def test_legacy_connections_payload_does_not_reset_new_settings():
    tree = ast.parse((ROOT / 'routers/configs.py').read_text(encoding='utf-8'))
    model = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConnectionsConfigForm')
    ns = {'BaseModel': BaseModel, 'field_validator': field_validator}
    exec(compile(ast.Module(body=[model], type_ignores=[]), '<connections-form>', 'exec'), ns)
    form = ns['ConnectionsConfigForm'](ENABLE_DIRECT_CONNECTIONS=True, ENABLE_BASE_MODELS_CACHE=False)
    assert 'ENABLE_TETHER' not in form.model_dump(exclude_unset=True)
    assert 'TERMINAL_PREVIEW_BASE_URL' not in form.model_dump(exclude_unset=True)
    assert form.ENABLE_TETHER is False


def test_environment_defaults_keep_tether_and_preview_disabled():
    tree = ast.parse((ROOT / 'config.py').read_text(encoding='utf-8'))
    selected = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id in {'ENABLE_TETHER', 'TERMINAL_PREVIEW_BASE_URL'}
            for target in n.targets
        )
    ]
    ns = {'os': SimpleNamespace(getenv=lambda key, default=None: default)}
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<config-defaults>', 'exec'), ns)
    assert ns['ENABLE_TETHER'] is False
    assert ns['TERMINAL_PREVIEW_BASE_URL'] == ''


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
