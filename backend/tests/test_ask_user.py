"""Exercise built-in user input and its real schema without importing the app."""

import copy
import inspect
import json
import logging
import re
from contextlib import asynccontextmanager
from functools import cache
from types import SimpleNamespace
from typing import Any, Callable, Literal, get_type_hints
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, status
from langchain_core.utils.function_calling import convert_to_openai_function
from pydantic import BaseModel, Field, create_model
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.utils import ask_user as ask_user_utils
from open_webui.utils.gemini_schema import compile_gemini_schema, validate_gemini_tool_arguments
from open_webui.utils.tool_calling import parse_arguments

from test_web_tool_citations import load_functions


@pytest.fixture(scope='module')
def runtime():
    namespace = {
        **vars(ask_user_utils),
        'Any': Any,
        'BaseModel': BaseModel,
        'Callable': Callable,
        'Field': Field,
        'cache': cache,
        'convert_pydantic_model_to_openai_function_spec': convert_to_openai_function,
        'copy': copy,
        'create_model': create_model,
        'get_type_hints': get_type_hints,
        'inspect': inspect,
        'log': logging.getLogger(__name__),
        're': re,
    }
    load_functions(namespace, 'tools/builtin.py', {'ask_user'})
    load_functions(
        namespace,
        'utils/tools.py',
        {
            'build_builtin_tool_spec_json',
            'clean_openai_tool_schema',
            'clean_properties',
            'convert_function_to_pydantic_model',
            'get_builtin_function_introspection',
            'get_builtin_tool_spec',
            'parse_description',
            'parse_docstring',
        },
    )
    load_functions(namespace, 'utils/middleware.py', {'prepare_tool_execution'})
    load_functions(namespace, 'utils/misc.py', {'get_provider_function'})
    ask = namespace['ask_user']
    spec = namespace['get_builtin_tool_spec'](ask)
    return SimpleNamespace(
        ask=ask,
        spec=spec,
        prepare=namespace['prepare_tool_execution'],
        get_provider_function=namespace['get_provider_function'],
    )


def make_call(arguments):
    return {'id': 'call_1', 'function': {'name': 'ask_user', 'arguments': json.dumps(arguments, indent=2)}}


def make_tools(runtime):
    return {'ask_user': {'tool_id': 'builtin:ask_user', 'type': 'builtin', 'spec': runtime.spec}}


@pytest.mark.parametrize('count', [2, 3, 4])
def test_choice_questions_accept_two_to_four_presets(count):
    options = ['Local', {'label': 'Shared'}, {'label': 'Both', 'description': 'Set up both'}, 'Later'][:count]
    expected = [
        {'label': 'Local', 'description': ''},
        {'label': 'Shared', 'description': ''},
        {'label': 'Both', 'description': 'Set up both'},
        {'label': 'Later', 'description': ''},
    ]
    normalized = ask_user_utils.normalize_ask_user_request(
        {'questions': [{'id': 'scope', 'question': 'Where will this run?', 'options': options}]}
    )

    assert normalized['questions'][0]['options'] == expected[:count]


