"""Cache usage normalization and read-only analytics without application imports."""

import ast
import copy
from numbers import Number
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, registry

from test_web_tool_citations import load_functions


@pytest.fixture
def usage():
    namespace = {'Number': Number}
    tree = load_functions(
        namespace,
        'utils/response.py',
        {
            'usage_counter',
            'normalize_usage',
            '_is_numeric_usage_value',
            '_merge_numeric_usage_map',
            'merge_usage',
            'update_usage_snapshot',
            'convert_responses_usage_to_chat_completions',
        },
    )
    constants = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id.startswith('USAGE_') for target in node.targets)
    ]
    exec(compile(ast.Module(body=constants, type_ignores=[]), 'usage_constants', 'exec'), namespace)
    return SimpleNamespace(**namespace)


def completion(cache, *, prompt=1000, output=10):
    return {'prompt_tokens': prompt, 'completion_tokens': output, **cache}


@pytest.mark.parametrize('counter', [0, 100, '100'])
def test_deepseek_cache_hits_have_canonical_counter(usage, counter):
    result = usage.normalize_usage(completion({'prompt_cache_hit_tokens': counter, 'prompt_cache_miss_tokens': 900}))

    assert result['cache_read_input_tokens'] == int(counter)
    assert result['input_tokens'] == 1000
    assert result['prompt_tokens'] == 1000
    assert result['prompt_cache_hit_tokens'] == int(counter)


@pytest.mark.parametrize('counter', [None, True, False, -1, 'unknown', float('inf'), {}, []])
def test_invalid_deepseek_cache_hits_stay_unknown(usage, counter):
    result = usage.normalize_usage(completion({'prompt_cache_hit_tokens': counter}))

    assert 'cache_read_input_tokens' not in result
    assert 'prompt_cache_hit_tokens' not in result


@pytest.mark.parametrize(
    ('cache', 'expected'),
    [
        ({'cache_read_input_tokens': 0, 'prompt_cache_hit_tokens': 100}, 0),
        ({'cache_read_input_tokens': 100, 'prompt_cache_hit_tokens': 100}, 100),
        ({'input_tokens_details': {'cached_tokens': 0}, 'prompt_cache_hit_tokens': 100}, 0),
        ({'input_tokens_details': {'cached_tokens': 100}, 'prompt_cache_hit_tokens': 200}, 100),
        ({'prompt_tokens_details': {'cached_tokens': 100}, 'prompt_cache_hit_tokens': 200}, 100),
        ({'cache_read_input_tokens': None, 'prompt_cache_hit_tokens': 100}, 100),
        ({'input_tokens_details': {'cached_tokens': None}, 'prompt_cache_hit_tokens': 100}, 100),
        ({'input_tokens_details': None, 'prompt_cache_hit_tokens': 100}, 100),
    ],
)
def test_cache_aliases_are_fallbacks_not_additional_tokens(usage, cache, expected):
    original = copy.deepcopy(cache)
    result = usage.normalize_usage(cache)

    assert result['cache_read_input_tokens'] == expected
    assert usage.normalize_usage(result) == result
    assert cache == original


def test_deepseek_rounds_accumulate_cache_hits_and_keep_latest_prompt_size(usage):
    first = completion({'prompt_cache_hit_tokens': 100})
    second = completion({'prompt_cache_hit_tokens': 200}, prompt=2000, output=20)
    result = usage.merge_usage(first, second)

    assert result['cache_read_input_tokens'] == 300
    assert result['input_tokens'] == 3000
    assert result['output_tokens'] == 30
    assert result['total_tokens'] == 3030
    assert result['prompt_tokens'] == 2000
    assert result['completion_tokens'] == 20
    assert result['prompt_cache_hit_tokens'] == 200
    assert result['incomplete'] is False

    result = usage.merge_usage(result, completion({'prompt_cache_hit_tokens': 50}, prompt=2500))
    assert result['cache_read_input_tokens'] == 350
    assert result['prompt_tokens'] == 2500


def test_mixed_cache_formats_accumulate_once_per_round(usage):
    result = None
    for cache in (
        {'prompt_cache_hit_tokens': 100},
        {'input_tokens_details': {'cached_tokens': 200}},
        {'prompt_tokens_details': {'cached_tokens': 300}},
        {
            'cache_read_input_tokens': 400,
            'prompt_cache_hit_tokens': 400,
            'prompt_tokens_details': {'cached_tokens': 400},
        },
    ):
        result = usage.merge_usage(result, completion(cache))

    assert result['cache_read_input_tokens'] == 1000
    assert usage.normalize_usage(result)['cache_read_input_tokens'] == 1000


def test_cache_snapshots_replace_counts_within_a_call(usage):
    snapshot = usage.update_usage_snapshot(None, completion({'prompt_cache_hit_tokens': 100}))
    snapshot = usage.update_usage_snapshot(snapshot, completion({'prompt_cache_hit_tokens': 100}))
    snapshot = usage.update_usage_snapshot(snapshot, {'prompt_cache_hit_tokens': 200})
    snapshot = usage.update_usage_snapshot(snapshot, {'completion_tokens': 20})

    assert snapshot['cache_read_input_tokens'] == 200
    assert snapshot['input_tokens'] == 1000
    assert snapshot['output_tokens'] == 20
    assert snapshot['total_tokens'] == 1020
    assert usage.merge_usage(snapshot, completion({'prompt_cache_hit_tokens': 50}))['cache_read_input_tokens'] == 250


