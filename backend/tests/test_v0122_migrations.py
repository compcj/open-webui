"""Real SQLite/PostgreSQL upgrade acceptance for the v0.12.2 merge.

TEST_POSTGRES_URL opts in using an administrator connection to a disposable test
server. Each case creates and drops its own database; the named database is never
migrated. No optional vector extension is needed by the checked-in Alembic graph.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
import uuid

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
HEAD = 'a122d15e0001'
BASELINES = [None, 'f9a4b2d6c805', 'a120d13e0003', 'b8f2c6d91a04', '0bd252b206fb']
RAW_CHAT = json.dumps(
    {
        'history': {
            'messages': {
                'reply': {
                    'id': 'reply',
                    'content': '原文\r\n',
                    'sources': [{'source': {'id': 'https://example.invalid/source'}, 'document': ['frozen']}],
                    'output': [
                        {
                            'type': 'function_call_output',
                            'call_id': 'call',
                            'output': 'raw\r\n',
                            'citation_context': 'Frozen citation [1]',
                            'citation_sources': [{'id': 'frozen'}],
                        }
                    ],
                }
            }
        }
    },
    ensure_ascii=False,
    indent=2,
)


def migration_config():
    config = Config(str(ROOT / 'backend/open_webui/alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend/open_webui/migrations'))
    return config


def test_v0122_merge_has_one_head_and_expected_parents():
    script = ScriptDirectory.from_config(migration_config())
    assert script.get_heads() == [HEAD]
    assert script.get_revision(HEAD).down_revision == ('a120d13e0003', '0bd252b206fb')
    # The merge itself must never perform business-data writes in either direction.
    revision = script.get_revision(HEAD).module
    assert revision.upgrade() is None
    assert revision.downgrade() is None


@pytest.fixture(params=['sqlite', 'postgresql'])
def database(request, tmp_path):
    if request.param == 'sqlite':
        url = sa.engine.make_url(f'sqlite:///{(tmp_path / "upgrade.db").as_posix()}')
        yield url
        return
    configured = os.environ.get('TEST_POSTGRES_URL')
    if not configured:
        pytest.skip('Set TEST_POSTGRES_URL to run real disposable PostgreSQL upgrades')
    admin = sa.create_engine(configured, isolation_level='AUTOCOMMIT')
    name = f'oui_v0122_migration_{uuid.uuid4().hex}'
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE {name}')
        yield sa.engine.make_url(configured).set(database=name)
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        admin.dispose()


def migrate(url, target, directory, *, success=True):
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / 'backend'),
        DATA_DIR=str(directory / 'data'),
        STATIC_DIR=str(directory / 'static'),
        DATABASE_URL=url.render_as_string(hide_password=False),
        DATABASE_SCHEMA='',
        WEBUI_SECRET_KEY='synthetic-v0122-migration-secret',
        ENABLE_DB_MIGRATIONS='false',
        ENABLE_ORJSON='false',
        USE_SLIM_DOCKER='True',
        PYTHONUTF8='1',
        PYTHONDONTWRITEBYTECODE='1',
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--migrate', target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=180,
    )
    log = directory / f'{target}-{len(list(directory.glob("*.log")))}.log'
    log.write_text(result.stdout + result.stderr, encoding='utf-8')
    if success:
        assert result.returncode == 0, f'{log}\n{result.stdout}{result.stderr}'
    else:
        assert result.returncode != 0, 'Expected migration failure'
    return result


def table(connection, name):
    return sa.Table(name, sa.MetaData(), autoload_with=connection)


def stored_rows(connection, name):
    """Read JSON as stored text too, detecting byte changes to historical snapshots."""
    target = table(connection, name)
    columns = [
        sa.cast(column, sa.Text).label(column.name) if isinstance(column.type, sa.JSON) else column
        for column in target.c
    ]
    return [dict(row) for row in connection.execute(sa.select(*columns).order_by(target.c.id)).mappings()]


def seed(connection):
    user = table(connection, 'user')
    auth = table(connection, 'auth')
    for identity in ['existing', 'missing', 'null-email']:
        connection.execute(
            user.insert().values(
                id=identity,
                name=identity,
                email=None if identity == 'null-email' else f'{identity}@example.invalid',
                role='user',
                created_at=1,
                updated_at=2,
            )
        )
    credentials = dict(
        id='existing', email='credential@example.invalid', password='synthetic-hash-unchanged', active=False
    )
    if 'mfa' in auth.c:
        credentials.update(mfa={'synthetic': 'unchanged'}, session_stamp='session-unchanged')
    connection.execute(auth.insert().values(**credentials))
    connection.execute(
        table(connection, 'chat')
        .insert()
        .values(
            id='chat',
            user_id='existing',
            title='Synthetic frozen chat',
            chat=RAW_CHAT,
            created_at=1,
            updated_at=2,
        )
    )
    skill = table(connection, 'skill')
    current = dict(name='Native', description='test', content='Preserve bytes\r\n', meta={'future': {'kept': True}})
    if 'data' in skill.c:
        current['data'] = {'files': [{'path': 'SKILL.md', 'content': current['content']}]}
        current['version_id'] = 'immutable-old'
        snapshot = {key: value for key, value in current.items() if key != 'version_id'}
        snapshot['meta'] = {
            'openclaw': {
                'frontmatter': {'future': 'retained'},
                'files': {'raw.bin': {'encoding': 'base64', 'content': 'AP8='}},
            }
        }
        connection.execute(
            table(connection, 'skill_history')
            .insert()
            .values(
                id='immutable-old',
                skill_id='skill',
                user_id='existing',
                parent_id=None,
                snapshot=snapshot,
                created_at=1,
                commit_message='Immutable legacy snapshot',
            )
        )
    connection.execute(
        skill.insert().values(
            id='skill',
            user_id='existing',
            is_active=True,
            created_at=1,
            updated_at=2,
            **current,
        )
    )
    names = set(sa.inspect(connection).get_table_names())
    if 'chat_context_checkpoint' in names:
        checkpoint = table(connection, 'chat_context_checkpoint')
        for version in [1, 2]:
            values = dict(
                id=f'checkpoint-{version}',
                chat_id='chat',
                model_id='model',
                branch_anchor_id='reply',
                version=version,
                source_refs=[{'message_id': 'reply', 'output_ids': ['output'], 'future': version}],
                source_fingerprint=f'checkpoint-fingerprint-{version}',
                covered_message_keys=['reply'],
                covered_output_ids=['output'],
                summary=f'Checkpoint {version}\r\n原文',
                created_at=version,
            )
            if 'source_scope' in checkpoint.c:
                values['source_scope'] = None if version == 1 else 'saved_history_v1'
            connection.execute(checkpoint.insert().values(**values))
    if 'chat_tool_result_selection' in names:
        source = dict(
            chat_id='chat',
            message_id='reply',
            output_id='output',
            call_id='call',
            batch_id='batch',
            name='synthetic',
            kind='text',
            source_hash='hash',
            source_fingerprint='fingerprint',
        )
        connection.execute(
            table(connection, 'chat_tool_result')
            .insert()
            .values(
                id='raw-result',
                text='Raw tool bytes\r\n原文',
                created_at=1,
                **source,
            )
        )
        context = table(connection, 'chat_tool_result_context')
        values = dict(
            id='context',
            result_id='raw-result',
            chat_id='chat',
            model_id='model',
            branch_id='reply',
            task_hash='task',
            policy_hash='policy',
            budget=100,
            text='Adopted summary',
            method='summary',
            original_tokens=100,
            processed_tokens=5,
            created_at=1,
        )
        if 'source_scope' in context.c:
            values['source_scope'] = 'saved_history_v1'
        connection.execute(context.insert().values(**values))
        connection.execute(
            table(connection, 'chat_tool_result_selection')
            .insert()
            .values(
                id='selection',
                result_id='raw-result',
                model_id='model',
                branch_anchor_id='reply',
                state='prepared',
                context_id='context',
                method='summary',
                allocation=10,
                original_tokens=100,
                processed_tokens=5,
                decision_id='decision',
                recorded_at=2,
                batch_report={'accounting_complete': True},
                **source,
            )
        )
    return {
        name: stored_rows(connection, name)
        for name in [
            'auth',
            'chat',
            'skill_history',
            'chat_context_checkpoint',
            'chat_tool_result',
            'chat_tool_result_context',
            'chat_tool_result_selection',
        ]
        if name in names
    }


def verify_preserved(connection, original):
    for name, before in original.items():
        after = stored_rows(connection, name)
        if name == 'auth':
            after = [row for row in after if row['id'] == 'existing']
        # New provenance columns may be added, but preexisting values may never change.
        assert [{key: row[key] for key in before[0]} for row in after] == before, name
        if name == 'chat_context_checkpoint':
            assert len(after) == 2
            if 'source_scope' not in before[0]:
                assert all(row['source_scope'] is None for row in after)
            else:
                assert {row['source_scope'] for row in after} == {None, 'saved_history_v1'}
    assert connection.execute(sa.select(table(connection, 'chat').c.chat)).scalar_one() == RAW_CHAT


def verify_auth(connection):
    auth = {row['id']: dict(row) for row in connection.execute(sa.select(table(connection, 'auth'))).mappings()}
    assert set(auth) == {'existing', 'missing', 'null-email'}
    assert auth['existing']['email'] == 'credential@example.invalid'
    assert auth['existing']['password'] == 'synthetic-hash-unchanged'
    assert auth['existing']['active'] is False
    for identity in ['missing', 'null-email']:
        assert auth[identity]['password'] == ''
        assert auth[identity]['active'] is True
        assert auth[identity]['email'] == (None if identity == 'null-email' else 'missing@example.invalid')


def verify_apps_constraints(connection):
    inspector = sa.inspect(connection)
    assert {'app_id', 'user_id'} <= {column['name'] for column in inspector.get_columns('channel_member')}
    assert 'app_id' in {column['name'] for column in inspector.get_columns('message')}
    assert 'ix_message_app' in {index['name'] for index in inspector.get_indexes('message')}
    app = table(connection, 'app')
    channel = table(connection, 'channel')
    member = table(connection, 'channel_member')
    connection.execute(channel.insert().values(id='constraint-channel', name='Synthetic'))
    values = dict(id='app-one', slug='synthetic-app', name='Synthetic', data={}, created_at=1, updated_at=1)
    connection.execute(app.insert().values(**values))
    with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
        connection.execute(app.insert().values(**{**values, 'id': 'duplicate-app'}))
    base = dict(channel_id='constraint-channel', joined_at=1)
    connection.execute(member.insert().values(id='app-member', user_id=None, app_id='app-one', **base))
    connection.execute(member.insert().values(id='user-member', user_id='existing', app_id=None, **base))
    for identity, user_id, app_id in [
        ('neither', None, None),
        ('both', 'existing', 'app-one'),
        ('duplicate-app-member', None, 'app-one'),
    ]:
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(member.insert().values(id=identity, user_id=user_id, app_id=app_id, **base))
    assert connection.execute(sa.select(sa.func.count()).select_from(member)).scalar_one() == 2


def snapshot_database(connection):
    return {
        name: stored_rows(connection, name)
        for name in sa.inspect(connection).get_table_names()
        if 'id' in table(connection, name).c
    }


@pytest.mark.parametrize(
    'baseline', BASELINES, ids=['empty', 'early-fork', 'fork-v0120', 'official-v0120', 'official-v0122']
)
def test_real_upgrade_preserves_data_constraints_and_is_idempotent(database, tmp_path, baseline):
    engine = sa.create_engine(database)
    try:
        if baseline is not None:
            # Exercise the official auth backfill while preparing an official v0.12.2 baseline.
            migrate(database, 'c92da83b017f' if baseline == '0bd252b206fb' else baseline, tmp_path)
            with engine.begin() as connection:
                original = seed(connection)
            if baseline == '0bd252b206fb':
                migrate(database, baseline, tmp_path)
                with engine.connect() as connection:
                    verify_preserved(connection, original)
                    verify_auth(connection)
        migrate(database, HEAD, tmp_path)
        with engine.begin() as connection:
            assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all() == [HEAD]
            inspector = sa.inspect(connection)
            for name in ['chat_context_checkpoint', 'chat_tool_result_context']:
                assert 'source_scope' in {column['name'] for column in inspector.get_columns(name)}
            if baseline is not None:
                verify_preserved(connection, original)
                verify_auth(connection)
            else:
                assert (
                    connection.execute(sa.select(sa.func.count()).select_from(table(connection, 'auth'))).scalar_one()
                    == 0
                )
            for name in [
                'chat_context_checkpoint',
                'chat_tool_result',
                'chat_tool_result_context',
                'chat_tool_result_selection',
            ]:
                if baseline is None or name not in original:
                    assert (
                        connection.execute(sa.select(sa.func.count()).select_from(table(connection, name))).scalar_one()
                        == 0
                    )
            verify_apps_constraints(connection)
            before_repeat = snapshot_database(connection)
            version = (
                connection.exec_driver_sql('select version()').scalar_one()
                if database.get_backend_name() == 'postgresql'
                else connection.exec_driver_sql('select sqlite_version()').scalar_one()
            )
            print(
                f'ENGINE={version} BASELINE={baseline or "empty"} HEAD={HEAD} TABLES={len(inspector.get_table_names())}'
            )
        migrate(database, HEAD, tmp_path)
        with engine.connect() as connection:
            assert snapshot_database(connection) == before_repeat
            assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all() == [HEAD]
    finally:
        engine.dispose()


def test_real_canonical_collision_rolls_back_data_and_keeps_old_snapshot(database, tmp_path):
    # Isolate canonical conversion from already completed official Apps/auth work.
    # SQLite does not roll back the complete multi-revision DDL graph atomically.
    migrate(database, '0bd252b206fb', tmp_path)
    engine = sa.create_engine(database)
    try:
        with engine.begin() as connection:
            seed(connection)
            skill = table(connection, 'skill')
            connection.execute(
                skill.update().values(
                    meta={'openclaw': {'files': {'SKILL.md': {'content': 'Conflicting bytes', 'encoding': 'utf-8'}}}}
                )
            )
            before = {name: stored_rows(connection, name) for name in ['skill', 'skill_history', 'auth', 'chat']}
        failure = migrate(database, HEAD, tmp_path, success=False)
        assert 'Skill skill' in failure.stderr and 'SKILL.md' in failure.stderr
        with engine.connect() as connection:
            for name, expected in before.items():
                assert stored_rows(connection, name) == expected
            assert HEAD not in connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all()
    finally:
        engine.dispose()


def migration_cli():
    from alembic import command

    for key in ['DATA_DIR', 'STATIC_DIR']:
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)
    command.upgrade(migration_config(), sys.argv[2])


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--migrate':
    migration_cli()
