import importlib.util
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

import pytest


@dataclass
class StubSearchResult:
    link: str
    title: str | None
    snippet: str | None

    def model_dump(self):
        return asdict(self)


def filter_results(results, filter_list):
    allowed = [entry for entry in filter_list if entry and not entry.startswith('!')]
    blocked = [entry[1:] for entry in filter_list if entry and entry.startswith('!')]

    def matches(host, domain):
        return host == domain or host.endswith(f'.{domain}')

    return [
        result
        for result in results
        if (
            not allowed
            or any(
                matches(urlparse(result['link']).hostname or '', domain)
                for domain in allowed
            )
        )
        and not any(
            matches(urlparse(result['link']).hostname or '', domain)
            for domain in blocked
        )
    ]


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def load_module(response):
    session = FakeSession(response)

    async def fake_get_session():
        return session

    main_module = ModuleType('open_webui.retrieval.web.main')
    main_module.SearchResult = StubSearchResult
    main_module.get_filtered_results = filter_results
    pool_module = ModuleType('open_webui.utils.session_pool')
    pool_module.get_session = fake_get_session
    module_path = (
        Path(__file__).parents[1]
        / 'open_webui'
        / 'retrieval'
        / 'web'
        / 'search1api.py'
    )
    spec = importlib.util.spec_from_file_location('search1api_under_test', module_path)
    module = importlib.util.module_from_spec(spec)
    old_main = sys.modules.get('open_webui.retrieval.web.main')
    old_pool = sys.modules.get('open_webui.utils.session_pool')
    sys.modules['open_webui.retrieval.web.main'] = main_module
    sys.modules['open_webui.utils.session_pool'] = pool_module
    try:
        spec.loader.exec_module(module)
    finally:
        if old_main is None:
            sys.modules.pop('open_webui.retrieval.web.main', None)
        else:
            sys.modules['open_webui.retrieval.web.main'] = old_main
        if old_pool is None:
            sys.modules.pop('open_webui.utils.session_pool', None)
        else:
            sys.modules['open_webui.utils.session_pool'] = old_pool
    return module, session


@pytest.mark.asyncio
async def test_request_and_result_normalisation():
    module, session = load_module(
        FakeResponse(
            {
                'results': [
                    {
                        'title': 'First',
                        'link': 'https://first.example/a',
                        'snippet': 'A',
                    },
                    {
                        'title': 'Second',
                        'link': 'https://second.example/b',
                        'content': 'B',
                    },
                    {
                        'title': 'Third',
                        'link': 'https://third.example/c',
                        'snippet': 'C',
                    },
                ]
            }
        )
    )
    results = await module.search_search1api('test-key', 'open webui', 2)
    assert session.calls == [
        (
            'https://api.search1api.com/search',
            {
                'headers': {
                    'Authorization': 'Bearer test-key',
                    'Content-Type': 'application/json',
                },
                'json': {'query': 'open webui', 'max_results': 2},
            },
        )
    ]
    assert [result.model_dump() for result in results] == [
        {'link': 'https://first.example/a', 'title': 'First', 'snippet': 'A'},
        {'link': 'https://second.example/b', 'title': 'Second', 'snippet': 'B'},
    ]


@pytest.mark.asyncio
async def test_allow_and_block_domain_filters():
    module, _ = load_module(
        FakeResponse(
            {
                'results': [
                    {
                        'title': 'Allowed',
                        'link': 'https://allowed.example/a',
                        'snippet': 'A',
                    },
                    {
                        'title': 'Subdomain',
                        'link': 'https://sub.allowed.example/b',
                        'snippet': 'B',
                    },
                    {
                        'title': 'Blocked',
                        'link': 'https://blocked.allowed.example/c',
                        'snippet': 'C',
                    },
                    {
                        'title': 'Outside',
                        'link': 'https://outside.example/d',
                        'snippet': 'D',
                    },
                ]
            }
        )
    )
    results = await module.search_search1api(
        'test-key', 'filtered query', 10, ['allowed.example', '!blocked.allowed.example']
    )
    assert [result.title for result in results] == ['Allowed', 'Subdomain']


@pytest.mark.asyncio
async def test_empty_results():
    module, _ = load_module(FakeResponse({'results': []}))
    assert await module.search_search1api('test-key', 'none', 3) == []


@pytest.mark.asyncio
async def test_http_error_is_propagated():
    module, _ = load_module(FakeResponse(error=RuntimeError('upstream failed')))
    with pytest.raises(RuntimeError, match='upstream failed'):
        await module.search_search1api('test-key', 'query', 3)
