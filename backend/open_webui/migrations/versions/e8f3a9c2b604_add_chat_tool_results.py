"""Add independently stored raw tool results and V2 context projections.

Revision ID: e8f3a9c2b604
Revises: c7d2e4f6a801
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'e8f3a9c2b604'
down_revision: str | None = 'c7d2e4f6a801'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_tool_result',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), nullable=False),
        *(
            sa.Column(name, sa.Text(), nullable=False)
            for name in (
                'message_id',
                'output_id',
                'call_id',
                'batch_id',
                'name',
                'kind',
                'text',
                'source_hash',
                'source_fingerprint',
            )
        ),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'chat_id', 'message_id', 'output_id', 'source_hash', 'source_fingerprint', name='uq_chat_tool_result_source'
        ),
    )
    op.create_index('ix_chat_tool_result_chat', 'chat_tool_result', ['chat_id', 'message_id'])
    op.create_table(
        'chat_tool_result_context',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('result_id', sa.Text(), sa.ForeignKey('chat_tool_result.id', ondelete='CASCADE'), nullable=False),
        sa.Column('chat_id', sa.Text(), sa.ForeignKey('chat.id', ondelete='CASCADE'), nullable=False),
        *(sa.Column(name, sa.Text(), nullable=False) for name in ('model_id', 'branch_id', 'task_hash', 'policy_hash')),
        sa.Column('budget', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('original_tokens', sa.Integer(), nullable=False),
        sa.Column('processed_tokens', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            'result_id',
            'model_id',
            'branch_id',
            'task_hash',
            'policy_hash',
            'budget',
            name='uq_chat_tool_result_context_key',
        ),
    )
    op.create_index(
        'ix_chat_tool_result_context_chat', 'chat_tool_result_context', ['chat_id', 'model_id', 'branch_id']
    )


def downgrade() -> None:
    op.drop_index('ix_chat_tool_result_context_chat', table_name='chat_tool_result_context')
    op.drop_table('chat_tool_result_context')
    op.drop_index('ix_chat_tool_result_chat', table_name='chat_tool_result')
    op.drop_table('chat_tool_result')
