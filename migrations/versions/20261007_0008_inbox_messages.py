"""inbox messages

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-07 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('inbox_messages',
    sa.Column('inbox_id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.String(length=250), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sender', sa.String(length=254), nullable=True),
    sa.Column('sender_name', sa.String(length=200), nullable=True),
    sa.Column('subject', sa.String(length=300), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('classified_by', sa.String(length=60), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('attachment_name', sa.String(length=255), nullable=True),
    sa.Column('attachment_path', sa.String(length=500), nullable=True),
    sa.Column('form_answers', sa.JSON(), nullable=True),
    sa.Column('application_id', sa.Integer(), nullable=True),
    sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.application_id'], ),
    sa.PrimaryKeyConstraint('inbox_id'),
    sa.UniqueConstraint('message_id', name='uq_inbox_messages_message_id')
    )
    with op.batch_alter_table('inbox_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_inbox_messages_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('inbox_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_inbox_messages_status'))

    op.drop_table('inbox_messages')
