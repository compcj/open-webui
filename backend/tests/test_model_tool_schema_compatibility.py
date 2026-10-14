"""Explicit model metadata controls tool adaptation without provider-name guesses."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from open_webui.utils import gemini
from test_model_controls_compat import model_controls_schema


@pytest.mark.parametrize('mode', [None, 'default', 'gemini'])
def test_model_form_preserves_manual_compatibility_through_json_round_trip(mode):
    schema = model_controls_schema()
    form = schema['ModelForm'](id='opaque-alias', name='Alias', params={}, meta={'tool_schema_compatibility': mode})
    restored = schema['ModelForm'].model_validate_json(form.model_dump_json())
    assert restored.meta.tool_schema_compatibility == mode
    assert 'tool_schema_compatibility' not in restored.params.model_dump()


@pytest.mark.parametrize('mode', ['auto', 'Gemini', '', True, 1, {}])
def test_model_metadata_rejects_invalid_compatibility(mode):
    with pytest.raises(ValidationError):
        model_controls_schema()['ModelMeta'](tool_schema_compatibility=mode)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode,expected', [(None, 'gemini'), ('gemini', 'gemini'), ('default', 'default'), ('auto', 'default')]
)
async def test_saved_model_choice_inherits_or_overrides_base(mode, expected):
    base = {'id': 'base', 'meta': {'tool_schema_compatibility': 'gemini'}}
    lookup = AsyncMock(return_value=base)
    model = SimpleNamespace(meta=SimpleNamespace(tool_schema_compatibility=mode), base_model_id='base')
    assert await gemini.resolve_tool_schema_compatibility(model, get_model=lookup) == expected
    assert lookup.await_count == int(mode is None)


@pytest.mark.asyncio
async def test_missing_configuration_does_not_infer_from_id_or_provider():
    model = {'id': 'google/gemini-test', 'owned_by': 'google', 'provider': 'gemini', 'meta': {}}
    assert await gemini.resolve_tool_schema_compatibility(model) == 'default'
    assert await gemini.resolve_tool_schema_compatibility(None) == 'default'


@pytest.mark.asyncio
async def test_inheritance_cycle_stops_without_enabling_conversion():
    models = {'a': {'base_model_id': 'b'}, 'b': {'base_model_id': 'a'}}
    lookup = AsyncMock(side_effect=models.get)
    assert await gemini.resolve_tool_schema_compatibility(models['a'], get_model=lookup) == 'default'
    assert lookup.await_count == 2


@pytest.mark.asyncio
async def test_direct_descriptor_never_loads_colliding_saved_models():
    model = {'id': 'server-id', 'direct': True, 'info': {'base_model_id': 'private-base', 'meta': {}}}
    lookup = AsyncMock(return_value={'meta': {'tool_schema_compatibility': 'gemini'}})
    assert await gemini.resolve_tool_schema_compatibility(model, get_model=lookup) == 'default'
    lookup.assert_not_awaited()
    model['info']['meta']['tool_schema_compatibility'] = 'gemini'
    assert await gemini.resolve_tool_schema_compatibility(model, get_model=lookup) == 'gemini'
    lookup.assert_not_awaited()