@pytest.mark.asyncio
async def test_live_and_saved_prompts_share_normalization_and_preserve_answers(runtime):
    arguments = {
        'allow_other': False,
        'timeout_ms': 60_000,
        'questions': [
            {'id': 'name', 'question': 'What should it be called?'},
            {'id': 'summary', 'header': 'Summary', 'question': 'What should it do?', 'options': []},
            {
                'id': 'scope',
                'question': 'Where will this run?',
                'options': ['Local', {'label': 'Shared', 'description': 'Team workspace'}],
                'allow_other': False,
            },
        ],
    }
    answers = {
        'name': {'type': 'other', 'text': '  原始名称\nsecond line  '},
        'summary': {'type': 'other', 'text': 'An example'},
        'scope': {'type': 'option', 'option_index': 0, 'label': 'Local', 'description': ''},
    }
    received = []

    async def event_call(event):
        received.append(event)
        return {'status': 'answered', 'answers': answers}

    call = make_call(arguments)
    original = copy.deepcopy(call)
    output = []
    assert ask_user_utils.stage_ask_user_tool_calls([call], output, lambda prefix: prefix) == (True, None)
    expected = json.loads(output[0]['arguments'])
    assert output[0]['status'] == 'pending'
    assert output[0]['original_arguments'] == original['function']['arguments']
    assert runtime.get_provider_function(output[0]) == original['function']
    assert call == original
    assert expected['timeout_ms'] == 60_000
    assert expected['allow_other'] is True
    assert all(question['allow_other'] is True for question in expected['questions'])
    assert [question['header'] for question in expected['questions']] == ['Question 1', 'Summary', 'Question 3']
    assert [question['options'] for question in expected['questions'][:2]] == [[], []]

    result = await runtime.ask(**arguments, __event_call__=event_call)
    assert received == [{'type': 'request:user_input', 'data': expected}]
    assert json.loads(result) == {'status': 'answered', 'answers': answers}


@pytest.mark.asyncio
@pytest.mark.parametrize('answers', [{}, {'code': ' \r\n'}, {'code': {'type': 'other', 'text': '\t\n '}}])
async def test_live_ask_user_rejects_blank_answer_payloads(runtime, answers):
    callback = AsyncMock(return_value={'status': 'answered', 'answers': answers})

    result = json.loads(await runtime.ask(questions=[{'id': 'code', 'question': 'Code?'}], __event_call__=callback))

    assert result['status'] == 'error'
    assert 'non-blank' in result['error']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'answers',
    [
        {'code': '00123'},
        {'code': '00123', 'scope': 'Local', 'extra': 'Unasked'},
        {'code': '00123', 'unrelated': 'Local'},
        {'code': {'type': 'option', 'option_index': 0, 'label': '00123'}, 'scope': 'Local'},
        {'code': {'type': 'option', 'option_index': 0, 'label': '\n '}, 'scope': 'Local'},
        {'code': '00123', 'scope': {'type': 'option', 'option_index': 2, 'label': 'Shared'}},
        {'code': '00123', 'scope': {'type': 'other', 'text': 123}},
    ],
)
async def test_live_ask_user_requires_valid_answers_for_the_exact_question_ids(runtime, answers):
    callback = AsyncMock(return_value={'status': 'answered', 'answers': answers})
    questions = [
        {'id': 'code', 'question': 'Code?'},
        {'id': 'scope', 'question': 'Where will this run?', 'options': ['Local', 'Shared']},
    ]

    result = json.loads(await runtime.ask(questions=questions, __event_call__=callback))

    assert result['status'] == 'error'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('selection', 'expected_status'),
    [
        ({'option_index': 1, 'label': 'Shared', 'description': 'Team workspace'}, 'answered'),
        ({'option_index': 1, 'label': 'Shared'}, 'answered'),
        ({'option_index': 0, 'label': 'Shared'}, 'error'),
        ({'option_index': 1, 'label': 'Shared', 'description': 'Different workspace'}, 'error'),
    ],
)
async def test_selected_answer_must_match_normalized_preset(runtime, selection, expected_status):
    questions = [
        {
            'id': 'scope',
            'question': 'Where will this run?',
            'options': ['Local', {'label': ' Shared ', 'description': ' Team workspace '}],
        },
    ]
    answers = {'scope': {'type': 'option', **selection}}
    callback = AsyncMock(return_value={'status': 'answered', 'answers': answers})

    result = json.loads(await runtime.ask(questions=questions, __event_call__=callback))

    assert result['status'] == expected_status
    if expected_status == 'answered':
        assert result['answers'] == answers


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['00123', {'type': 'other', 'text': '00123'}])
async def test_live_ask_user_preserves_numbers_entered_as_text(runtime, answer):
    response = {'status': 'answered', 'answers': {'code': answer}}
    callback = AsyncMock(return_value=response)

    result = await runtime.ask(questions=[{'id': 'code', 'question': 'Code?'}], __event_call__=callback)

    assert json.loads(result) == response


