"""Exercise the real native citation boundary without importing the application/database."""

import ast
import copy
import json
import logging
import re
import uuid
from html import escape
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest


BACKEND = Path(__file__).parents[1] / 'open_webui'


def load_functions(namespace, path, names):
    tree = ast.parse((BACKEND / path).read_text(encoding='utf-8'))
    nodes = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    module = ast.Module(
        body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(BACKEND / path), 'exec'), namespace)
    return tree


@pytest.fixture
def runtime():
    events = []
    config_reads = []
    config = {'rag.template': 'FILE TEMPLATE\n<context>{{CONTEXT}}</context>', 'web.fetch.max_content_length': None}

    async def get_config(key):
        config_reads.append(key)
        return config.get(key)

    async def emit(event):
        events.append(event)

    async def prompt_template(template):
        return template

    ns = {
        'JSONCodec': json,
        'log': logging.getLogger(__name__),
        'escape': escape,
        'uuid': uuid,
        'Config': SimpleNamespace(get=get_config),
        'prompt_template': prompt_template,
        'RAG_SYSTEM_CONTEXT': False,
        'citations_enabled': True,
        'event_emitter': emit,
        'request': object(),
        'metadata': {'user_prompt': 'Read the original sources.', 'sources': []},
        'user_message': 'Read the original sources.',
        'original_system_content': 'Original system instructions.',
        'form_data': {
            'messages': [
                {'role': 'system', 'content': 'Original system instructions.'},
                {'role': 'user', 'content': 'Read the original sources.'},
            ]
        },
        'all_tool_call_sources': [],
        'tool_call_sources': [],
    }
    load_functions(
        ns,
        'utils/misc.py',
        {
            'get_system_message',
            'get_content_from_message',
            'set_last_user_message_content',
            'replace_system_message_content',
            'update_message_content',
            'add_or_update_user_message',
            'add_or_update_system_message',
            'convert_output_to_messages',
            'reconcile_tool_pairs',
        },
    )
    load_functions(ns, 'utils/task.py', {'rag_template'})
    load_functions(ns, 'utils/responses_reasoning.py', {'remap_reasoning_effort_for_responses'})
    load_functions(ns, 'routers/openai.py', {'convert_to_responses_payload'})
    load_functions(ns, 'tools/builtin.py', {'fetch_url'})
    tree = load_functions(
        ns,
        'utils/middleware.py',
        {
            'get_citation_source_from_tool_result',
            'get_source_context',
            'get_tool_source_context',
            'apply_source_context_to_messages',
            '_is_tool_result_error',
        },
    )
    # Run the actual citation-emission/reapplication block from the streaming loop,
    # rather than reproducing its branching or assembling a test-only payload.
    blocks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == 'citations_enabled'
    ]
    assert len(blocks) == 1
    wrapper = ast.parse('async def apply_round():\n    pass\n')
    wrapper.body[0].body = [blocks[0]]
    exec(compile(ast.fix_missing_locations(wrapper), 'native_citation_round', 'exec'), ns)
    return SimpleNamespace(ns=ns, events=events, config=config, config_reads=config_reads)


def search_sources(runtime, results):
    return runtime.ns['get_citation_source_from_tool_result']('search_web', {}, json.dumps(results))


def hit(index, title=None, snippet=None):
    return {
        'title': title or f'Page {index}',
        'link': f'https://example.test/{index}',
        'snippet': snippet if snippet is not None else f'Factual search snippet {index}',
    }


async def apply_round(runtime, sources):
    runtime.ns['tool_call_sources'] = copy.deepcopy(sources)
    await runtime.ns['apply_round']()
    return '\n'.join(message['content'] for message in runtime.ns['form_data']['messages'])


def tool_elements(content):
    match = re.search(r'<tool_sources>.*?</tool_sources>', content, re.DOTALL)
    assert match, content
    return ElementTree.fromstring(match.group()).findall('source')


