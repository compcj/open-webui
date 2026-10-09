"""Add independent durable V2 chat context checkpoints.

Revision ID: c7d2e4f6a801
Revises: d4c1a8e37b62
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c7d2e4f6a801'
down_revision: str | None = 'd4c1a8e37b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_context_checkpoint',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), nullable=False),
        sa.Column('model_id', sa.Text(), nullable=False),
        sa.Column('branch_anchor_id', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('source_refs', sa.JSON(), nullable=False),
        sa.Column('source_fingerprint', sa.Text(), nullable=False),
        sa.Column('covered_message_keys', sa.JSON(), nullable=False),
        sa.Column('covered_output_ids', sa.JSON(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint('chat_id', 'model_id', 'version', name='uq_chat_context_checkpoint_version'),
    )
    op.create_index('ix_chat_context_checkpoint_lookup', 'chat_context_checkpoint', ['chat_id', 'model_id', 'version'])


def downgrade() -> None:
    op.drop_index('ix_chat_context_checkpoint_lookup', table_name='chat_context_checkpoint')
    op.drop_table('chat_context_checkpoint')