def test_measured_zero_and_missing_cache_counts_remain_distinct(usage):
    unknown = usage.merge_usage(completion({}), completion({}))
    measured = usage.merge_usage(completion({}), completion({'prompt_cache_hit_tokens': 0}))

    assert 'cache_read_input_tokens' not in unknown
    assert measured['cache_read_input_tokens'] == 0


def test_responses_cache_details_keep_other_measurements(usage):
    raw = {
        'input_tokens': 1000,
        'output_tokens': 10,
        'input_tokens_details': {'cached_tokens': 100, 'audio_tokens': 12},
    }
    result = usage.convert_responses_usage_to_chat_completions(raw)

    assert result['cache_read_input_tokens'] == 100
    assert result['input_tokens_details'] == result['prompt_tokens_details'] == raw['input_tokens_details']
    assert result['prompt_tokens'] == 1000


@pytest.fixture
def cache_stats():
    metadata = sa.MetaData()
    chat = sa.Table(
        'chat',
        metadata,
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('title', sa.Text),
        sa.Column('user_id', sa.Text),
        sa.Column('folder_id', sa.Text),
        sa.Column('meta', sa.JSON),
    )
    message = sa.Table(
        'chat_message',
        metadata,
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('chat_id', sa.Text),
        sa.Column('role', sa.Text),
        sa.Column('created_at', sa.Integer),
        sa.Column('model_id', sa.Text),
        sa.Column('usage', sa.JSON),
        sa.Column('meta', sa.JSON),
        sa.Column('error', sa.JSON),
    )
    user = sa.Table('user', metadata, sa.Column('id', sa.Text, primary_key=True), sa.Column('name', sa.Text))
    folder = sa.Table('folder', metadata, sa.Column('id', sa.Text, primary_key=True), sa.Column('name', sa.Text))
    namespace = {
        name: getattr(sa, name)
        for name in ('ARRAY', 'Integer', 'Text', 'case', 'cast', 'distinct', 'func', 'literal', 'or_', 'select')
    }
    models = registry()
    for name, table in (('Chat', chat), ('ChatMessage', message), ('User', user), ('Folder', folder)):
        model = type(name, (), {})
        models.map_imperatively(model, table)
        namespace[name] = model
    load_functions(namespace, 'models/chat_messages.py', {'_token_columns'})
    load_functions(
        namespace, 'models/analytics_sessions.py', {'json_text', 'message_filters', 'chat_filters', 'session_query'}
    )
    engine = sa.create_engine('sqlite:///:memory:')
    metadata.create_all(engine)

    def query(usages):
        with Session(engine) as db:
            db.execute(chat.insert().values(id='synthetic-chat', title='Synthetic', meta={}))
            for index, item in enumerate(usages):
                db.execute(
                    message.insert().values(
                        id=f'assistant-{index}',
                        chat_id='synthetic-chat',
                        role='assistant',
                        created_at=index + 1,
                        model_id='synthetic-model',
                        usage=completion(item),
                        meta={},
                        error=None,
                    )
                )
            db.execute(
                message.insert().values(
                    id='user',
                    chat_id='synthetic-chat',
                    role='user',
                    created_at=1,
                    usage={'prompt_cache_hit_tokens': 999999},
                    meta={},
                    error=None,
                )
            )
            statement, _ = namespace['session_query'](db)
            return dict(db.execute(statement).mappings().one())

    yield query
    engine.dispose()
    models.dispose()


def test_analytics_reads_legacy_deepseek_cache_hits(cache_stats):
    result = cache_stats([{'prompt_cache_hit_tokens': 100}, {'prompt_cache_hit_tokens': 200}])

    assert result['cache_read'] == 300
    assert result['cache_read_responses'] == 2


@pytest.mark.parametrize(
    ('usages', 'expected', 'measured'),
    [
        ([{}, {'prompt_cache_hit_tokens': None}], None, 0),
        ([{}, {'prompt_cache_hit_tokens': None}, {'prompt_cache_hit_tokens': 0}], 0, 1),
    ],
)
def test_analytics_distinguishes_unknown_from_zero_cache_hits(cache_stats, usages, expected, measured):
    result = cache_stats(usages)

    assert result['cache_read'] == expected
    assert result['cache_read_responses'] == measured


def test_analytics_prefers_canonical_cache_counters_to_legacy_alias(cache_stats):
    result = cache_stats(
        [
            {'cache_read_input_tokens': 0, 'prompt_cache_hit_tokens': 999},
            {'cache_read_input_tokens': 20, 'prompt_cache_hit_tokens': 999},
            {'input_tokens_details': {'cached_tokens': 30}, 'prompt_cache_hit_tokens': 999},
            {'prompt_tokens_details': {'cached_tokens': 40}, 'prompt_cache_hit_tokens': 999},
        ]
    )

    assert result['cache_read'] == 90
    assert result['cache_read_responses'] == 4


def test_analytics_reads_new_totals_and_only_surviving_legacy_values(usage, cache_stats):
    current = usage.merge_usage(
        completion({'prompt_cache_hit_tokens': 100}), completion({'prompt_cache_hit_tokens': 200})
    )
    legacy = {'input_tokens': 3000, 'prompt_tokens': 2000, 'prompt_cache_hit_tokens': 200}
    result = cache_stats([current, legacy])

    # A legacy round's overwritten hit count cannot be reconstructed from input totals.
    assert result['cache_read'] == 500
    assert result['cache_read_responses'] == 2