@pytest.mark.asyncio
@pytest.mark.parametrize('structured', [False, True])
async def test_saved_ask_user_rejects_blank_answers_and_can_retry_through_resolve_route(structured):
    app = FastAPI()
    actor = SimpleNamespace(id='author')
    arguments = {
        'questions': [
            {'id': 'log', 'header': 'Log', 'question': 'Paste the command output.', 'options': []},
            {
                'id': 'scope',
                'header': 'Scope',
                'question': 'Where did it run?',
                'options': [{'label': 'Local', 'description': ''}, {'label': 'Shared', 'description': ''}],
            },
        ],
        'allow_other': True,
        'timeout_ms': 120_000,
    }
    original_message = {
        'user_id': actor.id,
        'done': True,
        'output': [
            {
                'type': 'function_call',
                'id': 'call_1',
                'name': 'ask_user',
                'is_ask_user': True,
                'status': 'pending',
                'arguments': json.dumps(arguments) if structured else json.dumps(json.dumps(arguments)),
            }
        ],
    }
    message = copy.deepcopy(original_message)
    drafts = []

    @asynccontextmanager
    async def edit_message(_chat_id, _message_id):
        draft = copy.deepcopy(message)
        try:
            yield draft
        finally:
            drafts.append(draft)
        message.update(draft)

    publish = AsyncMock()
    resume = AsyncMock(return_value={'task_id': 'resumed-task'})
    namespace = {
        **vars(ask_user_utils),
        'app': app,
        'Any': Any,
        'BaseModel': BaseModel,
        'AsyncSession': AsyncSession,
        'Depends': Depends,
        'HTTPException': HTTPException,
        'Literal': Literal,
        'Request': Request,
        'SQLAlchemyError': SQLAlchemyError,
        'parse_arguments': parse_arguments,
        'status': status,
        'uuid4': uuid4,
        'Chats': SimpleNamespace(
            edit_message_output=edit_message,
            get_accessible_chat_by_id=AsyncMock(return_value=SimpleNamespace(user_id=actor.id)),
            get_agent_execution=AsyncMock(return_value=None),
        ),
        'get_event_emitter': AsyncMock(return_value=publish),
        'get_verified_user': lambda: actor,
        'get_async_session': lambda: None,
        'build_tool_approval_resume_payload': AsyncMock(return_value={}),
        'chat_completion': resume,
    }
    load_functions(
        namespace,
        'utils/tool_approval.py',
        {'ResolveToolCallForm', 'pending_tool_calls', 'resolve_tool_call_output', 'tool_continuation'},
    )
    namespace['ResolveToolCallForm'].model_rebuild(_types_namespace=namespace)
    load_functions(namespace, 'main.py', {'resolve_chat_message_tool_call'})
    endpoint = '/api/v1/chats/chat/messages/reply/resolve'

    def submission(text):
        answer = {'type': 'other', 'text': text} if structured else text
        return {
            'call_id': 'call_1',
            'action': 'answer',
            'answers': {
                'log': answer,
                'scope': {'type': 'option', 'option_index': 0, 'label': 'Local', 'description': ''},
            },
        }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        valid = submission('  error 00123\n  second line  ')
        invalid_response = await client.post(endpoint, json=submission(' \t\n '))
        assert invalid_response.status_code == 400
        assert 'non-blank' in invalid_response.json()['detail']
        for answers in [{'log': 'Incomplete'}, {**valid['answers'], 'unrelated': 'Unasked'}]:
            id_error = await client.post(endpoint, json={**valid, 'answers': answers})
            assert id_error.status_code == 400
            assert 'question ID' in id_error.json()['detail']
        assert drafts == [original_message] * 3
        assert message == original_message
        publish.assert_not_awaited()
        resume.assert_not_awaited()

        valid_response = await client.post(endpoint, json=valid)
        assert valid_response.status_code == 200

    assert [item['status'] for item in message['output']] == ['completed', 'completed', 'in_progress']
    assert json.loads(message['output'][1]['output'][0]['text']) == {'status': 'answered', 'answers': valid['answers']}
    publish.assert_awaited_once()
    resume.assert_awaited_once()


