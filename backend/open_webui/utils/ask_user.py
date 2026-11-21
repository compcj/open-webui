from collections.abc import Callable
from typing import Annotated

from pydantic import Field
from typing_extensions import NotRequired, TypedDict

from open_webui.utils.json_codec import JSONCodec


ASK_USER_NAME = 'ask_user'


class AskUserOption(TypedDict):
    label: str
    description: NotRequired[str]


class AskUserQuestion(TypedDict):
    id: str
    header: NotRequired[str]
    question: str
    allow_other: NotRequired[Annotated[bool, Field(description='Legacy field. Typed answers are always available.')]]
    # Check option counts at runtime so errors can name the question in every provider mode.
    options: NotRequired[
        Annotated[
            list[str | AskUserOption],
            Field(
                description='Omit options or use [] for a text question. For a choice question, give 2-4 presets, '
                'with the recommendation first. Use labels like "Local" or objects like {"label":"Shared"} '
                'with an optional description.',
                examples=[[], ['Local', {'label': 'Shared'}, {'label': 'Both', 'description': 'Set up both'}]],
            ),
        ]
    ]


def get_ask_user_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], str | None]:
    ask_user_calls = [
        tool_call for tool_call in tool_calls if tool_call.get('function', {}).get('name') == ASK_USER_NAME
    ]
    if not ask_user_calls:
        return [], None
    if len(tool_calls) != 1:
        return (
            ask_user_calls,
            'Error: ask_user must be the only tool call, so it did not run. Call ask_user on its own.',
        )
    if len(ask_user_calls) != 1:
        return ask_user_calls, 'Error: only one ask_user call is allowed per turn.'
    return ask_user_calls, None


def normalize_ask_user_request(arguments: dict) -> dict:
    questions = arguments.get('questions')
    if not isinstance(questions, list):
        raise ValueError('ask_user questions must be an array of question objects.')
    if not 1 <= len(questions) <= 3:
        raise ValueError('ask_user requires 1-3 questions.')

    normalized_questions = []
    seen_ids = set()
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise ValueError('Each question must be an object.')

        question_id = str(question.get('id') or '').strip()[:64]
        if not question_id:
            raise ValueError('Each question requires a non-empty id.')
        if question_id in seen_ids:
            raise ValueError(f'Duplicate question id: {question_id}')
        seen_ids.add(question_id)

        options = question.get('options', [])
        if not isinstance(options, list) or (options and not 2 <= len(options) <= 4):
            raise ValueError(
                f'Invalid options for question "{question_id}". Omit options (or use []) for a text question, '
                'or provide 2-4 preset options for a choice question.'
            )

        normalized_options = []
        for option in options:
            if isinstance(option, str):
                option = {'label': option}
            if not isinstance(option, dict):
                raise ValueError(f'Options for question "{question_id}" must be strings or objects with a label.')
            label = option.get('label')
            description = option.get('description', '')
            if not isinstance(label, str) or not label.strip() or not isinstance(description, str):
                raise ValueError(
                    f'Options for question "{question_id}" require a non-empty label and optional text description.'
                )
            normalized_options.append({'label': label.strip()[:80], 'description': description.strip()[:240]})

        question_text = str(question.get('question') or '').strip()[:500]
        if not question_text:
            raise ValueError('Each question requires question text.')

        normalized_questions.append(
            {
                'id': question_id,
                'header': str(question.get('header') or '').strip()[:48] or f'Question {index + 1}',
                'question': question_text,
                'options': normalized_options,
                'allow_other': True,
            }
        )

    timeout_ms = arguments.get('timeout_ms', 120_000)
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 60_000 <= timeout_ms <= 240_000:
        timeout_ms = 120_000

    return {
        'questions': normalized_questions,
        'allow_other': True,
        'timeout_ms': timeout_ms,
    }


def validate_ask_user_answers(questions: list[dict], answers: object) -> str | None:
    """Check submitted answers without changing the original payload."""
    if not isinstance(answers, dict) or answers.keys() != {question['id'] for question in questions}:
        return 'ask_user requires one non-blank answer for each question ID.'

    error = 'ask_user requires answers with non-blank text or selected options.'
    for question in questions:
        answer = answers[question['id']]
        if isinstance(answer, dict):
            answer_type = answer.get('type')
            if answer_type == 'other':
                answer = answer.get('text')
            elif answer_type == 'option':
                index = answer.get('option_index')
                if type(index) is not int or not 0 <= index < len(question['options']):
                    return error
                preset = question['options'][index]
                if (
                    answer.get('label') != preset['label']
                    or answer.get('description', preset['description']) != preset['description']
                ):
                    return error
                continue
            else:
                return error
        if not isinstance(answer, str) or not answer.strip():
            return error
    return None


def stage_ask_user_tool_calls(
    tool_calls: list[dict],
    output: list[dict],
    make_output_id: Callable[[str], str],
) -> tuple[bool, str | None]:
    ask_user_calls, error = get_ask_user_tool_calls(tool_calls)
    if not ask_user_calls:
        return False, None

    for tool_call in ask_user_calls:
        call_id = tool_call.get('id') or make_output_id('fc')
        function = tool_call.get('function', {})
        arguments = function.get('arguments', '{}')
        original_arguments = function.get('original_arguments', arguments)

        if not error:
            try:
                parsed_arguments = JSONCodec.loads(arguments or '{}')
                if not isinstance(parsed_arguments, dict):
                    raise ValueError('ask_user arguments must be an object.')
                arguments = JSONCodec.dumps(normalize_ask_user_request(parsed_arguments))
            except (JSONCodec.JSONDecodeError, TypeError, ValueError) as exc:
                error = f'Error: {exc}'

        item = {
            'type': 'function_call',
            'id': call_id or make_output_id('fc'),
            'call_id': call_id,
            'name': ASK_USER_NAME,
            'arguments': arguments,
            'status': 'completed' if error else 'pending',
        }

        existing_item = next(
            (
                existing
                for existing in output
                if existing.get('type') == 'function_call'
                and (
                    existing.get('call_id') == call_id
                    or existing.get('id') == tool_call.get('id')
                    or (
                        not existing.get('call_id')
                        and existing.get('name') == ASK_USER_NAME
                        and existing.get('status') not in {'rejected', 'failed'}
                    )
                )
            ),
            None,
        )
        if arguments != original_arguments:
            item['original_arguments'] = (existing_item or {}).get('original_arguments', original_arguments)
        if existing_item:
            existing_item.update(item)
        else:
            output.append(item)

        # Every invalid call needs its own result, or the UI waits on it forever.
        if error:
            output.append(
                {
                    'type': 'function_call_output',
                    'id': make_output_id('fco'),
                    'call_id': call_id,
                    'output': [{'type': 'input_text', 'text': error}],
                    'status': 'completed',
                }
            )

    return True, error
