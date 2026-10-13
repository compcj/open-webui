"""Exercise native image results, provider payloads, and history replay without app imports."""

import ast
import asyncio
import copy
import importlib.util
import json
import logging
import re
from contextlib import asynccontextmanager
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

from test_web_tool_citations import BACKEND, load_functions


IMAGE_URL = '/api/v1/files/generated-image/content'
IMAGE_DATA = 'data:image/png;base64,c3ludGhldGlj'


@pytest.fixture
def runtime():
    ns = {
        'JSONCodec': json,
        'new_tool_output_ids': [],
        'json': json,
        'log': logging.getLogger(__name__),
        'HTMLResponse': HTMLResponse,
        'get_image_base64_from_url': AsyncMock(return_value=IMAGE_DATA),
        'output_id': lambda prefix: prefix + '-1',
        'BASE64_IMAGE_DATA_URI_RE': re.compile(r'data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+={0,2}'),
    }
    helper_path = BACKEND / 'utils/images/context.py'
    if helper_path.exists():
        spec = importlib.util.spec_from_file_location('image_context_under_test', helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        ns.update({name: getattr(helper, name) for name in ('get_image_tool_result_files', 'build_tool_result_parts')})
    load_functions(
        ns,
        'utils/misc.py',
        {
            'convert_output_to_messages',
            'reconcile_tool_pairs',
            'get_paired_tool_call_ids',
            'get_system_message',
            'get_last_user_message_item',
            'get_message_list',
            'get_content_from_message',
            'strip_empty_content_blocks',
            'merge_system_messages',
        },
    )
    load_functions(ns, 'utils/responses_reasoning.py', {'remap_reasoning_effort_for_responses'})
    load_functions(ns, 'routers/openai.py', {'convert_to_responses_payload'})
    tree = load_functions(
        ns,
        'utils/middleware.py',
        {
            'process_tool_result',
            'extract_base64_images',
            '_is_tool_result_error',
            'process_messages_with_output',
            'convert_url_images_to_base64',
            'sanitize_tool_pairs',
            'resume_tool_calls',
            'attach_tool_citations',
            'get_tool_citation_source_ids',
            'get_source_context',
            'add_file_context',
            'normalize_messages_for_model',
        },
    )
    ns.update(asyncio=asyncio, copy=copy, HTTPException=HTTPException, uuid4=uuid4, ENABLE_FUNCTIONS=False)
    load_functions(ns, 'utils/tool_approval.py', {'pending_tool_calls', 'tool_continuation', 'complete_tool_call'})
    # Execute the real streaming output assembly and frontend filtering blocks.
    result_loop = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == 'result'
        and isinstance(node.iter, ast.Name)
        and node.iter.id == 'results'
        and any(isinstance(child, ast.Constant) and child.value == 'function_call_output' for child in ast.walk(node))
    )
    frontend_loop = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.IfExp)
        and isinstance(node.iter.body, ast.Call)
        and isinstance(node.iter.body.func, ast.Name)
        and node.iter.body.func.id == 'full_output'
    )
    followup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.If)
            and isinstance(child.test, ast.BoolOp)
            and any(
                isinstance(value, ast.Name) and value.id == 'ENABLE_RESPONSES_API_STATEFUL'
                for value in child.test.values
            )
            for child in node.body
        )
    )
    dispatch_index = next(
        index
        for index, node in enumerate(followup.body)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Await)
        and isinstance(node.value.value, ast.Call)
        and isinstance(node.value.value.func, ast.Name)
        and node.value.value.func.id == 'generate_chat_completion'
    )
    wrapper = ast.parse(
        'async def dispatch_followup():\n    global messages\n    if messages is None:\n        messages = list(form_data["messages"])\n'
    )
    wrapper.body[0].body.extend(followup.body[: dispatch_index + 1])
    ns['messages'] = None
    exec(compile(ast.fix_missing_locations(wrapper), 'native_image_followup', 'exec'), ns)
    return SimpleNamespace(ns=ns, result_loop=result_loop, frontend_loop=frontend_loop)


def install_approval_store(ns, message):
    """Transaction double for the real claim/complete interface; retain its atomic ownership."""
    lock = asyncio.Lock()

    @asynccontextmanager
    async def edit_message_output(chat_id, message_id):
        async with lock:
            yield message

    ns['Chats'] = SimpleNamespace(edit_message_output=edit_message_output)
    ns['ENABLE_FUNCTIONS'] = False
    ns['UserModel'] = SimpleNamespace
    ns['ERROR_MESSAGES'] = SimpleNamespace(NOT_FOUND='not found')
    load_functions(ns, 'utils/tool_approval.py', {'pending_tool_calls', 'tool_continuation', 'complete_tool_call'})
    return lock


