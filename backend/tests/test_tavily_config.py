"""Run the actual Tavily configuration and routing boundaries in isolation."""

import ast
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Literal, Union
from unittest.mock import Mock

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy import JSON, BigInteger, Column, Text, delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

BACKEND = Path(__file__).parents[1] / 'open_webui'
DEPTH_KEY = 'web.search.tavily_search_depth'


def execute_nodes(path, names, namespace):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        name = getattr(node, 'name', None)
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        if name in names:
            if hasattr(node, 'decorator_list'):
                node.decorator_list = []
            nodes.append(node)
    nodes.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), path, 'exec'), namespace)
    return namespace


def tavily_defaults():
    tree = ast.parse((BACKEND / 'config.py').read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id.startswith('TAVILY_'):
            nodes.append(node)
        elif node.targets[0].id == 'DEFAULT_CONFIG':
            pairs = [
                (k, v) for k, v in zip(node.value.keys, node.value.values) if k.value.startswith('web.search.tavily_')
            ]
            node.value = ast.Dict(keys=[k for k, _ in pairs], values=[v for _, v in pairs])
            nodes.append(node)
    namespace = {'os': os}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), 'config.py', 'exec'), namespace)
    return namespace['DEFAULT_CONFIG']


@pytest.mark.parametrize('env_depth', [None, 'advanced'])
def test_registered_default_uses_environment_or_basic(monkeypatch, env_depth):
    if env_depth is None:
        monkeypatch.delenv('TAVILY_SEARCH_DEPTH', raising=False)
    else:
        monkeypatch.setenv('TAVILY_SEARCH_DEPTH', env_depth)
    assert tavily_defaults().get(DEPTH_KEY) == (env_depth or 'basic')


@pytest_asyncio.fixture
async def runtime(monkeypatch):
    monkeypatch.delenv('TAVILY_SEARCH_DEPTH', raising=False)
    base = declarative_base()
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_async_db():
        async with sessions() as session:
            yield session

    namespace = {
        '__name__': __name__,
        'Base': base,
        'JSON': JSON,
        'BigInteger': BigInteger,
        'Column': Column,
        'Text': Text,
        'select': select,
        'delete': delete,
        'Any': Any,
        'ClassVar': ClassVar,
        'time': time,
        'get_async_db': get_async_db,
        'jsonable_encoder': jsonable_encoder,
        'log': logging.getLogger(__name__),
        'BaseModel': BaseModel,
        'Field': Field,
        'Literal': Literal,
        'Union': Union,
        'SimpleNamespace': SimpleNamespace,
        'Depends': Depends,
        'get_admin_user': lambda: 'admin',
        'Request': Request,
        'USE_SLIM': False,
        'asyncio': asyncio,
    }
    execute_nodes('models/config.py', {'_json_value', 'Config'}, namespace)
    execute_nodes(
        'routers/retrieval.py',
        {
            'RETRIEVAL_CONFIG_KEYS',
            'RetrievalConfig',
            'get_config_values',
            'get_retrieval_config',
            'WebConfig',
            'ConfigForm',
            'get_rag_config',
            'update_rag_config',
            'search_web',
        },
        namespace,
    )
    namespace['ConfigForm'].model_rebuild(_types_namespace=namespace)
    defaults = {key: None for key in namespace['RETRIEVAL_CONFIG_KEYS'].values()}
    defaults.update(tavily_defaults())
    defaults.update({'rag.reranking_engine': 'external', 'rag.hybrid_search': False})
    namespace['Config'].configure(defaults=defaults)
    async with engine.begin() as connection:
        await connection.run_sync(base.metadata.create_all)
    app = FastAPI()
    app.state.YOUTUBE_LOADER_TRANSLATION = None
    app.get('/config')(namespace['get_rag_config'])
    app.post('/config/update')(namespace['update_rag_config'])
    namespace['app'] = app
    try:
        yield namespace
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_config_returns_default_basic_depth(runtime):
    async with AsyncClient(transport=ASGITransport(app=runtime['app']), base_url='http://test') as client:
        response = await client.get('/config')
    assert response.status_code == 200
    assert response.json()['web'].get('TAVILY_SEARCH_DEPTH') == 'basic'


