"""Provider cache annotations that belong to an exact content-block boundary."""

from copy import deepcopy


def cache_fields(block: dict) -> dict:
    return {key: deepcopy(block[key]) for key in ('cache_control', 'prompt_cache_breakpoint') if key in block}


def has_cache_fields(content) -> bool:
    return isinstance(content, list) and any(isinstance(part, dict) and cache_fields(part) for part in content)


def responses_text_parts(content, text_type: str) -> list[dict]:
    if isinstance(content, str):
        return [{'type': text_type, 'text': content}]
    return [
        {'type': text_type, 'text': part.get('text', ''), **cache_fields(part)}
        for part in content
        if isinstance(part, dict) and part.get('type') in ('text', 'input_text', 'output_text')
    ]
