"""Record whether reusable context was derived from saved shared history.

Revision ID: a120d13e0003
Revises: a120d13e0002
"""

from alembic import op
import sqlalchemy as sa

revision = 'a120d13e0003'
down_revision = 'a120d13e0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_context_checkpoint', sa.Column('source_scope', sa.Text(), nullable=True))
    op.add_column('chat_tool_result_context', sa.Column('source_scope', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('chat_tool_result_context', 'source_scope')
    op.drop_column('chat_context_checkpoint', 'source_scope')
