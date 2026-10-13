"""Exercise real skill persistence on an isolated SQLite database with lightweight ACL stubs."""

import ast
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, String, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from open_webui.utils.skill_compat import canonical_snapshot
from open_webui.utils.skill_files import SkillFile, SkillFileOperation, apply_operations, validate_files

ROOT = Path(__file__).resolve().parents[1]


def load_persistence():
    base = declarative_base()

    class Grant(base):
        __tablename__ = 'access_grant'
        id = Column(String, primary_key=True)
        resource_type = Column(String)
        resource_id = Column(String)

    class GrantModel(BaseModel):
        model_config = ConfigDict(from_attributes=True)

    @asynccontextmanager
    async def context(db):
        yield db

    async def replace(*args):
        return []

    common = {
        'Base': base,
        'get_async_db_context': context,
        'AccessGrant': Grant,
        'AccessGrantModel': GrantModel,
        'AccessGrants': SimpleNamespace(replace_access_grants=replace),
        'Groups': SimpleNamespace(),
        'User': Grant,
        'UserModel': GrantModel,
        'UserResponse': GrantModel,
        'Users': SimpleNamespace(),
        'json_text_variants': lambda value: [value],
        'canonical_snapshot': canonical_snapshot,
        'SkillFile': SkillFile,
        'SkillFileOperation': SkillFileOperation,
        'apply_operations': apply_operations,
        'validate_files': validate_files,
    }

    def load(path, extra):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        tree.body = [
            node
            for node in tree.body
            if not isinstance(node, ast.ImportFrom) or not (node.module or '').startswith('open_webui')
        ]
        namespace = {'__name__': 'skill_persistence_under_test', **common, **extra}
        exec(compile(tree, str(path), 'exec'), namespace)
        return namespace

    history = load(ROOT / 'open_webui/models/skill_history.py', {})
    skills = load(
        ROOT / 'open_webui/models/skills.py',
        {'SkillHistory': history['SkillHistory'], 'SkillHistories': history['SkillHistories']},
    )

    async def model(skill, access_grants=None, db=None):
        return skills['SkillModel'].model_validate(
            {key: getattr(skill, key) for key in skills['SkillModel'].model_fields if key != 'access_grants'}
        )

    skills['Skills']._to_skill_model = model
    return base, history, skills


def test_version_cas_legacy_refresh_and_immutable_restore():
    async def exercise():
        base, history, skills = load_persistence()
        engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as connection:
            await connection.run_sync(base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            table = skills['Skills']
            form = skills['SkillForm'](id='demo', name='Demo', content='original', meta={'unknown': {'keep': True}})
            initial = await table.insert_new_skill('owner', form, db=session)
            updated = await table.update_skill_by_id(
                'demo',
                {'files': [{'path': 'SKILL.md', 'content': 'changed'}], 'expected_version_id': initial.version_id},
                db=session,
            )
            assert updated.version_id != initial.version_id
            assert updated.meta.model_dump()['unknown'] == {'keep': True}
            with pytest.raises(HTTPException) as stale:
                await table.update_skill_by_id(
                    'demo',
                    {'files': [{'path': 'SKILL.md', 'content': 'stale'}], 'expected_version_id': initial.version_id},
                    db=session,
                )
            assert stale.value.status_code == 409
            with pytest.raises(HTTPException) as old:
                await table.update_skill_by_id('demo', {'meta': {'openclaw': {'files': {}}}}, db=session)
            assert old.value.detail['code'] == 'refresh_required'
            from test_skill_compat import legacy

            snapshot = legacy()
            entry = history['SkillHistories'].new_entry('demo', snapshot, 'owner')
            session.add(entry)
            await session.commit()
            restored = await table.update_skill_version(
                'demo', entry.id, updated.version_id, db=session, user_id='writer'
            )
            assert restored.version_id != entry.id
            assert len(restored.data['files']) == 3
            unchanged = await session.get(history['SkillHistory'], entry.id, populate_existing=True)
            assert unchanged.snapshot == snapshot
            converted = await session.get(history['SkillHistory'], restored.version_id)
            assert converted.parent_id == entry.id
            assert converted.user_id == 'writer'
            await session.refresh(converted)
            with pytest.raises(HTTPException) as stale_restore:
                await table.update_skill_version('demo', initial.version_id, updated.version_id, db=session)
            assert stale_restore.value.status_code == 409
        await engine.dispose()

    asyncio.run(exercise())


def test_migration_preserves_old_snapshot_and_creates_compatible_version():
    import importlib.util
    import sqlalchemy as sa
    from test_skill_compat import legacy

    path = ROOT / 'open_webui/migrations/versions/a120d13e0002_unify_openclaw_skill_files.py'
    spec = importlib.util.spec_from_file_location('migration_under_test', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///:memory:')
    metadata = sa.MetaData()
    skill = sa.Table(
        'skill',
        metadata,
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('name', sa.Text),
        sa.Column('description', sa.Text),
        sa.Column('content', sa.Text),
        sa.Column('data', sa.JSON),
        sa.Column('meta', sa.JSON),
        sa.Column('version_id', sa.Text),
        sa.Column('user_id', sa.Text),
        sa.Column('updated_at', sa.BigInteger),
    )
    history = sa.Table(
        'skill_history',
        metadata,
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('skill_id', sa.Text),
        sa.Column('parent_id', sa.Text),
        sa.Column('snapshot', sa.JSON),
        sa.Column('user_id', sa.Text),
        sa.Column('created_at', sa.BigInteger),
        sa.Column('commit_message', sa.Text),
    )
    metadata.create_all(engine)
    snapshot = legacy()
    with engine.begin() as connection:
        connection.execute(
            skill.insert().values(id='demo', version_id='old', user_id='owner', updated_at=1, **snapshot)
        )
        connection.execute(
            history.insert().values(id='old', skill_id='demo', snapshot=snapshot, user_id='owner', created_at=1)
        )
        migration.op = SimpleNamespace(get_bind=lambda: connection)
        migration.upgrade()
        current = connection.execute(sa.select(skill)).mappings().one()
        assert current['version_id'] != 'old'
        assert len(current['data']['files']) == 3
        assert connection.execute(sa.select(history.c.snapshot).where(history.c.id == 'old')).scalar_one() == snapshot
        migration.upgrade()
        assert connection.execute(sa.select(sa.func.count()).select_from(history)).scalar_one() == 2
        # Both dialects compile the portable JSON-backed updates without backend-specific operators.
        from sqlalchemy.dialects import postgresql, sqlite

        statement = skill.update().where(skill.c.id == 'demo').values(data=current['data'])
        assert str(statement.compile(dialect=postgresql.dialect()))
        assert str(statement.compile(dialect=sqlite.dialect()))
        # Preflight conversion validates all skills before it mutates any row.
        pending = legacy()
        invalid = legacy()
        invalid['meta']['openclaw']['files'] = {'../bad': {'content': 'bad'}}
        connection.execute(
            skill.insert().values(id='pending', version_id='p-old', user_id='owner', updated_at=2, **pending)
        )
        connection.execute(
            skill.insert().values(id='invalid', version_id='i-old', user_id='owner', updated_at=2, **invalid)
        )
        with pytest.raises(ValueError, match='invalid'):
            migration.upgrade()
        assert connection.execute(sa.select(skill.c.version_id).where(skill.c.id == 'pending')).scalar_one() == 'p-old'
        assert connection.execute(sa.select(sa.func.count()).select_from(history)).scalar_one() == 2
    engine.dispose()
