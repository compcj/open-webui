"""Pure helpers for explicit OpenAI-compatible parameter handling."""

from copy import deepcopy
import json


_STRING_PARAMETERS = frozenset(
    {
        'prompt_cache_key',
        'prompt_cache_retention',
        'session_id',
        'user',
        'safety_identifier',
        'service_tier',
    }
)


def parse_custom_parameters(custom_params: dict, json_codec, *, preserve_openai_strings: bool = True) -> dict:
    """Decode custom JSON without retyping known string fields or caller-owned values."""
    result = deepcopy(custom_params)
    for key, value in result.items():
        # Legacy reasoning choices are exact literals, including surrounding JSON quotes.
        if not isinstance(value, str) or key == 'reasoning_effort':
            continue
        try:
            parsed = json_codec.loads(value)
        except json_codec.JSONDecodeError:
            continue
        if preserve_openai_strings and key in _STRING_PARAMETERS and not isinstance(parsed, str):
            continue
        result[key] = parsed
    return result


def expand_extra_body(payload: dict, enabled: bool = False) -> dict:
    """Optionally unwrap SDK-style extras; explicit top-level values win wholesale."""
    if not enabled:
        return payload
    if 'extra_body' not in payload:
        return deepcopy(payload)

    extra_body = payload['extra_body']
    if not isinstance(extra_body, dict):
        raise ValueError('extra_body must be a JSON object when compatibility mode is enabled.')

    expanded = {**extra_body, **payload}
    expanded.pop('extra_body', None)
    return deepcopy(expanded)


def project_private_tool_context(payload: dict, private_context: list[dict]) -> dict:
    """Send server-injected context without impersonating a model's tool response.

    Thinking providers can require original reasoning for every assistant call.
    These request-local pairs never came from a provider. Only IDs registered in
    server state qualify; real calls and the frozen internal snapshot stay intact.
    """
    private_ids = {
        call['id'] for message in private_context for call in message.get('tool_calls') or [] if call.get('id')
    }
    if not private_ids:
        return payload

    messages = payload.get('messages') or []
    results = {message.get('tool_call_id'): message for message in messages if message.get('role') == 'tool'}
    paired_private_ids = private_ids.intersection(results)
    calls = {
        call['id']
        for message in messages
        if message.get('role') == 'assistant'
        for call in message.get('tool_calls') or []
        if call.get('id') in paired_private_ids
    }
    if not calls:
        return payload

    projected = []
    for original in messages:
        message = deepcopy(original)
        if message.get('role') == 'assistant' and any(
            call.get('id') in calls for call in message.get('tool_calls') or []
        ):
            # A filter may combine synthetic and real calls into one batch. Put
            # context before the real assistant turn, never between calls/results.
            for call in message['tool_calls']:
                if call.get('id') not in calls:
                    continue
                source = json.dumps(call['function'], ensure_ascii=False)
                header = f'Automatically loaded context (source: {source}):\n'
                content = deepcopy(results[call['id']].get('content', ''))
                if isinstance(content, list):
                    content = [
                        {'type': 'text', 'text': header},
                        *[{**part, 'type': 'text'} if part.get('type') == 'input_text' else part for part in content],
                    ]
                else:
                    content = header + (content or '')
                projected.append({'role': 'system', 'content': content})
            remaining = [call for call in message['tool_calls'] if call.get('id') not in calls]
            if remaining:
                message['tool_calls'] = remaining
            else:
                message.pop('tool_calls')
                if not message.get('content') and not any(
                    message.get(key) for key in ('reasoning_content', 'reasoning_details', 'extra_content')
                ):
                    # llama.cpp adds an empty reasoning field to synthetic pairs;
                    # it is not a real assistant response or a Continue prefill.
                    continue
        elif message.get('role') == 'tool' and message.get('tool_call_id') in calls:
            continue
        projected.append(message)

    from open_webui.utils.misc import merge_system_messages

    # Preserve the existing single-leading-system contract and cache blocks.
    # Frozen source text yields the same prefix on every continuation.
    return {**payload, 'messages': merge_system_messages(projected)}