@pytest.mark.asyncio
@pytest.mark.parametrize('depth', ['basic', 'advanced'])
async def test_admin_update_persists_depth_and_get_reads_saved_value(runtime, depth):
    async with AsyncClient(transport=ASGITransport(app=runtime['app']), base_url='http://test') as client:
        response = await client.post('/config/update', json={'web': {'TAVILY_SEARCH_DEPTH': depth}})
        saved = await client.get('/config')
    assert response.status_code == 200
    assert response.json()['web'].get('TAVILY_SEARCH_DEPTH') == depth
    assert saved.json()['web'].get('TAVILY_SEARCH_DEPTH') == depth
    assert (await runtime['Config'].get_many(DEPTH_KEY)).get(DEPTH_KEY) == depth


@pytest.mark.asyncio
async def test_saved_depth_takes_precedence_over_environment_default(runtime):
    await runtime['Config'].upsert({DEPTH_KEY: 'advanced'})
    config = await runtime['get_retrieval_config']()
    assert getattr(config, 'TAVILY_SEARCH_DEPTH', None) == 'advanced'


@pytest.mark.asyncio
@pytest.mark.parametrize('web', [{}, {'TAVILY_SEARCH_DEPTH': None}])
async def test_legacy_omission_and_null_preserve_saved_depth(runtime, web):
    await runtime['Config'].upsert({DEPTH_KEY: 'advanced'})
    async with AsyncClient(transport=ASGITransport(app=runtime['app']), base_url='http://test') as client:
        response = await client.post('/config/update', json={'web': web})
    assert response.status_code == 200
    assert response.json()['web'].get('TAVILY_SEARCH_DEPTH') == 'advanced'
    assert await runtime['Config'].get(DEPTH_KEY) == 'advanced'


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_depth', ['auto', '', 'ADVANCED', 2])
async def test_invalid_depth_returns_422_without_mutating_saved_value(runtime, invalid_depth):
    await runtime['Config'].upsert({DEPTH_KEY: 'advanced'})
    async with AsyncClient(transport=ASGITransport(app=runtime['app']), base_url='http://test') as client:
        response = await client.post('/config/update', json={'web': {'TAVILY_SEARCH_DEPTH': invalid_depth}})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'] == ['body', 'web', 'TAVILY_SEARCH_DEPTH']
    assert await runtime['Config'].get(DEPTH_KEY) == 'advanced'


@pytest.mark.asyncio
@pytest.mark.parametrize('depth', ['basic', 'advanced'])
async def test_unified_search_router_forwards_configured_depth_by_keyword(runtime, depth):
    await runtime['Config'].upsert(
        {
            DEPTH_KEY: depth,
            'web.search.tavily_api_key': 'test-key',
            'web.search.result_count': 4,
            'web.search.domain.filter_list': ['allowed.example'],
        }
    )
    provider = Mock(return_value=['result'])
    runtime['search_tavily'] = provider
    results = await runtime['search_web'](None, 'tavily', 'query')
    assert results == ['result']
    provider.assert_called_once_with('test-key', 'query', 4, ['allowed.example'], search_depth=depth)


@pytest.mark.asyncio
async def test_unified_search_router_preserves_missing_key_error(runtime):
    await runtime['Config'].upsert({'web.search.tavily_api_key': ''})
    provider = Mock()
    runtime['search_tavily'] = provider
    with pytest.raises(Exception, match='No TAVILY_API_KEY found in environment variables'):
        await runtime['search_web'](None, 'tavily', 'query')
    provider.assert_not_called()


def test_web_config_omission_and_null_remain_optional():
    namespace = {'BaseModel': BaseModel, 'Field': Field, 'Literal': Literal}
    execute_nodes('routers/retrieval.py', {'WebConfig'}, namespace)
    namespace['WebConfig'].model_rebuild(_types_namespace=namespace)
    assert 'TAVILY_SEARCH_DEPTH' in namespace['WebConfig'].model_fields
    assert namespace['WebConfig']().TAVILY_SEARCH_DEPTH is None
    assert namespace['WebConfig'](TAVILY_SEARCH_DEPTH=None).TAVILY_SEARCH_DEPTH is None
