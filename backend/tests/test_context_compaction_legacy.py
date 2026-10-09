"""Legacy characterization and mode isolation without app import side effects."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any

import pytest

FILE = Path(__file__).resolve().parents[1] / 'open_webui/utils/context_compaction.py'


def load(names, **extra):
    ns = {'Any': Any, **extra}
    nodes = [
        n
        for n in ast.parse(FILE.read_text(encoding='utf-8')).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(FILE), 'exec'), ns)
    return ns


def test_legacy_threshold_and_retention_rules_remain():
    ns = load(
        {
            '_parse_positive_int',
            '_resolve_token_threshold',
            '_clamp_retention_percentage',
            '_find_compaction_boundary',
            '_apply_latest_summary_checkpoint',
        }
    )
    threshold = ns['_resolve_token_threshold']
    assert threshold(80000, 60000, {'params': {'compact_token_threshold': 90000}}) == 60000
    assert threshold(80000, 60000, {'params': {'compact_token_threshold': 12000}}) == 12000
    assert threshold(80000, 60000, {'params': {'compact_token_threshold': -1}}) == 60000
    messages = [{'role': role, 'content': str(i)} for i, role in enumerate(['user', 'assistant'] * 3)]
    assert ns['_find_compaction_boundary'](messages, 40) == 4
    assert ns['_find_compaction_boundary'](messages, 50) == 2
    messages[2]['contextSummary'] = 'old checkpoint'
    retained, summary = ns['_apply_latest_summary_checkpoint'](messages)
    assert retained == messages[2:] and summary == 'old checkpoint'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode,enabled,expected',
    [(None, False, False), (None, True, True), ('v2', True, False), ('off', True, False), ('legacy', False, True)],
)
async def test_legacy_enable_fallback_and_explicit_mode(mode, enabled, expected):
    config = SimpleNamespace(
        get_many=AsyncMock(
            return_value={'chat.context_compaction.mode': mode, 'chat.context_compaction.enable': enabled}
        )
    )
    ns = load({'_load_config', '_parse_positive_int', '_clamp_retention_percentage'}, Config=config)
    assert (await ns['_load_config']())['enable'] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['off', 'v2'])
async def test_nonlegacy_mode_leaves_raw_history_with_legacy_summary_untouched(mode):
    messages = [
        {'id': 'old', 'role': 'user', 'content': 'full old text'},
        {'id': 'new', 'role': 'user', 'content': 'next', 'contextSummary': 'legacy summary'},
    ]
    ns = load({'compact_messages_for_request'}, _load_config=AsyncMock(return_value={'enable': True}))
    req = SimpleNamespace(state=SimpleNamespace(context_compaction={'mode': mode}))
    result = await ns['compact_messages_for_request'](req, None, messages, {}, 'model', {})
    assert result == (messages, None, False)
    assert result[0] is messages


@pytest.mark.asyncio
async def test_legacy_threshold_is_capped_by_resolved_model_context():
    capture = []

    def check(messages, system, summary, threshold):
        capture.append(threshold)
        return False

    settings = SimpleNamespace(default_context_window=262144)
    ns = load(
        {
            'compact_messages_for_request',
            '_apply_latest_summary_checkpoint',
            '_resolve_token_threshold',
            '_parse_positive_int',
        },
        _load_config=AsyncMock(return_value={'enable': True, 'token_threshold': 80000, 'token_cap': 60000}),
        resolve_context_window=AsyncMock(return_value=32768),
        _exceeds_token_threshold=check,
    )
    req = SimpleNamespace(state=SimpleNamespace(context_compaction={'mode': 'legacy', 'settings': settings}))
    messages = [{'role': 'user', 'content': 'hello'}]
    result = await ns['compact_messages_for_request'](req, None, messages, {'params': {}}, 'preset', {})
    assert capture == [32768]
    assert result == (messages, None, False)