def result_output(runtime, tool_name='generate_image', images=None, tool_result=None):
    source = {'status': 'success', 'images': images if images is not None else [{'url': IMAGE_URL}]}
    content, files, embeds = asyncio.run(
        runtime.ns['process_tool_result'](None, tool_name, tool_result or json.dumps(source), 'builtin')
    )
    call = {'type': 'function_call', 'name': tool_name, 'call_id': 'call-1', 'status': 'completed', 'arguments': '{}'}
    ns = {
        **runtime.ns,
        'output': [call],
        'result_status_by_call_id': {},
        'results': [{'tool_call_id': 'call-1', 'content': content, 'files': files, 'embeds': embeds}],
    }
    wrapper = ast.parse('async def assemble():\n    pass\n')
    wrapper.body[0].body = [runtime.result_loop]
    exec(compile(ast.fix_missing_locations(wrapper), 'stream_result', 'exec'), ns)
    asyncio.run(ns['assemble']())
    return ns['output']


@pytest.mark.parametrize('tool_name', ['generate_image', 'edit_image'])
def test_builtin_persists_and_displays_the_attachment_once_while_returning_visual_context(runtime, tool_name):
    image = {'id': 'generated-image', 'url': IMAGE_URL}
    persist = AsyncMock(return_value=[{'type': 'image', **image}])
    emitter = AsyncMock()
    ns = runtime.ns
    ns.update(
        image_generations=AsyncMock(return_value=[image]),
        image_edits=AsyncMock(return_value=[image]),
        CreateImageForm=SimpleNamespace,
        EditImageForm=SimpleNamespace,
        is_saved_chat_id=lambda value: value == 'chat-1',
        Chats=SimpleNamespace(add_message_files_by_id_and_message_id=persist),
    )
    load_functions(ns, 'tools/builtin.py', {tool_name})
    args = {
        'prompt': 'A blue sky',
        '__request__': object(),
        '__chat_id__': 'chat-1',
        '__message_id__': 'assistant-1',
        '__event_emitter__': emitter,
    }
    if tool_name == 'edit_image':
        args['image_urls'] = ['/api/v1/files/source/content']
    result = asyncio.run(ns[tool_name](**args))
    assert json.loads(result)['status'] == 'success'
    persist.assert_awaited_once_with('chat-1', 'assistant-1', [{'type': 'image', **image}])
    emitter.assert_awaited_once_with({'type': 'chat:message:files', 'data': {'files': [{'type': 'image', **image}]}})
    output = result_output(runtime, tool_name, tool_result=result)
    assert {'type': 'input_image', 'image_url': IMAGE_URL} in output[-1]['output']
    assert not output[-1].get('files')


@pytest.mark.parametrize('tool_name', ['generate_image', 'edit_image'])
def test_native_image_result_is_visual_context_without_an_extra_display_file(runtime, tool_name):
    output = result_output(runtime, tool_name, [{'url': IMAGE_URL}, {'url': IMAGE_URL}])
    result = output[-1]
    assert [part for part in result['output'] if part['type'] == 'input_image'] == [
        {'type': 'input_image', 'image_url': IMAGE_URL}
    ]
    assert not result.get('files')


@pytest.mark.parametrize('chat_kind', ['saved', 'temporary'])
def test_history_replay_sends_image_pixels_without_mutating_saved_references(runtime, chat_kind):
    output = result_output(runtime)
    persisted = json.loads(json.dumps(output))
    history = [
        {'role': 'assistant', 'output': persisted, 'files': [{'type': 'image', 'url': IMAGE_URL}]},
        {'role': 'user', 'content': 'Make the sky blue'},
    ]
    if chat_kind == 'temporary':
        # The frontend sends structured assistant output for temporary chats.
        history[0].pop('files')
    messages = runtime.ns['process_messages_with_output'](history)
    user = SimpleNamespace(id='owner', role='user')
    payload = asyncio.run(runtime.ns['convert_url_images_to_base64']({'messages': messages}, user=user))
    image_parts = [
        part
        for msg in payload['messages']
        if isinstance(msg.get('content'), list)
        for part in msg['content']
        if part.get('type') == 'image_url'
    ]
    assert image_parts == [{'type': 'image_url', 'image_url': {'url': IMAGE_DATA}}]
    runtime.ns['get_image_base64_from_url'].assert_awaited_once_with(IMAGE_URL, user=user)
    assert persisted == output
    assert payload['messages'][-1] == {'role': 'user', 'content': 'Make the sky blue'}


