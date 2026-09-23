"""Exercise the real ComfyUI adapter, router and native tools without app/database imports."""

import asyncio
import importlib.util
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

from test_image_generation_engines import BACKEND, image_router
from test_web_tool_citations import load_functions


PROMPT_ID = 'synthetic-task'
PRIVATE_VALUE = 'private-value-must-not-appear'
WORKFLOW = {
    '6': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'original'}},
    '9': {'class_type': 'SaveImage', 'inputs': {}},
}
HISTORY = {
    PROMPT_ID: {
        'status': {'status_str': 'success', 'completed': True, 'messages': []},
        'outputs': {'9': {'images': [{'filename': 'output.png', 'subfolder': '', 'type': 'output'}]}},
    }
}


def message(event, **data):
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({'type': event, 'data': data}))


class Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                SimpleNamespace(real_url=f'https://comfy.example.test/?token={PRIVATE_VALUE}'),
                (),
                status=self.status,
                message=f'Bad Request: {PRIVATE_VALUE}',
            )

    async def json(self):
        return self.payload


class WebSocket:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for event in self.events:
            yield event


@pytest.fixture
def transport(image_router, monkeypatch):
    state = SimpleNamespace(
        connection_error=None,
        submission_error=None,
        submission_status=200,
        history_status=200,
        history=HISTORY,
        events=[message('executing', node=None, prompt_id=PROMPT_ID)],
        submissions=[],
    )

    class Session:
        def ws_connect(self, *args, **kwargs):
            if state.connection_error:
                raise state.connection_error
            return WebSocket(state.events)

        def post(self, url, **kwargs):
            state.submissions.append(kwargs['json'])
            if state.submission_error:
                raise state.submission_error
            return Response({'prompt_id': PROMPT_ID, 'number': 0, 'node_errors': {}}, state.submission_status)

        def get(self, *args, **kwargs):
            return Response(state.history, state.history_status)

    monkeypatch.setattr(image_router.comfyui, 'get_session', AsyncMock(return_value=Session()))
    return state


def call_adapter(comfyui, editing=False):
    options = {
        'workflow': comfyui.ComfyUIWorkflow(
            workflow=json.dumps(WORKFLOW), nodes=[{'type': 'prompt', 'key': 'text', 'node_ids': ['6']}]
        ),
        'prompt': PRIVATE_VALUE,
        'width': 512,
        'height': 512,
    }
    if editing:
        payload = comfyui.ComfyUIEditImageForm(image=['source.png'], **options)
        handler = comfyui.comfyui_edit_image
    else:
        payload = comfyui.ComfyUICreateImageForm(**options)
        handler = comfyui.comfyui_create_image
    return handler('synthetic-model', payload, 'synthetic-client', 'https://comfy.example.test', PRIVATE_VALUE)


@pytest.mark.parametrize('editing', [False, True])
@pytest.mark.parametrize('handshake', [False, True])
def test_connection_failure_reaches_caller_without_leaking_request_data(
    image_router, transport, caplog, editing, handshake
):
    transport.connection_error = (
        aiohttp.WSServerHandshakeError(
            SimpleNamespace(real_url=f'https://comfy.example.test/?token={PRIVATE_VALUE}'),
            (),
            status=400,
            message=PRIVATE_VALUE,
        )
        if handshake
        else aiohttp.ClientConnectionError(PRIVATE_VALUE)
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match='ComfyUI.*WebSocket connection') as error:
            asyncio.run(call_adapter(image_router.comfyui, editing))
    assert PRIVATE_VALUE not in str(error.value)
    assert PRIVATE_VALUE not in caplog.text
    assert error.value.__cause__ is transport.connection_error
    if handshake:
        assert 'HTTP 400' in str(error.value)
    assert transport.submissions == []


@pytest.mark.parametrize('editing', [False, True])
def test_rejected_workflow_preserves_stage_and_http_status(image_router, transport, caplog, editing):
    transport.submission_status = 400
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match='ComfyUI.*workflow submission.*HTTP 400') as error:
            asyncio.run(call_adapter(image_router.comfyui, editing))
    assert PRIVATE_VALUE not in str(error.value)
    assert PRIVATE_VALUE not in caplog.text
    assert len(transport.submissions) == 1


@pytest.mark.parametrize('editing', [False, True])
def test_submission_timeout_is_not_reported_as_a_prompt_error_or_retried(image_router, transport, editing):
    transport.submission_error = TimeoutError(PRIVATE_VALUE)
    with pytest.raises(RuntimeError, match='ComfyUI.*workflow submission.*timed out'):
        asyncio.run(call_adapter(image_router.comfyui, editing))
    assert len(transport.submissions) == 1