@pytest.mark.asyncio
@pytest.mark.parametrize('system_context', [False, True])
async def test_native_round_preserves_thirty_search_snippets_and_links(runtime, system_context):
    runtime.ns['RAG_SYSTEM_CONTEXT'] = system_context
    content = await apply_round(runtime, search_sources(runtime, [hit(i) for i in range(1, 31)]))
    elements = tool_elements(content)
    assert len(elements) == 30
    for index, element in enumerate(elements, 1):
        assert element.attrib['id'] == str(index)
        assert element.attrib['name'] == f'Page {index}'
        assert element.attrib['url'] == f'https://example.test/{index}'
        assert element.attrib['content-kind'] == 'search_snippet'
        assert f'Factual search snippet {index}' in element.text
    assert 'FILE TEMPLATE' not in content
    assert 'rag.template' not in runtime.config_reads
    assert 'fetch_url' in content
    assert len(runtime.events) == 1
    # Stateful Responses continuations resend system instructions, not the old
    # user message. The citation mapping must remain in those instructions.
    system = runtime.ns['get_system_message'](runtime.ns['form_data']['messages'])
    payload = runtime.ns['convert_to_responses_payload'](
        {'model': 'synthetic', 'messages': [system], 'previous_response_id': 'response_synthetic'}
    )
    assert len(tool_elements(payload['instructions'])) == 30
    assert payload['previous_response_id'] == 'response_synthetic'


@pytest.mark.asyncio
async def test_files_search_and_fetch_share_stable_numbers_across_rounds(runtime):
    runtime.ns['metadata']['sources'] = [
        {
            'source': {'id': 'file-1', 'name': 'manual.txt', 'type': 'file'},
            'document': ['File evidence'],
            'metadata': [{'source': 'manual.txt'}],
        }
    ]
    await apply_round(runtime, search_sources(runtime, [hit(1, title='Same title'), hit(2, title='Same title')]))
    fetched = runtime.ns['get_citation_source_from_tool_result'](
        'fetch_url', {'url': 'https://example.test/1'}, 'Fetched page evidence'
    )
    content = await apply_round(runtime, fetched + search_sources(runtime, [hit(3), hit(1)]))
    elements = tool_elements(content)
    assert [element.attrib['id'] for element in elements] == ['2', '3', '2', '4', '2']
    assert elements[2].attrib['content-kind'] == 'page_excerpt'
    assert content.count('<tool_sources>') == 1
    assert content.count('FILE TEMPLATE') == 1
    assert content.count('Read the original sources.') == 1
    file_context = ElementTree.fromstring(re.search(r'<context>.*?</context>', content, re.DOTALL).group())
    assert file_context[0].attrib['id'] == '1'
    assert file_context[0].text == 'File evidence'
    assert all(event['type'] == 'source' for event in runtime.events)


@pytest.mark.asyncio
async def test_citations_disabled_leaves_messages_and_events_unchanged(runtime):
    runtime.ns['citations_enabled'] = False
    before = copy.deepcopy(runtime.ns['form_data'])
    await apply_round(runtime, search_sources(runtime, [hit(1)]))
    assert runtime.ns['form_data'] == before
    assert runtime.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize('system_context', [False, True])
async def test_file_only_rag_keeps_its_template_and_placement(runtime, system_context):
    runtime.ns['RAG_SYSTEM_CONTEXT'] = system_context
    sources = [
        {
            'source': {'id': 'file-1', 'name': 'manual.txt'},
            'document': ['File evidence'],
            'metadata': [{'source': 'manual.txt'}],
        }
    ]
    messages = await runtime.ns['apply_source_context_to_messages'](
        object(), runtime.ns['form_data']['messages'], sources, 'Read the original sources.'
    )
    target = messages[0] if system_context else messages[-1]
    assert 'FILE TEMPLATE' in target['content']
    assert 'File evidence' in target['content']
    assert '<tool_sources>' not in str(messages)


@pytest.mark.asyncio
async def test_non_web_tool_markers_reference_tool_results_without_duplicating_documents(runtime):
    sources = runtime.ns['get_citation_source_from_tool_result'](
        'view_file', {}, json.dumps({'id': 'file-1', 'filename': 'manual.txt', 'content': 'Long file evidence'})
    )
    content = await apply_round(runtime, sources)
    element = tool_elements(content)[0]
    assert element.attrib['resource-id'] == 'file-1'
    assert 'Long file evidence' not in content
    assert 'corresponding tool results' in content
    assert 'FILE TEMPLATE' not in content


