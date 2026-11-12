"""Pure helpers for explicit OpenAI-compatible parameter handling."""

from copy import deepcopy


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
