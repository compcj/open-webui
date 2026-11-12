"""Configured subagent model order and existing access checks without app imports."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import load_functions


@pytest.fixture
def runtime():
    namespace = {
        'BYPASS_ADMIN_ACCESS_CONTROL': False,
        'BYPASS_MODEL_ACCESS_CONTROL': False,
        'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[])),
        'AccessGrants': SimpleNamespace(get_accessible_resource_ids=AsyncMock(return_value=['a', 'b'])),
        'has_access': AsyncMock(return_value=False),
    }
    load_functions(namespace, 'utils/models.py', {'get_filtered_models'})
    load_functions(namespace, 'utils/subagents.py', {'get_subagent_models'})

    async def select(pool_ids, configured, role='user'):
        pool = {
            model_id: {
                'id': model_id,
                'name': model_id.upper(),
                'info': {
                    'user_id': 'synthetic-user' if model_id == 'owned' else 'another-user',
                    'meta': {'description': f'{model_id} description'},
                },
            }
            for model_id in pool_ids
        }
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(MODELS=pool)))
        user = SimpleNamespace(id='synthetic-user', role=role)
        config = {'info': {'meta': {'subagents': {'model_ids': configured}}}}
        return await namespace['get_subagent_models'](request, user, config)

    return SimpleNamespace(select=select, **namespace)


@pytest.mark.asyncio
@pytest.mark.parametrize('pool_ids', [['a', 'b'], ['b', 'a']])
async def test_subagent_candidates_follow_configured_order_independent_of_model_pool(runtime, pool_ids):
    result = await runtime.select(pool_ids, ['b', 'a'])

    assert result == [
        {'id': 'b', 'name': 'B', 'description': 'b description'},
        {'id': 'a', 'name': 'A', 'description': 'a description'},
    ]


@pytest.mark.asyncio
async def test_subagent_candidates_deduplicate_ids_and_skip_unavailable_models(runtime):
    result = await runtime.select(['unselected', 'a', 'b'], ['b', 'missing', 'a', 'b', 'a'])

    assert [model['id'] for model in result] == ['b', 'a']
    call = runtime.AccessGrants.get_accessible_resource_ids.await_args
    assert call.kwargs['resource_ids'] == ['b', 'a']


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['user', 'admin'])
async def test_subagent_order_preserves_live_access_filtering_and_owned_models(runtime, role):
    result = await runtime.select(['blocked', 'a', 'owned', 'b'], ['b', 'blocked', 'owned', 'a'], role)

    assert [model['id'] for model in result] == ['b', 'owned', 'a']
    runtime.Groups.get_groups_by_member_id.assert_awaited_once_with('synthetic-user', db=None, include_inherited=True)
    call = runtime.AccessGrants.get_accessible_resource_ids.await_args
    assert call.kwargs['permission'] == 'read'
    assert call.kwargs['resource_ids'] == ['b', 'blocked', 'owned', 'a']


@pytest.mark.asyncio
async def test_subagent_configuration_does_not_grant_access(runtime):
    assert await runtime.select(['blocked'], ['blocked', 'missing']) == []


@pytest.mark.asyncio
async def test_subagents_without_configured_models_have_no_candidates(runtime):
    assert await runtime.select(['a', 'b'], []) == []
    runtime.AccessGrants.get_accessible_resource_ids.assert_not_awaited()
    runtime.Groups.get_groups_by_member_id.assert_not_awaited()