def test_stateful_responses_followup_resolves_image_refs_in_tool_content(runtime):
    output = result_output(runtime)
    messages = runtime.ns['convert_output_to_messages'](output, raw=True)
    payload = asyncio.run(runtime.ns['convert_url_images_to_base64']({'model': 'vision', 'messages': messages}))
    responses = runtime.ns['convert_to_responses_payload'](payload)
    result = next(item for item in responses['input'] if item['type'] == 'function_call_output')
    assert {'type': 'input_image', 'image_url': IMAGE_DATA} in result['output']


@pytest.mark.parametrize('vision', [False, True])
def test_generated_image_history_keeps_later_attachments_on_their_user_turns(runtime, vision):
    history = [
        {'role': 'user', 'content': 'Draw a tree'},
        {'role': 'assistant', 'output': result_output(runtime)},
        {
            'role': 'user',
            'content': 'Compare this reference',
            'files': [{'type': 'image', 'url': '/api/v1/files/reference/content'}],
        },
        {'role': 'assistant', 'content': 'The shapes differ.'},
        {
            'role': 'user',
            'content': [{'type': 'text', 'text': 'Use this document'}],
            'files': [{'type': 'file', 'url': '/api/v1/files/document/content'}],
        },
    ]
    stored = {str(index): {**message, 'parentId': str(index - 1)} for index, message in enumerate(history)}
    ns = runtime.ns
    ns.update(
        is_saved_chat_id=lambda value: value == 'chat-1',
        Chats=SimpleNamespace(
            get_chat_by_id_and_user_id=AsyncMock(
                return_value=SimpleNamespace(chat={'history': {'messages': stored, 'currentId': '4'}})
            )
        ),
    )
    replay = ns['process_messages_with_output'](
        copy.deepcopy(history), include_tool_images=vision, include_file_context=True
    )
    payload = ns['normalize_messages_for_model']({'messages': replay})
    user_texts = [
        '\n'.join(part.get('text', '') for part in msg['content'])
        if isinstance(msg['content'], list)
        else msg['content']
        for msg in payload['messages']
        if msg['role'] == 'user'
    ]
    assert sum('<attached_files>' in text for text in user_texts) == 2
    for text in user_texts:
        if 'Compare this reference' in text:
            assert '/api/v1/files/reference/content' in text
            assert '/api/v1/files/document/content' not in text
        elif 'Use this document' in text:
            assert '/api/v1/files/document/content' in text
            assert '/api/v1/files/reference/content' not in text
        else:
            assert '<attached_files>' not in text
    assert all(set(msg) == {'role', 'content'} for msg in payload['messages'] if msg['role'] == 'user')


@pytest.mark.parametrize('stateful', [False, True])
@pytest.mark.parametrize('vision', [False, True])
def test_real_native_followup_dispatch_sends_pixels_only_to_vision_models(runtime, stateful, vision):
    output = result_output(runtime)
    capture = AsyncMock()
    ns = runtime.ns
    ns.update(
        output=output,
        form_data={'messages': [{'role': 'user', 'content': 'Draw a tree'}]},
        model_id='model',
        model={'info': {'meta': {'capabilities': {'vision': vision}}}},
        metadata={},
        ENABLE_RESPONSES_API_STATEFUL=stateful,
        last_response_id='previous',
        results=[{'tool_call_id': 'call-1'}],
        get_reasoning_format=lambda model: None,
        filter_functions=[],
        generate_chat_completion=capture,
        user=SimpleNamespace(id='owner'),
        request=object(),
    )
    asyncio.run(ns['dispatch_followup']())
    payload = capture.await_args.args[1]
    parts = [part for msg in payload['messages'] if isinstance(msg.get('content'), list) for part in msg['content']]
    visual = [part for part in parts if part.get('type') in {'image_url', 'input_image'}]
    if vision:
        expected = (
            {'type': 'input_image', 'image_url': IMAGE_DATA}
            if stateful
            else {'type': 'image_url', 'image_url': {'url': IMAGE_DATA}}
        )
        assert visual == [expected]
        ns['get_image_base64_from_url'].assert_awaited_once_with(IMAGE_URL, user=ns['user'])
    else:
        assert visual == []
        ns['get_image_base64_from_url'].assert_not_awaited()
    assert payload.get('previous_response_id') == ('previous' if stateful else None)
    assert {'type': 'input_image', 'image_url': IMAGE_URL} in output[-1]['output']


