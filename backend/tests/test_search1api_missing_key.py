"""Preserve the legacy dev branch missing-key routing regression."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_search_web_function(get_retrieval_config):
    router_path = Path(__file__).parents[1] / 'open_webui' / 'routers' / 'retrieval.py'
    tree = ast.parse(router_path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'search_web')
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), function],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {'Config': SimpleNamespace(get_many=get_retrieval_config), 'RETRIEVAL_CONFIG_KEYS': {}}
    exec(compile(module, router_path, 'exec'), namespace)
    return namespace['search_web']


@pytest.mark.asyncio
async def test_search_web_requires_search1api_api_key():
    async def fake_get_retrieval_config(*keys):
        return {'web.search.search1api_api_key': ''}

    search_web = load_search_web_function(fake_get_retrieval_config)

    with pytest.raises(Exception, match='No SEARCH1API_API_KEY found in environment variables'):
        await search_web(None, 'search1api', 'query')
