"""Isolated defaults for the administrator-configured skill paging limits."""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def skill_config(monkeypatch):
    module = ModuleType('open_webui.models.config')
    module.Config = SimpleNamespace(get=AsyncMock(return_value=None), get_many=AsyncMock(return_value={}))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.Config
