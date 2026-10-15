"""Exact reductions for server-registered builtin schemas and skill pages.

These projections never rewrite definitions or summarize skill instructions.
The caller supplies token measurement to keep this helper free of app imports.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Callable

EXACT_TOOL_KINDS = frozenset({'tool_schema', 'skill_page', 'skill_search'})


def resize_exact_tool_result(text: str, kind: str, budget: int, estimate: Callable[[str], int]) -> str:
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    try:
        page = json.loads(text)
    except (TypeError, ValueError) as error:
        raise ValueError('Exact builtin output must be valid JSON; retry using saved outputs') from error
    if not isinstance(page, dict):
        raise ValueError('Exact builtin output must be an object; retry using saved outputs')
    # A small JSON envelope is not a meaningful exact result when every source
    # match was omitted. Check before the fit shortcut; explicit tool errors and
    # genuine zero-match searches remain valid bounded outputs.
    if (
        kind == 'skill_search'
        and type(page.get('total_matches')) is int
        and page['total_matches'] > 0
        and not (page.get('matches') or page.get('omitted_matches'))
    ):
        raise ValueError(
            'A complete skill search match and its metadata cannot fit; narrow the pattern, specify path, '
            'or increase the tool/context budget'
        )
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
    if kind == 'skill_search':
        matches, locations = page.get('matches'), page.get('omitted_matches', [])
        total = page.get('total_matches')
        if (
            not isinstance(matches, list)
            or not all(isinstance(item, dict) for item in matches)
            or not isinstance(locations, list)
            or not all(isinstance(item, dict) for item in locations)
            or type(total) is not int
            or total < 0
        ):
            raise ValueError('Skill search metadata is invalid; retry using saved outputs')
        # Excerpts and their complete location lists are indivisible. Keep ranked
        # entries and provenance intact; compact location entries can also be omitted.
        minimum = 1 if matches else 0
        for count in range(len(matches), minimum - 1, -1):
            retained = sum(len(item.get('locations') or [item]) for item in matches[:count])
            minimum_locations = 1 if total and not matches else 0
            for location_count in range(len(locations), minimum_locations - 1, -1):
                candidate = {
                    **page,
                    'matches': matches[:count],
                    'omitted_matches': locations[:location_count],
                    'truncated': True,
                    'omitted_count': page.get('omitted_count', 0) + len(matches) - count,
                    'omitted_match_count': max(0, total - retained),
                    'omitted_matches_truncated': bool(
                        page.get('omitted_matches_truncated') or count < len(matches) or location_count < len(locations)
                    ),
                    'notice': 'Exact matches omitted to fit the budget; narrow the pattern or specify path.',
                }
                rendered = encode(candidate)
                if estimate(rendered) <= budget:
                    return rendered
        raise ValueError(
            'A complete skill search match and its metadata cannot fit; narrow the pattern, specify path, '
            'or increase the tool/context budget'
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
