"""Convert legacy OpenClaw packages without rewriting immutable snapshots."""

import uuid
import sqlalchemy as sa
from alembic import op
from open_webui.utils.skill_compat import canonical_snapshot

revision = 'a120d13e0002'
down_revision = 'a120d13e0001'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    skill = sa.Table('skill', sa.MetaData(), autoload_with=connection)
    history = sa.Table('skill_history', sa.MetaData(), autoload_with=connection)
    conversions = []
    # Validate the full dataset before any writes, including on SQLite.
    for row in connection.execute(sa.select(skill)).mappings():
        original = {key: row[key] for key in ('name', 'description', 'content', 'data', 'meta')}
        converted = canonical_snapshot(row['id'], original)
        if converted != original:
            conversions.append((dict(row), converted))
    for row, snapshot in conversions:
        version_id = str(uuid.uuid4())
        latest = connection.execute(
            sa.select(sa.func.max(history.c.created_at)).where(history.c.skill_id == row['id'])
        ).scalar()
        connection.execute(
            history.insert().values(
                id=version_id,
                skill_id=row['id'],
                parent_id=row['version_id'],
                user_id=row['user_id'],
                commit_message='Convert legacy OpenClaw package',
                created_at=max(row['updated_at'] or 0, (latest or 0) + 1),
                snapshot=snapshot,
            )
        )
        connection.execute(
            skill.update()
            .where(skill.c.id == row['id'])
            .values(
                **snapshot,
                version_id=version_id,
            )
        )


def downgrade():
    # No destructive reverse conversion: rollback uses the matching database backup.
    pass
