"""Execute the merged policy boundaries without importing application state."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest
from fastapi import HTTPException
from pydantic import BaseModel

from skill_test_config import skill_config
from test_auth_revocation import load_functions
from test_model_controls_compat import helper

ROOT = Path(__file__).parents[1] / 'open_webui'


def request_fragment(start, end, scope):
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    endpoint = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    nodes = next(node.body for node in endpoint.body if isinstance(node, ast.Try))
    begin = next(
        i for i, node in enumerate(nodes) if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == start
    )
    finish = next(
        i
        for i in range(begin + 1, len(nodes))
        if isinstance(nodes[i], ast.Assign) and ast.unparse(nodes[i].targets[0]) == end
    )
    wrapper = ast.parse('async def run():\n    pass')
    wrapper.body[0].body = [
        *copy.deepcopy(nodes[begin:finish]),
        ast.Return(value=ast.Call(func=ast.Name(id='locals', ctx=ast.Load()), args=[], keywords=[])),
    ]
    exec(compile(ast.fix_missing_locations(wrapper), '<request-policy>', 'exec'), scope)
    return scope['run']


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
@pytest.mark.parametrize('enabled', [False, True])
async def test_integrations_hard_switch_keeps_saved_config_and_has_no_admin_bypass(role, enabled):
    saved = [{'id': 'personal', 'url': 'https://synthetic.invalid'}]
    original = copy.deepcopy(saved)
    scope = {
        'form_data': {'tool_servers': copy.deepcopy(saved)},
        'user': SimpleNamespace(id='actor', role=role),
        'Config': SimpleNamespace(
            get=AsyncMock(side_effect=lambda key, *args: enabled if key == 'direct.integrations.enable' else {})
        ),
        'has_permission': AsyncMock(return_value=True),
    }
    result = await request_fragment('tool_servers', 'automation_id', scope)()
    assert result['tool_servers'] == (original if enabled else None)
    assert saved == original


@pytest.mark.asyncio
async def test_controls_and_advanced_params_are_not_authorized_by_panel_permissions():
    scope = {
        'form_data': {'params': {'temperature': 0.7, 'model_controls': {'model': {'reasoning': 'high'}}}},
        'model_info_params': {},
        'missing_base_model': False,
        'agent_model': None,
        'model_id': 'model',
        'merge_model_params': lambda a, b: {**a, **b},
        'HTTPException': HTTPException,
        'has_permission': AsyncMock(return_value=False),
    }
    # The first boundary extracts explicit choices, then the real server-defined control resolver validates them.
    result = await request_fragment('request_params', 'stream_delta_chunk_size', scope)()
    assert result['request_params']['temperature'] == 0.7
    apply = helper('utils/payload.py', {'apply_model_controls'})['apply_model_controls']
    controls = {'reasoning': {'options': {'high': {'params': {'reasoning_effort': 'high'}}}}}
    params = apply(scope['form_data']['params'], controls, result['model_controls']['model'])
    assert params['custom_params']['reasoning_effort'] == 'high'
    with pytest.raises(HTTPException) as error:
        apply({}, controls, {'reasoning': 'not-a-valid-option'})
    assert error.value.status_code == 400
    scope['has_permission'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('lazy,expected', [(False, {'selected', 'mentioned', 'preset'}), (True, {'mentioned'})])
async def test_selected_and_preset_skills_eager_by_default_mentions_stay_eager(lazy, expected):
    tree = ast.parse((ROOT / 'utils/middleware.py').read_text(encoding='utf-8'))
    process = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'process_chat_payload'
    )
    start = next(
        i
        for i, node in enumerate(process.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'activated_skill_ids'
    )
    finish = next(
        i
        for i in range(start + 1, len(process.body))
        if isinstance(process.body[i], ast.Assign) and ast.unparse(process.body[i].targets[0]) == 'available_skills'
    )
    wrapper = ast.parse('async def run():\n    pass')
    wrapper.body[0].body = [
        *copy.deepcopy(process.body[start:finish]),
        ast.parse('return activated_skill_ids, skill_ids').body[0],
    ]
    scope = {
        'form_data': {'skill_ids': ['selected']},
        'mentioned_skill_ids': {'mentioned'},
        'model': {'info': {'meta': {'skillIds': ['preset']}}},
        'payload_tools': None,
        'Config': SimpleNamespace(get=AsyncMock(return_value=lazy)),
    }
    exec(compile(ast.fix_missing_locations(wrapper), '<skill-selection>', 'exec'), scope)
    activated, available = await scope['run']()
    assert activated == expected
    assert set(available) == {'selected', 'mentioned', 'preset'}


@pytest.mark.asyncio
async def test_configured_skill_limits_preserve_exact_offsets_and_disabled_tool_cap(skill_config):
    from open_webui.utils.skill_files import skill_overview, skill_content_page

    skill_config.get_many.return_value = {'skills.content_max_chars': 6000, 'skills.preview_max_chars': 1200}
    skill_config.get.return_value = 6000
    text = '中😀' * 60000
    overview = await skill_overview(text, [])
    assert overview['content'] == text[:1200]
    assert overview['next_offset'] == 1200
    page = await skill_content_page(text, overview['next_offset'], 100000)
    assert page == {'content': text[1200:7200], 'next_offset': 7200}
    assert (await skill_overview(text, [], tools_enabled=False))['content'] == text[:100000]


@pytest.fixture
def sharing():
    scope = {
        'BaseModel': BaseModel,
        'uuid': uuid,
        'HTTPException': HTTPException,
        'PRINCIPAL_TYPE_USER': 'user',
        'PRINCIPAL_TYPE_GROUP': 'group',
        'PRINCIPAL_TYPE_ANYONE': 'anyone',
        'WILDCARD_PRINCIPAL_ID': '*',
    }
    load_functions('models/access_grants.py', {'normalize_access_grants', 'strip_anyone_access_grants'}, scope)
    tree = ast.parse((ROOT / 'utils/access_control/__init__.py').read_text(encoding='utf-8'))
    mapping = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == '_SHARING_PERMISSION_KEYS'
    )
    scope['_SHARING_PERMISSION_KEYS'] = ast.literal_eval(mapping)
    scope['get_permissions'] = AsyncMock(
        return_value={
            'sharing': {'skills': True, 'public_skills': False},
            'access_grants': {'allow_users': True, 'allow_groups': True},
        }
    )
    load_functions(
        'utils/access_control/__init__.py', {'filter_allowed_access_grants', 'validate_access_grants'}, scope
    )
    return scope


@pytest.mark.asyncio
async def test_creation_filters_forbidden_grants_but_access_save_fails_without_mutation(sharing):
    grant = {'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}
    assert await sharing['filter_allowed_access_grants']({}, 'owner', 'user', [grant], 'skill') == []
    skill = SimpleNamespace(id='skill', user_id='owner', name='Skill')
    writes = AsyncMock()
    sharing.update(
        {
            'Skills': SimpleNamespace(get_skill_by_id=AsyncMock(return_value=skill)),
            'AccessGrants': SimpleNamespace(set_access_grants=writes),
            'Config': SimpleNamespace(get=AsyncMock(return_value={})),
            'publish_event': AsyncMock(),
            'EVENTS': SimpleNamespace(SKILL_UPDATED='skill.updated'),
        }
    )
    load_functions('routers/skills.py', {'update_skill_access_by_id'}, sharing)
    actor = SimpleNamespace(id='owner', role='user')
    with pytest.raises(HTTPException) as error:
        await sharing['update_skill_access_by_id'](None, 'skill', SimpleNamespace(access_grants=[grant]), actor, None)
    assert error.value.status_code == 403
    writes.assert_not_awaited()
    allowed = {**grant, 'principal_id': 'collaborator'}
    await sharing['update_skill_access_by_id'](None, 'skill', SimpleNamespace(access_grants=[allowed]), actor, None)
    writes.assert_awaited_once()
    assert writes.call_args.args[2][0]['principal_id'] == 'collaborator'
