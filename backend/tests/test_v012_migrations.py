"""Run the complete checked-in Alembic graph in isolated subprocess databases."""

from pathlib import Path
import os
import subprocess
import sys

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
HEAD = 'a120d13e0003'
FORK = 'f9a4b2d6c805'
OFFICIAL = 'b8f2c6d91a04'


def migrate(database: Path, target: str, *, success=True):
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT / 'backend'),
        DATA_DIR=str(database.parent / 'data'),
        STATIC_DIR=str(database.parent / 'static'),
        DATABASE_URL=f'sqlite:///{database.as_posix()}',
        DATABASE_SCHEMA='',
        WEBUI_SECRET_KEY='synthetic-migration-test-secret',
        ENABLE_DB_MIGRATIONS='False',
        ENABLE_ORJSON='False',
        USE_SLIM_DOCKER='True',
    )
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--migrate', str(database), target],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    (database.parent / f'{target}-migration.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, 'Expected migration failure'
    return result


def engine(database):
    return sa.create_engine(f'sqlite:///{database.as_posix()}')


def legacy_snapshot():
    return {
        'name': 'Legacy Package',
        'description': 'test',
        'content': 'Run {baseDir}/run.sh\n',
        'meta': {
            'future': {'nested': True},
            'openclaw': {
                'frontmatter': {'license': 'MIT'},
                'files': {
                    'run.sh': {'content': 'echo ok\n', 'encoding': 'utf-8'},
                    'image.bin': {'content': 'AP8=', 'encoding': 'base64'},
                },
                'source': {'type': 'zip', 'future': 'kept'},
                'config': {'enabled': True},
                'env': {'DEMO': 'value'},
            },
        },
    }


def seed(database, *, collision=False):
    import uuid

    snapshot = legacy_snapshot()
    with engine(database).begin() as connection:
        metadata = sa.MetaData()
        skill = sa.Table('skill', metadata, autoload_with=connection)
        values = {'id': 'legacy', 'user_id': 'owner', 'is_active': True, 'created_at': 1, 'updated_at': 2, **snapshot}
        if 'data' in skill.c:
            snapshot['data'] = {'files': [{'path': 'SKILL.md', 'content': snapshot['content']}]}
            if collision:
                snapshot['data']['files'].append({'path': 'run.sh', 'content': 'do not overwrite'})
            values['data'] = snapshot['data']
            values['version_id'] = 'immutable-old'
            history = sa.Table('skill_history', metadata, autoload_with=connection)
            connection.execute(
                history.insert().values(
                    id='immutable-old',
                    skill_id='legacy',
                    parent_id=None,
                    snapshot=snapshot,
                    user_id='owner',
                    created_at=2,
                    commit_message='old',
                )
            )
        connection.execute(skill.insert().values(**values))
    return snapshot


def verify_head(database):
    with engine(database).connect() as connection:
        assert connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all() == [HEAD]
        inspector = sa.inspect(connection)
        assert 'source_scope' in {column['name'] for column in inspector.get_columns('chat_context_checkpoint')}
        assert 'source_scope' in {column['name'] for column in inspector.get_columns('chat_tool_result_context')}
        assert {'skill_history', 'model_history', 'tool_history', 'function_history'} <= set(
            inspector.get_table_names()
        )
        # Compile the real migrated schema for PostgreSQL; this does not execute on a PG service.
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateTable, CreateIndex

        metadata = sa.MetaData()
        metadata.reflect(bind=connection)
        for table in metadata.sorted_tables:
            assert str(CreateTable(table).compile(dialect=postgresql.dialect()))
            for index in table.indexes:
                assert str(CreateIndex(index).compile(dialect=postgresql.dialect()))


def test_fresh_sqlite_complete_migration_graph(tmp_path):
    database = tmp_path / 'fresh.db'
    migrate(database, HEAD)
    verify_head(database)


@pytest.mark.parametrize('baseline', [FORK, OFFICIAL])
def test_fork_and_official_sqlite_upgrade_preserves_skill_bytes_and_old_snapshot(tmp_path, baseline):
    import base64

    database = tmp_path / f'{baseline}.db'
    migrate(database, baseline)
    original = seed(database)
    migrate(database, HEAD)
    verify_head(database)
    with engine(database).connect() as connection:
        metadata = sa.MetaData()
        skill = sa.Table('skill', metadata, autoload_with=connection)
        history = sa.Table('skill_history', metadata, autoload_with=connection)
        current = connection.execute(sa.select(skill)).mappings().one()
        files = {file['path']: file for file in current['data']['files']}
        assert files['run.sh']['content'] == 'echo ok\n'
        assert base64.b64decode(files['image.bin']['content']) == b'\x00\xff'
        assert files['SKILL.md']['content'].endswith(original['content'])
        assert current['meta']['future'] == {'nested': True}
        assert current['meta']['openclaw']['source']['future'] == 'kept'
        assert 'files' not in current['meta']['openclaw'] and 'frontmatter' not in current['meta']['openclaw']
        entries = connection.execute(sa.select(history)).mappings().all()
        assert len(entries) == 2
        previous = next(entry for entry in entries if entry['id'] != current['version_id'])
        assert previous['snapshot']['content'] == original['content']
        assert previous['snapshot']['meta'] == original['meta']
        if baseline == OFFICIAL:
            assert previous['snapshot'] == original
    migrate(database, HEAD)
    with engine(database).connect() as connection:
        assert connection.exec_driver_sql('SELECT count(*) FROM skill_history').scalar_one() == 2


def test_invalid_compat_migration_rolls_back_data_and_preserves_immutable_history(tmp_path):
    database = tmp_path / 'invalid.db'
    migrate(database, OFFICIAL)
    original = seed(database, collision=True)
    result = migrate(database, HEAD, success=False)
    assert 'legacy' in result.stderr and 'run.sh' in result.stderr
    with engine(database).connect() as connection:
        metadata = sa.MetaData()
        skill = sa.Table('skill', metadata, autoload_with=connection)
        history = sa.Table('skill_history', metadata, autoload_with=connection)
        current = connection.execute(sa.select(skill)).mappings().one()
        assert current['version_id'] == 'immutable-old'
        assert current['data'] == original['data'] and current['meta'] == original['meta']
        assert connection.execute(sa.select(history.c.snapshot)).scalar_one() == original
        assert HEAD not in connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalars().all()


def migration_cli():
    from alembic import command
    from alembic.config import Config

    database, target = sys.argv[2:]
    Path(database).parent.mkdir(parents=True, exist_ok=True)
    Path(os.environ['DATA_DIR']).mkdir(exist_ok=True)
    Path(os.environ['STATIC_DIR']).mkdir(exist_ok=True)
    config = Config(str(ROOT / 'backend/open_webui/alembic.ini'))
    config.set_main_option('script_location', str(ROOT / 'backend/open_webui/migrations'))
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD]
    assert script.get_revision('a120d13e0001').down_revision == (FORK, OFFICIAL)
    command.upgrade(config, target)


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--migrate':
    migration_cli()