@pytest.mark.parametrize('event', ['execution_error', 'execution_interrupted'])
def test_execution_failure_is_reported_before_reading_partial_outputs(image_router, transport, event):
    transport.events = [
        message(event, prompt_id='unrelated-task', exception_message=PRIVATE_VALUE),
        message(event, prompt_id=PROMPT_ID, exception_message=PRIVATE_VALUE, node_id='6'),
    ]
    with pytest.raises(RuntimeError, match='ComfyUI.*execution') as error:
        asyncio.run(call_adapter(image_router.comfyui))
    assert PRIVATE_VALUE not in str(error.value)
    assert len(transport.submissions) == 1


def test_missing_history_is_a_result_retrieval_error(image_router, transport):
    transport.history = {}
    with pytest.raises(RuntimeError, match='ComfyUI.*history.*unavailable'):
        asyncio.run(call_adapter(image_router.comfyui))
    assert len(transport.submissions) == 1


def test_history_http_error_preserves_its_stage(image_router, transport):
    transport.history_status = 503
    with pytest.raises(RuntimeError, match='ComfyUI.*history retrieval.*HTTP 503'):
        asyncio.run(call_adapter(image_router.comfyui))


@pytest.mark.parametrize('event_data', [None, 'custom node progress', ['preview']])
def test_unrelated_websocket_events_remain_ignored(image_router, transport, event_data):
    transport.events.insert(
        0, SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({'type': 'custom_event', 'data': event_data}))
    )
    result = asyncio.run(call_adapter(image_router.comfyui))
    assert len(result['data']) == 1


def test_connection_can_recover_with_the_same_prompt(image_router, transport):
    transport.connection_error = aiohttp.ClientConnectionError(PRIVATE_VALUE)
    with pytest.raises(RuntimeError, match='WebSocket connection'):
        asyncio.run(call_adapter(image_router.comfyui))
    transport.connection_error = None
    result = asyncio.run(call_adapter(image_router.comfyui))
    assert len(result['data']) == 1
    assert len(transport.submissions) == 1
    assert transport.submissions[0]['prompt']['6']['inputs']['text'] == PRIVATE_VALUE


@pytest.mark.parametrize('disconnected', [False, True])
@pytest.mark.parametrize('editing', [False, True])
def test_successful_history_still_returns_images(image_router, transport, disconnected, editing):
    if disconnected:
        transport.events = []
    else:
        transport.events.insert(0, message('execution_error', prompt_id='unrelated-task'))
    result = asyncio.run(call_adapter(image_router.comfyui, editing))
    assert result == {'data': [{'url': 'https://comfy.example.test/view?filename=output.png&subfolder=&type=output'}]}
    assert len(transport.submissions) == 1
    assert transport.submissions[0]['prompt']['6']['inputs']['text'] == PRIVATE_VALUE


@pytest.mark.parametrize('editing', [False, True])
def test_builtin_tool_retains_comfyui_error_through_router(image_router, transport, monkeypatch, caplog, editing):
    router = image_router.module
    constants_spec = importlib.util.spec_from_file_location('constants_under_test', BACKEND / 'constants.py')
    constants = importlib.util.module_from_spec(constants_spec)
    constants_spec.loader.exec_module(constants)
    monkeypatch.setattr(router, 'ERROR_MESSAGES', constants.ERROR_MESSAGES)
    monkeypatch.setattr(router, 'comfyui_create_image', image_router.comfyui.comfyui_create_image)
    monkeypatch.setattr(router, 'comfyui_edit_image', image_router.comfyui.comfyui_edit_image)
    monkeypatch.setattr(router, 'comfyui_upload_image', AsyncMock(return_value={'name': 'source.png'}))
    values = image_router.state.values
    for prefix in ['image_generation', 'images.edit']:
        values[f'{prefix}.engine'] = 'comfyui'
        values[f'{prefix}.comfyui.base_url'] = 'https://comfy.example.test'
        values[f'{prefix}.comfyui.workflow'] = json.dumps(WORKFLOW)
        values[f'{prefix}.comfyui.nodes'] = [{'type': 'prompt', 'key': 'text', 'node_ids': ['6']}]
    values['images.edit.enable'] = True
    transport.connection_error = aiohttp.ClientConnectionError(PRIVATE_VALUE)
    namespace = {
        'JSONCodec': json,
        'image_generations': router.image_generations,
        'image_edits': router.image_edits,
        'CreateImageForm': router.CreateImageForm,
        'EditImageForm': router.EditImageForm,
        'log': logging.getLogger(__name__),
    }
    tool = 'edit_image' if editing else 'generate_image'
    load_functions(namespace, 'tools/builtin.py', {tool})
    args = {'prompt': 'A blue sky', '__request__': SimpleNamespace()}
    if editing:
        args['image_urls'] = ['data:image/png;base64,c3ludGhldGlj']
    result = json.loads(asyncio.run(namespace[tool](**args)))
    assert 'ComfyUI' in result['error']
    assert 'WebSocket connection' in result['error']
    assert 'Something went wrong' not in result['error']
    assert PRIVATE_VALUE not in result['error']
    assert PRIVATE_VALUE not in caplog.text
    assert transport.submissions == []
