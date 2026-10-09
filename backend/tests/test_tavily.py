"""Exercise Tavily's HTTP contract without importing the application."""

import ast
import logging
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from pydantic import BaseModel

BACKEND = Path(__file__).parents[1] / 'open_webui'


def load_search(response):
    namespace = {
        'requests': Mock(post=Mock(return_value=response)),
        'TAVILY_API_BASE_URL': 'https://tavily.example',
        'log': logging.getLogger(__name__),
        'BaseModel': BaseModel,
        'get_filtered_results': Mock(side_effect=lambda results, filters: results[:1]),
    }
    nodes = []
    for path, name in [('retrieval/web/main.py', 'SearchResult'), ('retrieval/web/tavily.py', 'search_tavily')]:
        tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
        nodes.append(next(node for node in tree.body if getattr(node, 'name', None) == name))
    nodes.insert(0, ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0))
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<tavily>', 'exec'), namespace)
    return namespace


def test_default_request_explicitly_uses_basic_depth():
    response = Mock()
    response.json.return_value = {'results': [{'url': 'https://result.example/a', 'title': 'Title', 'content': 'Text'}]}
    runtime = load_search(response)
    results = runtime['search_tavily']('test-key', 'query', 3)
    runtime['requests'].post.assert_called_once_with(
        'https://tavily.example/search',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer test-key'},
        json={'query': 'query', 'max_results': 3, 'search_depth': 'basic'},
    )
    response.raise_for_status.assert_called_once_with()
    assert [result.model_dump() for result in results] == [
        {'link': 'https://result.example/a', 'title': 'Title', 'snippet': 'Text'}
    ]
    runtime['get_filtered_results'].assert_not_called()


@pytest.mark.parametrize('depth', ['basic', 'advanced'])
def test_selected_search_depth_is_sent_to_tavily(depth):
    response = Mock()
    response.json.return_value = {'results': []}
    runtime = load_search(response)
    assert runtime['search_tavily']('test-key', 'query', 5, search_depth=depth) == []
    assert runtime['requests'].post.call_args.kwargs['json'] == {
        'query': 'query',
        'max_results': 5,
        'search_depth': depth,
    }


def test_fourth_positional_argument_remains_domain_filter_list():
    response = Mock()
    payload = [{'url': 'https://allowed.example/a'}, {'url': 'https://blocked.example/b', 'content': 'B'}]
    response.json.return_value = {'results': payload}
    runtime = load_search(response)
    filters = ['allowed.example', '!blocked.example']
    results = runtime['search_tavily']('test-key', 'query', 2, filters)
    runtime['get_filtered_results'].assert_called_once_with(payload, filters)
    assert [result.model_dump() for result in results] == [
        {'link': 'https://allowed.example/a', 'title': '', 'snippet': None}
    ]


@pytest.mark.parametrize('payload', [{}, {'results': []}])
def test_empty_results_remain_empty(payload):
    response = Mock()
    response.json.return_value = payload
    assert load_search(response)['search_tavily']('test-key', 'query', 2) == []


def test_http_error_is_propagated_without_reading_results():
    response = Mock()
    error = requests.HTTPError('upstream failed')
    response.raise_for_status.side_effect = error
    with pytest.raises(requests.HTTPError, match='upstream failed'):
        load_search(response)['search_tavily']('test-key', 'query', 2)
    response.json.assert_not_called()
