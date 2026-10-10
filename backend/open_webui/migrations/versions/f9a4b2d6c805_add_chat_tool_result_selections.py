"""Add immutable admitted tool-result selections without guessing historical choices.

Revision ID: f9a4b2d6c805
Revises: e8f3a9c2b604
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f9a4b2d6c805'
down_revision: str | None = 'e8f3a9c2b604'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_tool_result_selection',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), nullable=False),
        sa.Column('result_id', sa.Text(), sa.ForeignKey('chat_tool_result.id', ondelete='CASCADE'), nullable=True),
        *(
            sa.Column(name, sa.Text(), nullable=False)
            for name in (
                'model_id',
                'branch_anchor_id',
                'state',
                'message_id',
                'output_id',
                'call_id',
                'batch_id',
                'name',
                'kind',
                'source_hash',
                'source_fingerprint',
            )
        ),
        sa.Column(
            'context_id', sa.Text(), sa.ForeignKey('chat_tool_result_context.id', ondelete='CASCADE'), nullable=True
        ),
        sa.Column('method', sa.Text(), nullable=False),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in ('allocation', 'original_tokens', 'processed_tokens')
        ),
        sa.Column('decision_id', sa.Text(), nullable=True),
        sa.Column('recorded_at', sa.BigInteger(), nullable=False),
        sa.Column('batch_report', sa.JSON(), nullable=True),
        sa.UniqueConstraint(
            'chat_id',
            'model_id',
            'branch_anchor_id',
            'message_id',
            'output_id',
            'source_hash',
            'source_fingerprint',
            name='uq_chat_tool_result_selection_anchor',
        ),
    )
    op.create_index(
        'ix_chat_tool_result_selection_chat', 'chat_tool_result_selection', ['chat_id', 'model_id', 'branch_anchor_id']
    )


def downgrade() -> None:
    op.drop_index('ix_chat_tool_result_selection_chat', table_name='chat_tool_result_selection')
    op.drop_table('chat_tool_result_selection')
