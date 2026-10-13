"""Exact reductions for server-registered builtin schemas and skill pages.

These projections never rewrite definitions or summarize skill instructions.
The caller supplies token measurement to keep this helper free of app imports.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Callable

EXACT_TOOL_KINDS = frozenset({'tool_schema', 'skill_page'})


def resize_exact_tool_result(text: str, kind: str, budget: int, estimate: Callable[[str], int]) -> str:
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    try:
        page = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError('Exact builtin output must be valid JSON; retry using saved outputs') from error
    if not isinstance(page, dict):
        raise ValueError('Exact builtin output must be an object; retry using saved outputs')
    encoded = encode(page)
    if estimate(encoded) <= budget:
        return encoded
    if kind == 'tool_schema':
        definitions = page.get('tools')
        if not isinstance(definitions, list) or not all(isinstance(item, dict) for item in definitions):
            raise ValueError('Tool definitions are invalid; retry using saved outputs')
        # Ranking is significant: never skip the first required definition just to fit a later one.
        for count in range(len(definitions) - 1, 0, -1):
            candidate = {**page, 'tools': definitions[:count], 'omitted_count': len(definitions) - count}
            rendered = encode(candidate)
            if estimate(rendered) <= budget:
                return rendered
        raise ValueError(
            'A complete required tool definition cannot fit; search for one exact tool name or increase the tool/context budget'
        )
    if kind == 'skill_page':
        content, offset = page.get('content'), page.get('offset')
        if not isinstance(content, str) or type(offset) is not int or offset < 0:
            raise ValueError(
                'Exact skill metadata cannot fit or is invalid; request a smaller file page or increase the tool/context budget'
            )

        # Every continuation uses an absolute character offset into the same version/file.
        def candidate(count):
            return encode({**deepcopy(page), 'content': content[:count], 'next_offset': offset + count})

        if not content or estimate(candidate(1)) > budget:
            raise ValueError(
                'A skill page and its continuation metadata cannot fit; reduce the batch or increase the tool/context budget'
            )
        low, high, best = 1, len(content) - 1, 1
        while low <= high:
            middle = (low + high) // 2
            if estimate(candidate(middle)) <= budget:
                best, low = middle, middle + 1
            else:
                high = middle - 1
        return candidate(best)
    raise ValueError('Unknown exact builtin result kind')