def test_nonvision_models_keep_tool_text_and_history_but_receive_no_images(runtime):
    output = result_output(runtime)
    messages = runtime.ns['process_messages_with_output'](
        [{'role': 'assistant', 'output': output}], include_tool_images=False
    )
    assert all(isinstance(message['content'], str) for message in messages)
    assert IMAGE_URL in messages[-1]['content']
    assert any(part['type'] == 'input_image' for part in output[-1]['output'])


def test_frontend_keeps_small_image_refs_for_temporary_chat_replay_but_omits_data_uris(runtime):
    output = result_output(runtime)
    output[-1]['output'].append({'type': 'input_image', 'image_url': IMAGE_DATA})
    ns = {'full_output': lambda: output, 'frontend_output': [], 'snapshot': False, 'output_start': 0}
    exec(compile(ast.Module(body=[runtime.frontend_loop], type_ignores=[]), 'frontend_output', 'exec'), ns)
    parts = ns['frontend_output'][-1]['output']
    assert {'type': 'input_image', 'image_url': IMAGE_URL} in parts
    assert {'type': 'input_image', 'image_url': IMAGE_DATA} not in parts
    assert {'type': 'input_image', 'image_url': IMAGE_DATA} in output[-1]['output']


@pytest.mark.parametrize('failure', [None, RuntimeError('file unavailable')])
def test_unreadable_local_images_do_not_break_provider_payloads(runtime, failure):
    loader = runtime.ns['get_image_base64_from_url']
    loader.return_value = None
    loader.side_effect = failure
    form = {
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'Image context'},
                    {'type': 'image_url', 'image_url': {'url': IMAGE_URL}},
                ],
            }
        ]
    }
    result = asyncio.run(runtime.ns['convert_url_images_to_base64'](form, user=SimpleNamespace(id='other')))
    assert result['messages'][0]['content'] == [{'type': 'text', 'text': 'Image context'}]


@pytest.mark.parametrize(
    'tool_name,tool_type,result',
    [
        ('generate_image', 'builtin', {'error': 'provider failed', 'images': [{'url': IMAGE_URL}]}),
        ('generate_image', 'builtin', {'status': 'success', 'images': [None, {}, {'url': 10}, {'url': ''}]}),
        ('generate_image', 'builtin', {'status': 'success', 'images': 'invalid'}),
        ('generate_image', 'external', {'status': 'success', 'images': [{'url': IMAGE_URL}]}),
        ('other_tool', 'builtin', {'status': 'success', 'images': [{'url': IMAGE_URL}]}),
    ],
)
def test_unrelated_or_invalid_results_do_not_inject_image_context(runtime, tool_name, tool_type, result):
    _, files, _ = asyncio.run(runtime.ns['process_tool_result'](None, tool_name, json.dumps(result), tool_type))
    assert files == []


def test_approved_image_tool_uses_the_same_context_and_authorized_image_loading(runtime):
    output = result_output(runtime)
    result = {
        'tool_call_id': 'call-1',
        'content': output[-1]['output'][0]['text'],
        'files': [{'type': 'image', 'url': IMAGE_URL, 'context_only': True}],
    }
    message = {'role': 'assistant', 'model': 'vision', 'output': [{**output[0], 'status': 'queued', 'approved': True}]}

    async def event_pair(metadata):
        return AsyncMock(), AsyncMock()

    ns = runtime.ns
    install_approval_store(ns, message)
    ns.update(
        is_saved_chat_id=lambda value: value == 'chat-1',
        get_event_emitter_and_caller=event_pair,
        execute_tool_call_for_output=AsyncMock(return_value=result),
        get_reasoning_format=lambda model: None,
    )
    form = {'messages': [{'role': 'user', 'content': 'Draw a tree'}]}
    assert (
        asyncio.run(
            ns['resume_tool_calls'](
                None,
                form,
                SimpleNamespace(id='owner'),
                {'id': 'vision'},
                {'chat_id': 'chat-1', 'assistant_message_id': 'assistant-1'},
                message,
            )
        )
        is False
    )
    stored = next(item for item in message['output'] if item['type'] == 'function_call_output')
    assert not stored.get('files')
    assert {'type': 'input_image', 'image_url': IMAGE_URL} in stored['output']
    # The actual next pipeline stage projects completed results before the final image read.
    form['messages'].extend(ns['process_messages_with_output']([message]))
    asyncio.run(ns['convert_url_images_to_base64'](form, user=SimpleNamespace(id='owner')))
    assert any(
        part.get('image_url', {}).get('url') == IMAGE_DATA
        for msg in form['messages']
        if isinstance(msg.get('content'), list)
        for part in msg['content']
    )