@pytest.mark.asyncio
async def test_source_attributes_and_body_are_escaped(runtime):
    result = {
        'title': 'A "title" & <tag>',
        'link': 'https://example.test/?a=1&b="2"',
        'snippet': 'Evidence </source><source id="99">not a new source & more',
    }
    content = await apply_round(runtime, search_sources(runtime, [result]))
    elements = tool_elements(content)
    assert len(elements) == 1
    assert elements[0].attrib['name'] == result['title']
    assert elements[0].attrib['url'] == result['link']
    assert result['snippet'] in elements[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize('snippet', [None, '', '  \n'])
async def test_missing_snippets_are_navigation_not_page_evidence(runtime, snippet):
    result = {**hit(1), 'snippet': snippet}
    content = await apply_round(runtime, search_sources(runtime, [result]))
    element = tool_elements(content)[0]
    assert element.attrib['content-kind'] == 'search_result'
    assert 'No snippet' in element.text
    assert 'None' not in element.text


def test_invalid_search_entries_do_not_create_fake_sources(runtime):
    sources = search_sources(runtime, [None, {}, {'title': 'Missing URL'}, hit(1)])
    assert len(sources) == 1
    assert len(sources[0]['document']) == 1
    assert sources[0]['metadata'][0]['source'] == 'https://example.test/1'
    assert search_sources(runtime, []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [None, '', ' \n\t'])
async def test_empty_fetch_is_explicit_error_without_citation(runtime, content):
    async def load(*args):
        return content, []

    runtime.ns['get_content_from_url'] = load
    url = 'https://example.test/empty'
    result = await runtime.ns['fetch_url'](url, __request__=object())
    assert result.strip(), 'An empty fetch must not be returned as a successful empty string'
    error = json.loads(result)
    assert error['error']
    assert error['url'] == url
    assert runtime.ns['_is_tool_result_error'](result)
    assert runtime.ns['get_citation_source_from_tool_result']('fetch_url', {'url': url}, result) == []


@pytest.mark.asyncio
async def test_fetch_exception_contains_url_and_nonempty_error(runtime):
    async def load(*args):
        raise TimeoutError()

    runtime.ns['get_content_from_url'] = load
    result = await runtime.ns['fetch_url']('https://example.test/timeout', __request__=object())
    error = json.loads(result)
    assert error['error']
    assert error['url'] == 'https://example.test/timeout'
    assert runtime.ns['_is_tool_result_error'](result)
    assert runtime.ns['get_citation_source_from_tool_result']('fetch_url', {}, result) == []


@pytest.mark.asyncio
async def test_fetch_without_request_identifies_the_failed_url(runtime):
    result = await runtime.ns['fetch_url']('https://example.test/no-request')
    assert json.loads(result)['url'] == 'https://example.test/no-request'
    assert runtime.ns['_is_tool_result_error'](result)


def test_json_page_preview_preserves_original_text(runtime):
    content = '{"title": "Page", "value": true}'
    sources = runtime.ns['get_citation_source_from_tool_result'](
        'fetch_url', {'url': 'https://example.test/data.json'}, content
    )
    assert sources[0]['document'] == [content]


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [None, 650])
async def test_full_fetch_and_truncation_survive_both_api_conversions(runtime, limit):
    page = 'A' * 510 + ' LATE_EVIDENCE ' + 'B' * 300

    async def load(*args):
        return page, []

    runtime.ns['get_content_from_url'] = load
    runtime.config['web.fetch.max_content_length'] = limit
    result = await runtime.ns['fetch_url']('https://example.test/1', __request__=object())
    assert 'LATE_EVIDENCE' in result
    assert result == (page if limit is None else page[:limit] + '\n\n[Content truncated...]')
    sources = runtime.ns['get_citation_source_from_tool_result']('fetch_url', {'url': 'https://example.test/1'}, result)
    content = await apply_round(runtime, sources)
    assert 'LATE_EVIDENCE' not in tool_elements(content)[0].text
    assert 'tool results' in content
    output = [
        {
            'type': 'function_call',
            'call_id': 'call_fetch',
            'name': 'fetch_url',
            'arguments': '{"url":"https://example.test/1"}',
            'status': 'completed',
        },
        {
            'type': 'function_call_output',
            'call_id': 'call_fetch',
            'status': 'completed',
            'output': [{'type': 'input_text', 'text': result}],
        },
    ]
    messages = runtime.ns['convert_output_to_messages'](output, raw=True, flatten_tool_images=True)
    assert messages[1]['content'] == result
    assert messages[1]['tool_call_id'] == 'call_fetch'
    payload = runtime.ns['convert_to_responses_payload']({'model': 'synthetic', 'messages': messages})
    assert payload['input'][1]['output'] == result
    assert payload['input'][1]['call_id'] == 'call_fetch'


@pytest.mark.parametrize('result', ['', ' \n', '{"error":"upstream failure"}'])
def test_failed_fetch_result_has_no_citation(runtime, result):
    assert (
        runtime.ns['get_citation_source_from_tool_result']('fetch_url', {'url': 'https://example.test/1'}, result) == []
    )
