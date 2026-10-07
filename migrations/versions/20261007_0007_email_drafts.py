"""email drafts

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07 11:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('email_drafts',
    sa.Column('draft_id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=True),
    sa.Column('to_address', sa.String(length=254), nullable=True),
    sa.Column('subject', sa.String(length=250), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('drafted_by', sa.String(length=10), nullable=False),
    sa.Column('approved_by', sa.String(length=80), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('redirected', sa.Boolean(), nullable=False),
    sa.Column('error', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.application_id'], ),
    sa.ForeignKeyConstraint(['decision_id'], ['hr_decisions.decision_id'], ),
    sa.PrimaryKeyConstraint('draft_id')
    )
    with op.batch_alter_table('email_drafts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_drafts_application_id'), ['application_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('email_drafts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_drafts_application_id'))

    op.drop_table('email_drafts')
