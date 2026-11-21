"""Exercise the update endpoint without contacting either release service."""

import ast
import asyncio
import logging
from pathlib import Path

import aiohttp
import pytest


def updates(monkeypatch, tags, enabled=True):
    source = Path(__file__).parents[1] / 'open_webui/main.py'
    endpoint = next(
        node
        for node in ast.parse(source.read_text(encoding='utf-8')).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'get_app_latest_release_version'
    )
    endpoint.decorator_list = []
    endpoint.args.defaults = [ast.Constant(value=None)]
    requested = []

    class Response:
        def __init__(self, tag):
            self.tag = tag

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def raise_for_status(self):
            if isinstance(self.tag, Exception):
                raise self.tag

        async def json(self):
            return {'tag_name': self.tag}

    class Session(Response):
        def __init__(self, **kwargs):
            pass

        def get(self, url, **kwargs):
            requested.append(url)
            return Response(tags[url])

    monkeypatch.setattr(aiohttp, 'ClientSession', Session)
    env = {
        'ENABLE_VERSION_UPDATE_CHECK': enabled,
        'VERSION': '0.13.4+f1',
        'AIOHTTP_CLIENT_SESSION_SSL': True,
        'aiohttp': aiohttp,
        'asyncio': asyncio,
        'log': logging.getLogger(__name__),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[endpoint], type_ignores=[])), str(source), 'exec'), env)
    return env[endpoint.name], requested


UPSTREAM = 'https://api.github.com/repos/open-webui/open-webui/releases/latest'
FORK = 'https://api.github.com/repos/compcj/open-webui/releases/latest'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'upstream,fork,expected',
    [
        ('v0.13.5', 'v0.13.4+f11', ('0.13.5', '0.13.4+f11')),
        (aiohttp.ClientError(), 'v0.13.4+f2', (None, '0.13.4+f2')),
        ('v0.13.5', TimeoutError(), ('0.13.5', None)),
    ],
)
async def test_release_sources_are_independent(monkeypatch, upstream, fork, expected):
    endpoint, requested = updates(monkeypatch, {UPSTREAM: upstream, FORK: fork})
    assert await endpoint() == {'current': '0.13.4+f1', 'latest': expected[0], 'fork_latest': expected[1]}
    assert set(requested) == {UPSTREAM, FORK}


@pytest.mark.asyncio
async def test_disabled_check_reports_installed_versions_offline(monkeypatch):
    endpoint, requested = updates(monkeypatch, {}, enabled=False)
    assert await endpoint() == {'current': '0.13.4+f1', 'latest': '0.13.4', 'fork_latest': '0.13.4+f1'}
    assert not requested