@pytest.mark.parametrize('timeout', [True, 59_999, 240_001, '60000'])
def test_invalid_timeouts_keep_existing_default(timeout):
    result = ask_user_utils.normalize_ask_user_request(
        {'questions': [{'id': 'name', 'question': 'Name?', 'options': []}], 'timeout_ms': timeout}
    )

    assert result['timeout_ms'] == 120_000


def test_real_builtin_schema_exposes_question_shapes_and_gemini_keeps_strict_types(runtime):
    schema = runtime.spec['parameters']
    assert all(word in runtime.spec['description'] for word in ['logs', 'command output', 'errors', 'numbers'])
    assert 'Paste the command output.' in schema['properties']['questions']['description']
    assert set(schema['properties']) == {'questions', 'allow_other', 'timeout_ms'}
    question_schema = schema['properties']['questions']['items']
    assert set(question_schema['required']) == {'id', 'question'}
    assert {'id', 'header', 'question', 'options'} <= question_schema['properties'].keys()
    option_schema = question_schema['properties']['options']
    assert option_schema['examples']
    variants = {branch['type']: branch for branch in option_schema['items']['anyOf']}
    assert set(variants) == {'string', 'object'}
    assert set(variants['object']['required']) == {'label'}
    assert set(variants['object']['properties']) == {'label', 'description'}
    projected = compile_gemini_schema(schema).parameters
    projected_options = projected['properties']['questions']['items']['properties']['options']
    assert {branch['type'] for branch in projected_options['items']['anyOf']} == {'string', 'object'}

    valid = {
        'questions': [
            {'id': 'text', 'question': 'Name?'},
            {'id': 'empty', 'question': 'Details?', 'options': []},
            {'id': 'scope', 'question': 'Scope?', 'options': ['Local', {'label': 'Shared'}], 'allow_other': False},
        ],
        'allow_other': False,
    }
    assert validate_gemini_tool_arguments(schema, valid) is None
    for bad_option in [1, None, {'description': 'Missing label'}, {'label': 1}, {'label': 'OK', 'description': 4}]:
        invalid = copy.deepcopy(valid)
        invalid['questions'][2]['options'][0] = bad_option
        assert '#/questions/2/options/0' in validate_gemini_tool_arguments(schema, invalid)


@pytest.mark.parametrize('count', [1, 5])
@pytest.mark.parametrize('gemini', [False, True])
@pytest.mark.asyncio
async def test_invalid_choice_count_completes_saved_call_and_live_error_names_question(runtime, count, gemini, caplog):
    arguments = {'questions': [{'id': 'scope', 'question': 'Scope?', 'options': [{'label': 'Preset'}] * count}]}
    call = make_call(arguments)
    original_arguments = call['function']['arguments']
    metadata = {'tools': make_tools(runtime), '_tool_schema_compatibility': 'gemini' if gemini else 'default'}
    _, _, preflight_error = runtime.prepare({}, metadata, call)
    assert preflight_error is None
    output = [{'type': 'function_call', 'id': 'call_1', 'call_id': 'call_1', 'status': 'in_progress'}]
    staged, error = ask_user_utils.stage_ask_user_tool_calls([call], output, lambda prefix: prefix)
    assert staged and error.startswith('Error: ')
    assert all(text in error for text in ['scope', '2-4 preset options', 'Omit options'])
    assert [item['status'] for item in output] == ['completed', 'completed']
    assert output[0]['original_arguments'] == original_arguments
    assert output[1]['call_id'] == 'call_1'
    assert output[1]['output'] == [{'type': 'input_text', 'text': error}]

    async def unexpected_event_call(_event):
        pytest.fail('An invalid question must never open a prompt')

    assert json.loads(await runtime.ask(**arguments, __event_call__=unexpected_event_call)) == {
        'status': 'error',
        'error': error.removeprefix('Error: '),
    }
    assert 'scope' not in caplog.text