@pytest.mark.parametrize('url', [IMAGE_URL, IMAGE_URL + '?download=true', 'generated-image'])
def test_local_image_urls_resolve_the_file_id_through_the_authorized_reader(url):
    loader = AsyncMock(return_value=IMAGE_DATA)
    ns = {
        're': re,
        'get_image_base64_from_file_id': loader,
        'FILE_CONTENT_URL_PATTERN': re.compile(r'/api/v1/files/([^/?#]+)/content'),
    }
    load_functions(ns, 'utils/files.py', {'get_image_base64_from_url'})
    user = SimpleNamespace(id='owner', role='user')
    assert asyncio.run(ns['get_image_base64_from_url'](url, user=user)) == IMAGE_DATA
    loader.assert_awaited_once_with('generated-image', user=user)


def test_generated_image_context_does_not_read_another_users_file():
    ns = {
        're': re,
        'Files': SimpleNamespace(
            get_file_by_id=AsyncMock(return_value=SimpleNamespace(id='generated-image', user_id='owner'))
        ),
        'has_access_to_file': AsyncMock(return_value=False),
    }
    load_functions(ns, 'utils/files.py', {'get_image_base64_from_url', 'get_image_base64_from_file_id'})
    other = SimpleNamespace(id='other', role='user')
    assert asyncio.run(ns['get_image_base64_from_url'](IMAGE_URL, user=other)) is None
    ns['Files'].get_file_by_id.assert_awaited_once_with('generated-image')
    ns['has_access_to_file'].assert_awaited_once_with('generated-image', 'read', other)


@pytest.mark.parametrize('approved', [False, True])
def test_extracted_tool_image_keeps_visual_context_after_persistence(runtime, approved):
    image = {'type': 'image', 'url': IMAGE_DATA}
    store = AsyncMock(return_value=IMAGE_URL)
    runtime.ns.update(
        store_tool_result_image=store,
        request=object(),
        metadata={'chat_id': 'chat-1'},
        user=SimpleNamespace(id='owner'),
    )
    if not approved:
        output = result_output(runtime, tool_name='other_tool', tool_result=IMAGE_DATA)
        assert {'type': 'input_image', 'image_url': IMAGE_URL} in output[-1]['output']
        store.assert_awaited_once()
    else:
        # The approval drain uses the same production assembly helper after persistence.
        parts, display = runtime.ns['build_tool_result_parts'](
            {'content': '[image]', 'files': [{'type': 'image', 'url': IMAGE_URL}]}
        )
        assert {'type': 'input_image', 'image_url': IMAGE_URL} in parts


@pytest.mark.parametrize('chat_id', ['chat-1', 'local:temporary'])
def test_tool_image_storage_supports_owned_temporary_files_without_chat_link(runtime, chat_id):
    store = AsyncMock(return_value=IMAGE_URL)
    ns = runtime.ns
    ns.update(get_file_url_from_base64=store, is_saved_chat_id=lambda value: value == 'chat-1')
    load_functions(ns, 'utils/middleware.py', {'store_tool_result_image'})
    request = object()
    user = SimpleNamespace(id='owner')
    metadata = {'chat_id': chat_id, 'message_id': 'assistant-1', 'session_id': 's'}
    url = asyncio.run(ns['store_tool_result_image'](request, IMAGE_DATA, metadata, user))
    assert url == IMAGE_URL
    ns.update(request=request, metadata=metadata, user=user)
    expected_metadata = metadata if chat_id == 'chat-1' else {'session_id': 's'}
    store.assert_awaited_once_with(request, IMAGE_DATA, expected_metadata, user)
    output = result_output(runtime, tool_name='other_tool', tool_result=IMAGE_DATA)
    ns.update(full_output=lambda: output, frontend_output=[], snapshot=False, output_start=0)
    exec(compile(ast.Module(body=[runtime.frontend_loop], type_ignores=[]), 'frontend_output', 'exec'), ns)
    assert {'type': 'input_image', 'image_url': IMAGE_URL} in ns['frontend_output'][-1]['output']


@pytest.mark.parametrize('failure', [None, RuntimeError('storage unavailable')])
def test_failed_tool_image_storage_keeps_inline_context_without_breaking_chat(runtime, failure):
    ns = runtime.ns
    ns.update(
        get_file_url_from_base64=AsyncMock(return_value=None, side_effect=failure), is_saved_chat_id=lambda value: True
    )
    load_functions(ns, 'utils/middleware.py', {'store_tool_result_image'})
    assert (
        asyncio.run(
            ns['store_tool_result_image'](object(), IMAGE_DATA, {'chat_id': 'chat-1'}, SimpleNamespace(id='owner'))
        )
        == IMAGE_DATA
    )
